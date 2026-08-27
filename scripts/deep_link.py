# -*- coding: utf-8 -*-
"""搜索深链定位 —— 来源必须落到「实际地址」。

tech_signals → docs 具体子页;user_feedback → G2/Trustpilot/HN/Reddit 具体帖;
pricing → 官方定价页(全候选 404 时的搜索发现)。

搜索通道:DuckDuckGo HTML 端点(无 key、纯 GET、本地跑);解析只取
结果链接,不依赖第三方解析库。深链 URL 必须爬出真实内容且包含
关键词才可作为 source —— 杜绝"看起来像的 URL"当出处。
"""

import base64
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


def _bing_unwrap(url: str) -> str:
    """Bing 结果链接是 bing.com/ck/a?...&u=a1<base64> 跳板,解出真实 URL。"""
    if "bing.com/ck/" not in url:
        return url
    q = parse_qs(urlparse(url).query).get("u")
    if q and q[0].startswith("a1"):
        try:
            pad = q[0][2:] + "=" * (-len(q[0][2:]) % 4)
            dec = base64.b64decode(pad).decode("utf-8", "ignore")
            if dec.startswith("http"):
                return dec
        except Exception:
            pass
    return url


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
    """爬 URL 并验证内容含关键词 —— 深链必须爬出真实内容才能当 source。"""
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
    return {
        "url": url,
        "markdown": md,
        "engine": (r.get("stats") or {}).get("primary_scraper", ""),
        "fetched_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
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


def locate_pricing_page(domain: str, timeout: int = 30) -> Optional[Dict]:
    """官方定价页发现(全部候选 URL 404/反爬时)。"""
    for q in (
        f"site:{domain} pricing",
        f"{domain} pricing plans",
    ):
        for hit in search_web(q, n=4):
            host = urlparse(hit["url"]).netloc.lower().replace("www.", "")
            if domain.lower().replace("www.", "") not in host:
                continue
            r = _scrape_and_verify(hit["url"], "", timeout)
            if r:
                return r
    return None
