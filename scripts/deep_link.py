# -*- coding: utf-8 -*-
"""搜索深链定位 —— 来源必须落到「实际地址」。

tech_signals → docs 具体子页;user_feedback → G2/Trustpilot/HN/Reddit 具体帖;
pricing → 官方定价页(全候选 404 时的搜索发现)。

搜索通道:DuckDuckGo HTML 端点(无 key、纯 GET、本地跑);解析只取
结果链接,不依赖第三方解析库。深链 URL 必须爬出真实内容且包含
关键词才可作为 source —— 杜绝"看起来像的 URL"当出处。
"""

import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import quote_plus, urlparse, parse_qs, unquote

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters import scrape_smart  # noqa: E402

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_SEARCH_CACHE: Dict[str, List[Dict[str, str]]] = {}

# 2026-08-30 清理:删除 _bing_unwrap(Bing 跳板解包)—— 搜索通道只有
# Jina Reader(DDG lite)与 DDG html POST 两条,全库零调用方(grep 证实)。


def _ddg_redirect_url(href: str) -> str:
    """DDG 结果链接是 //duckduckgo.com/l/?uddg=<encoded> 跳板,解出真实 URL。"""
    if href.startswith("//"):
        href = "https:" + href
    if "duckduckgo.com/l/" in href or "uddg=" in href:
        q = parse_qs(urlparse(href).query).get("uddg")
        if q:
            return unquote(q[0])
    return href


def _search_via_jina(query: str, n: int) -> List[Dict[str, str]]:
    """主通道:Jina Reader(免 key)渲染 DDG lite 搜索页 → markdown 结果。

    本机直连 DDG 会被 202 质询,但 r.jina.ai 的渲染集群能拿到完整结果,
    且 DDG 严格支持 site: 过滤。结果链接是 uddg 跳板,解码后使用。"""
    import requests

    url = "https://r.jina.ai/https://lite.duckduckgo.com/lite/?q=" + quote_plus(query)
    try:
        # 短 UA:完整 Chrome UA + requests TLS 指纹会被 jina 质询(实测
        # 403),短 UA 反而稳定 —— ponytail: 以实测为准,不猜
        resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=45)
        if resp.status_code != 200:
            return []
        results, seen = [], set()
        for m in re.finditer(r"\[([^\]\[]{3,150})\]\((https?://[^)]+)\)", resp.text):
            title = re.sub(r"[*#`]", "", m.group(1)).strip()
            link = _ddg_redirect_url(m.group(2))
            if "duckduckgo.com" in link or "bing.com" in link:
                continue
            if not link.startswith("http") or link in seen or not title:
                continue
            seen.add(link)
            results.append({"title": title[:120], "url": link})
            if len(results) >= n:
                break
        return results
    except Exception:
        return []


def _search_via_ddg(query: str, n: int) -> List[Dict[str, str]]:
    """主通道:DuckDuckGo html 端点 —— 必须用 POST(GET 会被 202 质询,
    POST 带 Referer 实测通过),支持 site: 过滤。"""
    import requests

    results: List[Dict[str, str]] = []
    seen = set()
    try:
        resp = requests.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={"User-Agent": _UA, "Referer": "https://html.duckduckgo.com/"},
            timeout=20,
        )
        for m in re.finditer(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
            resp.text,
            re.S,
        ):
            url = _ddg_redirect_url(m.group(1))
            title = re.sub(r"<[^>]+>", "", m.group(2)).strip()
            if not url.startswith("http") or "duckduckgo.com" in url:
                continue
            if url in seen or not title:
                continue
            seen.add(url)
            results.append({"title": title[:120], "url": url})
            if len(results) >= n:
                break
    except Exception:
        pass
    return results[:n]


def search_web(query: str, n: int = 6) -> List[Dict[str, str]]:
    """网页搜索:Jina+DDG lite(支持 site:)→ DDG 直连 POST 兜底。

    免费通道都限流:失败时单次退避重试(jina 免费档 20 rpm,
    真实运行里搜索被 30s+ 的爬取自然隔开,重试只兜瞬时抖动)。"""
    if query in _SEARCH_CACHE:
        return _SEARCH_CACHE[query][:n]
    results = _search_via_jina(query, n)
    if not results:
        time.sleep(3)
        results = _search_via_ddg(query, n)
    if not results:  # 二次抖动兜底:换通道再试一轮
        time.sleep(5)
        results = _search_via_jina(query, n) or _search_via_ddg(query, n)
    _SEARCH_CACHE[query] = results
    return results[:n]


def _scrape_and_verify(url: str, keyword: str, timeout: int = 30) -> Optional[Dict]:
    """爬 URL 并验证内容含关键词 —— 深链必须爬出真实内容才能当 source。

    2026-08-30 修复:透传 all_results(各引擎原文)。scrape_smart 内部本来就
    按 URL 类型跑了多引擎组合,历史实现只保留单引擎标签 + 合并文本 →
    fetch 四级回退第三级拿到的天然是"单引擎",定价交叉验证(≥2 独立引擎)
    注定凑不齐。各引擎原文传回后,该级回退恢复交叉验证能力。"""
    try:
        r = scrape_smart(url, max_chars=30000, timeout=timeout)
    except Exception:
        return None
    md = r.get("markdown", "") if r.get("success") else ""
    if not md or len(md) < 300 or _looks_like_shell(md):
        return None
    if keyword:
        kw = keyword.lower().split("(")[0].strip()
        if kw and kw not in md.lower():
            return None
    all_results = [
        {
            "success": True,
            "scraper": x.get("scraper"),
            "markdown": x.get("markdown") or "",
        }
        for x in (r.get("all_results") or [])
        if x.get("success") and x.get("markdown")
    ]
    return {
        "url": url,
        "markdown": md,
        "engine": (r.get("stats") or {}).get("primary_scraper", ""),
        "fetched_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "all_results": all_results,
    }


def _looks_like_shell(md: str) -> bool:
    low = md.lower()
    return (
        len(md.strip()) < 300
        or "enable javascript" in low[:2000]
        or low.count("<script") > 5
    )


# ── 三类深链定位 ──


def locate_tech(domain: str, keyword: str, timeout: int = 30) -> Optional[Dict]:
    """技术信号 → 官方 docs 具体子页(非栏目首页)。"""
    kw = keyword.split("(")[0].strip()
    for q in (
        f"site:docs.{domain} {kw}",
        f"site:{domain} docs {kw}",
        f"{domain} documentation {kw}",
    ):
        for hit in search_web(q, n=4):
            host = urlparse(hit["url"]).netloc.lower().replace("www.", "")
            if domain.lower().replace("www.", "") not in host:
                continue  # 技术证据只认官方域
            r = _scrape_and_verify(hit["url"], kw, timeout)
            if r:
                return r
    return None


_FEEDBACK_SITES = ["g2.com", "trustpilot.com", "news.ycombinator.com", "reddit.com"]


def _reddit_feedback(product: str, timeout: int = 30) -> Optional[Dict]:
    """Reddit JSON 通道(old.reddit 对浏览器 UA 开放,免 key)。

    搜索产品讨论帖 → 取最高分帖的评论页 → 拼成 markdown 供引语提取。
    G2/Trustpilot 反爬全封时的可靠社区反馈源。"""
    import requests

    for _attempt in range(2):
        try:
            s = requests.get(
                "https://old.reddit.com/search.json",
                params={"q": product, "limit": 8, "sort": "relevance"},
                headers={"User-Agent": _UA},
                timeout=20,
            )
            if s.status_code != 200 or not s.text.lstrip().startswith("{"):
                time.sleep(3)  # reddit 间歇性反爬,退避重试一次
                continue
            posts = s.json().get("data", {}).get("children", [])
            permalink = next(
                (
                    p["data"]["permalink"]
                    for p in posts
                    if p.get("data", {}).get("num_comments", 0) >= 2
                    and product.split()[0].lower() in p["data"].get("title", "").lower()
                ),
                None,
            )
            if not permalink:
                return None
            c = requests.get(
                "https://old.reddit.com" + permalink.rstrip("/") + ".json",
                headers={"User-Agent": _UA},
                timeout=20,
            )
            if c.status_code != 200 or not c.text.lstrip().startswith("["):
                time.sleep(3)
                continue
            blobs = c.json()
            lines = []
            try:
                post = blobs[0]["data"]["children"][0]["data"]
                lines.append(
                    f"# {post.get('title', '')} (r/{post.get('subreddit', '')}, ↑{post.get('ups', 0)})"
                )
            except Exception:
                pass

            def _walk(children):
                for ch in children:
                    d = ch.get("data", {})
                    body = d.get("body")
                    if body and len(body) > 60:
                        lines.append(body[:600])
                    replies = d.get("replies")
                    if isinstance(replies, dict):
                        _walk(replies.get("data", {}).get("children", []))

            if len(blobs) > 1:
                _walk(blobs[1]["data"]["children"])
            md = "\n\n".join(lines)
            if len(md) < 300:
                return None
            return {
                "url": "https://www.reddit.com" + permalink,
                "markdown": md,
                "engine": "reddit-json",
                "fetched_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
            }
        except Exception:
            time.sleep(3)
    return None


def _g2_direct_feedback(product: str, timeout: int = 30) -> Optional[Dict]:
    """直连 G2 的 AI 镜像域(ai.g2.com/product/<slug>,反爬比主站松)。

    免搜索一步到位;slug 常见变体都试。内容必须含产品名 + 足量正文
    (评论引语由上层 _extract_user_feedback 提取)。"""
    base = re.sub(r"[^\w]+", "-", (product or "").strip().lower()).strip("-")
    slugs = list(
        dict.fromkeys(
            [
                base,
                base.replace("-io", ""),
                (product or "").split(".")[0].strip().lower(),
            ]
        )
    )
    for slug in slugs:
        if not slug or len(slug) < 3:
            continue
        url = f"https://ai.g2.com/product/{slug}"
        r = _scrape_and_verify(url, (product or "").split()[0], timeout)
        if r and len(r["markdown"]) > 1500:
            return r
    return None


def locate_feedback(product: str, timeout: int = 30) -> Optional[Dict]:
    """用户反馈 → 具体评论/帖子页(ai.g2.com 直连 > site: 搜索 > Reddit)。"""
    prod = re.sub(r"[^\w\s.-]", "", product).strip() or product
    # 1) ai.g2.com 直连(免搜索、命中率高)
    r = _g2_direct_feedback(prod, timeout)
    if r:
        return r
    # 2) G2 / Trustpilot site: 搜索(常被封)
    queries = [f"site:{site} {prod} review" for site in _FEEDBACK_SITES[:2]]
    for q in queries:
        for hit in search_web(q, n=4):
            host = urlparse(hit["url"]).netloc.lower()
            site = next((s for s in _FEEDBACK_SITES if s in host), "")
            if not site:
                continue
            path = urlparse(hit["url"]).path.lower()
            if site == "g2.com" and not re.search(r"/products?/|#reviews", hit["url"]):
                continue
            if site == "trustpilot.com" and "/review/" not in path:
                continue
            r = _scrape_and_verify(hit["url"], prod.split()[0] if prod else "", timeout)
            if r:
                return r
    # 3) Reddit JSON 兜底(可靠)
    return _reddit_feedback(prod, timeout)


def locate_pricing_page(
    domain: str, timeout: int = 30, budget_s: Optional[float] = None
) -> Optional[Dict]:
    """官方定价页发现(全部候选 URL 404/反爬时)。

    budget_s(2026-08-30):总预算秒数。并发组合实测事故:3 竞品并发时
    资源竞争导致某定价页全引擎失败 → deep_link 被触发,而 2 查询 ×
    4 候选 × 每次 scrape_smart 整体上限 45s 的最坏路径 ≈ 360s,全部
    落在 fetch 的 150s 竞品预算之外(deadline 只在步骤间检查,不约束
    步内执行)→ 整个运行 >500s 超时。预算耗尽诚实返回 None。
    """
    deadline = (time.monotonic() + budget_s) if budget_s else None
    for q in (
        f"site:{domain} pricing",
        f"{domain} pricing plans",
    ):
        if deadline and time.monotonic() > deadline:
            return None
        for hit in search_web(q, n=4):
            if deadline and time.monotonic() > deadline:
                return None
            host = urlparse(hit["url"]).netloc.lower().replace("www.", "")
            if domain.lower().replace("www.", "") not in host:
                continue
            r = _scrape_and_verify(hit["url"], "", timeout)
            if r:
                return r
    return None


# ============================================================
# CLI 入口 —— SKILL.md Step 2.5 补爬工作流的可执行化
# (2026-08-30:SKILL.md:41-42 一直指示"用 deep_link 定位 tech/feedback
# 具体页",但本模块没有 CLI,LLM 只能 python -c 拼函数调用)
# ============================================================
if __name__ == "__main__":
    import argparse
    import json as _json

    ap = argparse.ArgumentParser(
        description="deep_link · 搜索深链定位(Step 2.5 补爬工具,免 key)"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_tech = sub.add_parser("tech", help="技术信号 → 官方 docs 具体子页")
    p_tech.add_argument("domain", help="竞品域名,如 wati.io")
    p_tech.add_argument("keyword", help="技术关键词,如 'WhatsApp Cloud API'")
    p_fb = sub.add_parser("feedback", help="用户反馈 → G2/Trustpilot/Reddit 具体页")
    p_fb.add_argument("product", help="产品名,如 WATI")
    p_pr = sub.add_parser("pricing", help="官方定价页发现(候选全灭时)")
    p_pr.add_argument("domain", help="竞品域名")
    a = ap.parse_args()

    if a.cmd == "tech":
        out = locate_tech(a.domain, a.keyword)
    elif a.cmd == "feedback":
        out = locate_feedback(a.product)
    else:
        out = locate_pricing_page(a.domain)
    print(_json.dumps(out, ensure_ascii=False, indent=1) if out else "null")
