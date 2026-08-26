# -*- coding: utf-8 -*-
"""
/youzi skill · 爬虫 adapter 集合

设计理念：并行 + 智能合并

每个 adapter 实现统一的 scrape(url, **kwargs) → dict 接口。
统一入口 scrape_smart() 自动并行调用 13 个爬虫 + 合并去重结果。

支持的爬虫（按优先级）：

  🔒 商业（需 API key）：
    1. firecrawl         —— 96% 网页覆盖 + JS 重度 + 截图（业界最强）
    2. jina              —— 轻量 URL→Markdown（无 key 20 req/min）

  🆓 开源 Python 库（本地运行）：
    3. crawl4ai          —— LLM-ready markdown，开源 firecrawl 替代
    4. crawlee           —— Apify 出品，现代爬虫框架，自带反爬（指纹/UA/代理）
    5. trafilatura       —— 学术界标准 web 正文抽取
    6. newspaper3k       —— 老牌新闻/文章抽取（带 NLP）
    7. readability-lxml  —— Mozilla Readability 算法移植
    8. markdownify       —— 通用 HTML→MD fallback
    9. html2text         —— HTML→纯文本 fallback
    10. playwright       —— 浏览器自动化（登录/JS 重度）
    11. camoufox         —— Firefox 隐身浏览器，反 Cloudflare/反指纹
    12. scrapy           —— Python 工业级爬虫框架（整站爬取）
    13. requests_html    —— requests + 轻量 JS 渲染
"""

import asyncio
import hashlib
import json
from typing import Dict, Any, Optional, List


def url_hash(url: str) -> str:
    """生成 URL 的短 hash（用于截图文件名 + dedup）。"""
    return hashlib.md5(url.encode()).hexdigest()[:8]


# ============================================================
# 智能 URL 路由 —— 按 URL 类型 + 特征自动选择最佳爬虫组合
# ============================================================
import re as _re_route  # noqa: E402

# URL 类型识别模式(按优先级匹配)
_URL_TYPE_PATTERNS = [
    # 文档站(技术文档,需要深读,firecrawl/crawl4ai 最强)
    ("docs", _re_route.compile(r"^https?://docs?\.", _re_route.I)),
    ("docs", _re_route.compile(r"/docs?/", _re_route.I)),
    ("docs", _re_route.compile(r"/reference/", _re_route.I)),
    ("docs", _re_route.compile(r"/api[-_]?(docs|reference)", _re_route.I)),
    # Dashboard / App (登录后内容,必须 playwright)
    ("dashboard", _re_route.compile(r"^https?://app\.", _re_route.I)),
    ("dashboard", _re_route.compile(r"^https?://(console|panel|admin)\.", _re_route.I)),
    # 定价页 (JS 重,firecrawl/playwright 强)
    ("pricing", _re_route.compile(r"/pricing[-_]?(plan|plans|tier|tiers)?$", _re_route.I)),
    ("pricing", _re_route.compile(r"/plan[-_]?s?$", _re_route.I)),
    ("pricing", _re_route.compile(r"/price", _re_route.I)),
    # 公司信息
    ("about", _re_route.compile(r"/(about|company|our[-_]?story|team|leadership|careers)", _re_route.I)),
    # 集成 / 合作伙伴
    ("integration", _re_route.compile(r"/integrations?(?:/|$)", _re_route.I)),
    ("integration", _re_route.compile(r"/marketplace(?:/|$)", _re_route.I)),
    # 客户案例
    ("customer", _re_route.compile(r"/(customer[-_]?stor(y|ies)|case[-_]?stud(y|ies))", _re_route.I)),
    # 博客 / 文章
    ("blog", _re_route.compile(r"/(blog|news|article|press|release)", _re_route.I)),
    # 产品 / 功能页
    ("feature", _re_route.compile(r"/(feature|features|product|capabilities|whatsapp[-_]?business[-_]?api)", _re_route.I)),
    # changelog
    ("changelog", _re_route.compile(r"/(changelog|release[-_]?notes?|updates?)", _re_route.I)),
]

# 各 URL 类型推荐的爬虫组合(按优先级,前几个 = 主力,后面 = 补充)。
# 设计原则:
#   - pricing: JS 渲染组(价格几乎都是前端渲染) + 1 个静态引擎做交叉对照
#     —— 双通道独立取证,是定价可信度判定的基础(≥2 独立引擎一致才 verified)
#   - docs/feature: 内容型页面,firecrawl/crawl4ai 主力
#   - about/blog/customer: 静态文章型,轻量正文抽取器就够(省资源、快)
_URL_TYPE_SCRAPERS = {
    # 组合按 storage/engine-stats.json 的真实历史质量校准(2026-08-26,
    # 214+ 次真实爬取):playwright 在 JS 页质量最高(pricing q=0.50)、
    # trafilatura 文档型最强(docs q=0.77)、markdownify/readability 在
    # about 上成功率仅 44-51% 已替换。
    "docs":        ["trafilatura", "firecrawl", "crawl4ai"],        # 文档站:trafilatura q=0.77 王
    "dashboard":   ["playwright", "camoufox"],                       # dashboard:必须浏览器
    "pricing":     ["firecrawl", "playwright", "crawl4ai", "trafilatura"],  # 定价:JS 组 + 静态对照
    "about":       ["firecrawl", "trafilatura", "readability"],     # about:公司信息页要 JS 渲染,firecrawl 主力
    "integration": ["firecrawl", "trafilatura", "readability"],     # 集成页
    "customer":    ["trafilatura", "firecrawl", "newspaper3k"],      # 客户案例:文章型 + firecrawl 兜底
    "blog":        ["trafilatura", "firecrawl", "newspaper3k"],     # 博客:文章型
    "feature":     ["firecrawl", "playwright", "crawl4ai"],         # 功能页:JS 展示页,playwright 进组(trafilatura 在 feature ok 仅 39% 已移出)
    "changelog":   ["firecrawl", "trafilatura", "readability"],     # changelog
    "homepage":    ["firecrawl", "playwright", "crawl4ai", "trafilatura"],  # 首页:功能区块 JS 渲染,playwright 补全 DOM(trafilatura 保留:tagline frontmatter)
}

# 定价类页面:证据语义强、顺序敏感,禁止跨引擎合并补充段落(防价格污染)
_NO_SUPPLEMENT_TYPES = {"pricing"}

# 引擎历史统计文件(成功率 + 质量分,按 url_type 分桶) —— 智能路由的学习数据
_ENGINE_STATS_PATH = __import__("pathlib").Path(__file__).resolve().parent.parent / "storage" / "engine-stats.json"


def _load_engine_stats() -> Dict[str, Any]:
    try:
        return json.loads(_ENGINE_STATS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_engine_stats(stats: Dict[str, Any]) -> None:
    try:
        _ENGINE_STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
        _ENGINE_STATS_PATH.write_text(json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8")
    except Exception:
        pass  # 统计写失败不影响爬取


def record_engine_outcome(url_type: str, outcomes: Dict[str, Any]) -> None:
    """记录一轮爬取中各引擎的表现,供 recommend_scrapers 学习排序。

    outcomes: {engine_name: {"success": bool, "quality": float 0-1}}
    """
    stats = _load_engine_stats()
    for eng, oc in outcomes.items():
        bucket = stats.setdefault(eng, {}).setdefault(url_type, {"n": 0, "ok": 0, "q_sum": 0.0})
        bucket["n"] += 1
        if oc.get("success"):
            bucket["ok"] += 1
        bucket["q_sum"] += float(oc.get("quality") or 0.0)
    _save_engine_stats(stats)


def _engine_stats_score(eng: str, url_type: str) -> Optional[float]:
    """该引擎在该 URL 类型上的历史平均质量(0-1);无历史返回 None。"""
    b = _load_engine_stats().get(eng, {}).get(url_type)
    if not b or not b.get("n"):
        return None
    ok_rate = b["ok"] / b["n"]
    avg_q = b["q_sum"] / b["n"]
    return ok_rate * 0.6 + avg_q * 0.4


def classify_url(url: str) -> str:
    """根据 URL 模式识别页面类型。

    先去掉尾斜杠/query/fragment 再匹配 —— "/pricing/" 尾斜杠会破坏
    模式的 $ 锚点,导致定价页被误判为 homepage(路由不走定价引擎组,
    真实事故:WATI /pricing/ 拿到的是 homepage 组合)。
    """
    url_clean = url.lower().split("?")[0].split("#")[0].rstrip("/")
    for url_type, pattern in _URL_TYPE_PATTERNS:
        if pattern.search(url_clean):
            return url_type
    return "homepage"


def recommend_scrapers(url: str, need_login: bool = False) -> List[str]:
    """根据 URL 类型 + 引擎历史表现返回最合适的爬虫组合。

    智能路由 = 静态规则(该类型页面用什么引擎) + 动态排序(该引擎在该类型
    页面上的历史成功率/质量)。历史数据不足(<3 次)的引擎保持静态位次 ——
    避免一两次偶发失败把新引擎永久踢出局。

    Args:
        url: 目标 URL
        need_login: 是否需要登录(强制用 playwright/camoufox)

    Returns:
        爬虫名称列表(有序,前几个 = 主力)
    """
    if need_login:
        return ["playwright", "camoufox"]
    url_type = classify_url(url)
    base = list(_URL_TYPE_SCRAPERS.get(url_type, _URL_TYPE_SCRAPERS["homepage"]))
    scored = []
    for i, eng in enumerate(base):
        hist = _engine_stats_score(eng, url_type)
        # 历史分为主(权重 0.7),静态位次为辅(0.3);无历史 = 中性 0.5
        dynamic = hist if hist is not None else 0.5
        static = 1.0 - i * 0.15
        scored.append((dynamic * 0.7 + static * 0.3, i, eng))
    scored.sort(reverse=True)
    return [eng for _, _, eng in scored]


# ============================================================
# Adapter 注册表 —— 新增爬虫只需在这里加一行
# ============================================================
def _build_adapter_registry():
    """懒加载：每个 adapter 在第一次访问时才 import（避免硬依赖）"""
    from adapters import (
        firecrawl_scraper,
        crawl4ai_scraper,
        trafilatura_scraper,
        newspaper3k_scraper,
        readability_scraper,
        markdownify_scraper,
        playwright_scraper,
        # 第二批（v1.1 新增 4 个主流爬虫）
        scrapy_scraper,
        jina_scraper,
        html2text_scraper,
        requests_html_scraper,
        # 第三批（v1.2 新增 2 个反爬爬虫）
        crawlee_scraper,    # 现代爬虫框架 + 反爬（Apify 出品）
        camoufox_scraper,   # Firefox 隐身浏览器 + 反 Cloudflare
    )

    return {
        # (scraper_id, module, supports_screenshot, supports_login, supports_prompt)
        "firecrawl": (firecrawl_scraper, True, False, False),
        "crawl4ai": (crawl4ai_scraper, False, False, True),
        "trafilatura": (trafilatura_scraper, False, False, False),
        "newspaper3k": (newspaper3k_scraper, False, False, False),
        "readability": (readability_scraper, False, False, False),
        "markdownify": (markdownify_scraper, False, False, False),
        "playwright": (playwright_scraper, True, True, True),
        "scrapy": (scrapy_scraper, False, False, False),
        "jina": (jina_scraper, False, False, False),
        "html2text": (html2text_scraper, False, False, False),
        "requests_html": (requests_html_scraper, False, False, False),
        "crawlee": (crawlee_scraper, False, False, False),
        "camoufox": (camoufox_scraper, True, True, False),
    }


# ============================================================
# 核心：并行爬取 + 智能合并
# ============================================================
async def _scrape_one(name: str, scrape_fn, url: str, **kwargs) -> Dict[str, Any]:
    """包装单个 scraper 调用，捕获异常。

    sync scraper 在线程池中跑（避免阻塞 event loop）；
    async scraper 直接 await。
    """
    import inspect

    try:
        if inspect.iscoroutinefunction(scrape_fn):
            result = await scrape_fn(url, **kwargs)
        else:
            # 同步 scraper 在默认 executor 跑（线程池）
            result = await asyncio.to_thread(scrape_fn, url, **kwargs)
        result["scraper"] = name
        return result
    except Exception as e:
        return {
            "success": False,
            "scraper": name,
            "error": f"{name} 异常: {type(e).__name__}: {e}",
            "markdown": "",
            "html": "",
            "text": "",
            "screenshot": None,
            "extracted": None,
        }


async def _scrape_parallel(
    url: str,
    prompt: Optional[str] = None,
    max_chars: int = 50000,
    need_screenshot: bool = False,
    need_login: bool = False,
    timeout: float = 60.0,
    enabled_scrapers: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """并行调用所有启用的 scraper。

    Args:
        url: 目标 URL
        prompt: LLM 提取提示
        max_chars: markdown 最大长度
        need_screenshot: 是否需要截图
        need_login: 是否需要登录（强制用 Playwright）
        timeout: 单个 scraper 超时（秒）
        enabled_scrapers: 指定启用的 scraper（如 ['firecrawl', 'crawl4ai']）
                         None 表示全部（按注册表顺序）
    """
    registry = _build_adapter_registry()
    enabled = enabled_scrapers or list(registry.keys())

    tasks = []
    for name in enabled:
        if name not in registry:
            continue
        module, supports_screenshot, supports_login, supports_prompt = registry[name]

        if not module.is_available():
            continue
        # need_login 强制走 playwright
        if need_login and not supports_login:
            continue

        kwargs = {}
        # max_chars / prompt —— 仅对接受的 scraper 传
        if name not in ("playwright",):
            kwargs["max_chars"] = max_chars
        if (
            supports_prompt
            and prompt
            and name
            in (
                "crawl4ai",
                "playwright",
            )
        ):
            kwargs["prompt"] = prompt
        if supports_screenshot and need_screenshot:
            if name == "playwright":
                kwargs["screenshot_path"] = f"/tmp/youzi_{url_hash(url)}.png"
            else:
                kwargs["screenshot"] = True
        if supports_login:
            kwargs["extract_prompt"] = prompt
            kwargs["timeout"] = int(timeout * 1000)

        tasks.append(_scrape_one(name, module.scrape, url, **kwargs))

    if not tasks:
        return [
            {
                "success": False,
                "scraper": "none",
                "error": "no scraper available (check install)",
                "markdown": "",
                "html": "",
                "text": "",
                "screenshot": None,
                "extracted": None,
            }
        ]

    # 并行执行（带超时）
    results = await asyncio.gather(*tasks, return_exceptions=True)

    final = []
    for r in results:
        if isinstance(r, Exception):
            final.append(
                {
                    "success": False,
                    "scraper": "unknown",
                    "error": str(r),
                    "markdown": "",
                    "html": "",
                    "text": "",
                    "screenshot": None,
                    "extracted": None,
                }
            )
        else:
            final.append(r)
    return final


# 引擎质量优先级(数字越小越可信):商业 API > 专用正文抽取 > 通用 fallback
# primary 按此顺序选,长度只做 tie-break —— "最长"经常是 nav/JS 垃圾最多的那份
_ENGINE_QUALITY = {
    "firecrawl": 0, "crawl4ai": 1, "jina": 2, "trafilatura": 3,
    "readability": 4, "newspaper3k": 5, "scrapy": 6, "camoufox": 7,
    "playwright": 8, "crawlee": 9, "markdownify": 10, "html2text": 11,
    "requests_html": 12,
}

_CODE_JUNK_RX = __import__("re").compile(
    r"(function\s*\(|var\s+\w+\s*=|jQuery|document\.|window\.|=>\s*\{"
    r"|\.css-|!important|@media|<script|\\n\s*[{}]|\"@context\"|\{\"@"
    r"|--[\w-]+\s*:|gradient\(|animation\s*:|:root|@import|\.woff2?"
    r"|#[0-9a-fA-F]{3,8}\s*[;}]|self\.__next_f|partytown|pointer-events"
    r"|border-radius|radial-gradient|position\s*:\s*absolute)"
)
# markdown 链接 [text](url) —— 用于计算链接密度
_LINK_RX = __import__("re").compile(r"\[([^\]]*)\]\([^)]+\)")


def _md_quality(md: str) -> float:
    """粗估 markdown 质量:代码垃圾/链接密度(导航菜单)越低越好,结构适度加分。"""
    if not md:
        return 0.0
    lines = [ln for ln in md.split("\n") if ln.strip()]
    if not lines:
        return 0.0
    junk = sum(1 for ln in lines if _CODE_JUNK_RX.search(ln))
    junk_ratio = junk / len(lines)
    structured = sum(
        1 for ln in lines if ln.lstrip().startswith(("#", "- ", "* ", "## "))
    )
    structure_ratio = structured / len(lines)
    # 链接密度:正文里链接字符占比。nav/footer 菜单 >60% 都是链接,定价正文 <25%
    total_chars = max(len(md), 1)
    link_chars = sum(len(m.group(0)) for m in _LINK_RX.finditer(md))
    link_density = link_chars / total_chars
    # 纯文本长度奖励(对数,防长垃圾)
    import math
    length_bonus = min(math.log10(max(len(md), 10)) / 5, 1.0)
    base = max(0.0, 1.0 - junk_ratio * 1.5) * 0.5 + structure_ratio * 0.3 + length_bonus * 0.2
    return base * max(0.05, 1.0 - link_density * 1.2)


def _norm_para(p: str) -> str:
    """段落归一化(去空白/大小写)用于跨引擎去重。"""
    return " ".join(p.lower().split())


def _merge_results(
    results: List[Dict[str, Any]], max_chars: int = 50000,
    allow_supplements: bool = True,
) -> Dict[str, Any]:
    """智能合并多个 scraper 的结果。

    策略(顺序敏感 —— 乱序会破坏 "套餐名 ↔ 价格" 的对应关系):
    - primary:按引擎质量优先级选,垃圾占比高的一票否决;长度只做 tie-break
    - primary markdown 原序保留为文档主体
    - 其他引擎只追加 primary 没有的补充段落(归一化去重,保留各引擎内部顺序)
    - allow_supplements=False(pricing 等证据敏感页):禁止跨引擎拼正文 ——
      价格/套餐名来自不同引擎的碎片拼在一起 = 张冠李戴的温床。每个引擎
      的完整原文保留在 all_results 里供上层逐引擎取证。
    - 截图:优先 firecrawl > playwright > 其他
    """
    success = [r for r in results if r.get("success") and r.get("markdown")]
    failed = [r for r in results if not r.get("success")]

    if not success:
        return {
            "success": False,
            "scraper": "none",
            "markdown": "",
            "html": "",
            "text": "",
            "screenshot": None,
            "extracted": None,
            "error": "; ".join(
                f"[{r['scraper']}] {r.get('error', '?')}"
                for r in results
                if not r.get("success")
            )
            or "all failed",
            "all_results": results,
        }

    def primary_key(r):
        q = _md_quality(r.get("markdown", ""))
        # 垃圾占比过高(quality < 0.3)的引擎没有资格当 primary。
        # 注意用 max() 选 key:rank 取负 —— _ENGINE_QUALITY 越小越可信,
        # 取负后 max() 才会优先 firecrawl 等高可信引擎(历史 bug:未取负时
        # 反而选出"质量达标里最不可信"的引擎当 primary)。
        return (q >= 0.3, -_ENGINE_QUALITY.get(r["scraper"], 99), q, len(r.get("markdown", "")))

    primary = max(success, key=primary_key)
    used_scrapers = [r["scraper"] for r in success]

    # 主体 = primary 原序;补充 = 其他引擎的独有段落(按质量排序追加)
    primary_md = primary.get("markdown", "") or ""
    seen = {_norm_para(p) for p in primary_md.split("\n\n") if p.strip()}
    ordered_others = sorted(
        (r for r in success if r is not primary),
        key=lambda r: _ENGINE_QUALITY.get(r["scraper"], 99),
    )
    supplements = []
    for r in ordered_others:
        for p in (r.get("markdown", "") or "").split("\n\n"):
            p = p.strip()
            if len(p) <= 40:  # 太短的"独有段落"几乎都是噪音碎片
                continue
            key = _norm_para(p)
            if key in seen:
                continue
            seen.add(key)
            supplements.append(p)
    supplements = [] if not allow_supplements else supplements
    merged_md = primary_md
    if allow_supplements and supplements:
        merged_md += "\n\n<!-- 以下为其他引擎补充段落 -->\n\n" + "\n\n".join(supplements)
    if len(merged_md) > max_chars:
        merged_md = merged_md[:max_chars] + "\n\n[... 内容已截断 ...]"

    screenshot = None
    for r in success:
        if r.get("screenshot"):
            screenshot = r["screenshot"]
            break

    extracted = {}
    for r in success:
        if r.get("extracted") and isinstance(r["extracted"], dict):
            for k, v in r["extracted"].items():
                if k not in extracted or not extracted[k]:
                    extracted[k] = v

    return {
        "success": True,
        "scraper": "+".join(used_scrapers),
        "markdown": merged_md,
        "html": primary.get("html", ""),
        "text": primary.get("text", ""),
        "screenshot": screenshot,
        "extracted": extracted if extracted else None,
        "all_results": results,
        "stats": {
            "total_scrapers": len(results),
            "successful": len(success),
            "failed": len(failed),
            "scrapers_used": used_scrapers,
            "primary_scraper": primary["scraper"],
            "primary_quality": round(_md_quality(primary_md), 2),
            "merged_paragraphs": len(seen),
            "supplement_paragraphs": len(supplements) if allow_supplements else 0,
        },
        "error": None,
    }


async def _scrape_smart_async(
    url: str,
    prompt: Optional[str] = None,
    max_chars: int = 50000,
    need_screenshot: bool = False,
    need_login: bool = False,
    timeout: float = 60.0,
    strategy: str = "auto",
    enabled_scrapers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """智能爬取（异步主入口）。

    默认 auto 策略(智能路由): 根据 URL 类型(classify_url) + 引擎历史表现
    自动选择最合适的爬虫组合 —— 不再全开 13 个引擎(慢,且低质引擎的
    补充段落会污染证据)。
    - pricing 页 → JS 渲染组 + 静态对照引擎,且禁止跨引擎合并正文
    - docs 站 → firecrawl + crawl4ai + trafilatura
    - dashboard → playwright + camoufox
    - about/blog 页 → trafilatura + readability + newspaper3k
    - feature/home 页 → firecrawl + crawl4ai + jina

    其他策略:
    - "parallel": 全部可用引擎并行(覆盖最大,只用于 auto 失败后的兜底)
    - "fallback": 旧版串行 fallback(兼容保留)
    """
    if strategy == "fallback":
        from adapters import scrape_with_fallback as old_fallback

        return old_fallback(url, prompt, max_chars, need_screenshot, need_login)

    # ── 智能路由 ──
    url_type = classify_url(url)
    if enabled_scrapers is None and strategy != "all":
        # auto(默认):按 URL 类型 + 历史表现选引擎组合
        if strategy == "auto":
            enabled_scrapers = recommend_scrapers(url, need_login=need_login)
        # 否则(parallel)用全开模式,保证覆盖

    results = await _scrape_parallel(
        url,
        prompt,
        max_chars,
        need_screenshot,
        need_login,
        timeout,
        enabled_scrapers=enabled_scrapers,
    )
    merged = _merge_results(
        results, max_chars,
        allow_supplements=url_type not in _NO_SUPPLEMENT_TYPES,
    )
    merged["url_type"] = url_type

    # ── 引擎表现学习:喂给下一轮智能路由 ──
    try:
        record_engine_outcome(url_type, {
            r.get("scraper", "?"): {
                "success": bool(r.get("success") and r.get("markdown")),
                "quality": _md_quality(r.get("markdown", "")),
            }
            for r in results if r.get("scraper") not in (None, "none", "unknown")
        })
    except Exception:
        pass  # 统计失败绝不影响爬取结果

    return merged


def scrape_smart(
    url: str,
    prompt: Optional[str] = None,
    max_chars: int = 50000,
    need_screenshot: bool = False,
    need_login: bool = False,
    timeout: float = 60.0,
    strategy: str = "auto",
    enabled_scrapers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """智能爬取（同步入口，推荐用这个）。

    默认 auto(智能路由):按 URL 类型 + 引擎历史表现选组合;pricing 页
    自动加静态对照引擎并隔离各引擎原文(供交叉验证)。

    用法：
        # 智能路由(默认,推荐)
        result = scrape_smart("https://example.com/pricing")

        # 全部引擎并行(兜底,覆盖最大)
        result = scrape_smart("https://example.com", strategy="parallel")

        # 指定引擎
        result = scrape_smart(
            "https://example.com",
            enabled_scrapers=["trafilatura", "markdownify"],
        )

        # 截图
        result = scrape_smart(
            "https://example.com", need_screenshot=True,
        )

        # 登录态
        result = scrape_smart(
            "https://app.example.com",
            need_login=True,
        )
    """
    return asyncio.run(
        _scrape_smart_async(
            url,
            prompt,
            max_chars,
            need_screenshot,
            need_login,
            timeout,
            strategy,
            enabled_scrapers=enabled_scrapers,
        )
    )


def list_scrapers() -> Dict[str, bool]:
    """返回所有注册的爬虫及其可用状态（用于 CLI 显示）。"""
    registry = _build_adapter_registry()
    return {name: mod.is_available() for name, (mod, *_) in registry.items()}


# ============================================================
# 兼容：保留旧的 fallback 函数（不推荐，仅紧急回退用）
# ============================================================
def scrape_with_fallback(
    url: str,
    prompt: Optional[str] = None,
    max_chars: int = 50000,
    need_screenshot: bool = False,
    need_login: bool = False,
) -> Dict[str, Any]:
    """⚠️ 旧版串行 fallback（保留以兼容）。新版请用 scrape_smart()。"""
    from adapters import firecrawl_scraper, crawl4ai_scraper, playwright_scraper

    last_error = None
    if firecrawl_scraper.is_available():
        result = firecrawl_scraper.scrape(
            url, max_chars=max_chars, screenshot=need_screenshot
        )
        if result["success"]:
            result["scraper"] = "firecrawl"
            return result
        last_error = result.get("error", "firecrawl failed")
    if crawl4ai_scraper.is_available():
        result = crawl4ai_scraper.scrape(url, prompt=prompt, max_chars=max_chars)
        if result["success"]:
            result["scraper"] = "crawl4ai"
            if need_screenshot and playwright_scraper.is_available():
                ss = playwright_scraper.scrape(
                    url, screenshot_path=f"/tmp/youzi_{url_hash(url)}.png"
                )
                if ss["success"]:
                    result["screenshot"] = ss["screenshot"]
            return result
        last_error = result.get("error", "crawl4ai failed")
    if need_login or playwright_scraper.is_available():
        screenshot_path = f"/tmp/youzi_{url_hash(url)}.png" if need_screenshot else None
        result = playwright_scraper.scrape(
            url, screenshot_path=screenshot_path, extract_prompt=prompt, timeout=30000
        )
        if result["success"]:
            result["scraper"] = "playwright"
            return result
        last_error = result.get("error", "playwright failed")
    return {
        "success": False,
        "scraper": "none",
        "markdown": "",
        "html": "",
        "text": "",
        "screenshot": None,
        "extracted": None,
        "error": last_error or "no scraper available",
    }


__all__ = [
    "scrape_smart",
    "scrape_with_fallback",
    "list_scrapers",
    "url_hash",
    "classify_url",
    "recommend_scrapers",
    "record_engine_outcome",
]
