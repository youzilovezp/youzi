# -*- coding: utf-8 -*-
"""
/youzi skill · 爬虫 adapter 集合

设计理念：并行 + 智能合并

每个 adapter 实现统一的 scrape(url, **kwargs) → dict 接口。
统一入口 scrape_smart() 自动并行调用 7 个爬虫 + 合并去重结果。

支持的爬虫（按优先级）：
  🔒 商业（需 API key）：
    1. firecrawl         —— 96% 网页覆盖 + JS 重度 + 截图

  🆓 开源（本地运行）：
    2. crawl4ai          —— Python, LLM-ready markdown
    3. trafilatura       —— 学术界标准 web 正文抽取
    4. newspaper3k       —— 老牌新闻/文章抽取（带 NLP）
    5. readability-lxml  —— Mozilla Readability 算法移植
    6. markdownify       —— 通用 HTML→MD fallback
    7. playwright        —— 浏览器自动化（登录/JS 重度）
"""

import asyncio
import hashlib
from typing import Dict, Any, Optional, List


def url_hash(url: str) -> str:
    """生成 URL 的短 hash（用于截图文件名 + dedup）。"""
    return hashlib.md5(url.encode()).hexdigest()[:8]


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
        # 新增的 4 个主流爬虫(Scrapy/Jina/html2text/requests-html)
        scrapy_scraper,
        jina_scraper,
        html2text_scraper,
        requests_html_scraper,
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


def _merge_results(
    results: List[Dict[str, Any]], max_chars: int = 50000
) -> Dict[str, Any]:
    """智能合并多个 scraper 的结果。

    策略：
    - 成功结果：取 markdown 最长的作为主体（通常是 firecrawl 质量最高）
    - 独有内容：合并每个 scraper 独有的内容（去重）
    - 截图：优先 firecrawl > playwright > crawl4ai
    - 错误：保留所有错误信息用于调试
    """
    success = [r for r in results if r.get("success")]
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

    primary = max(success, key=lambda r: len(r.get("markdown", "") or ""))
    used_scrapers = [r["scraper"] for r in success]

    all_paragraphs = set()
    primary_paragraphs = set()
    for p in (primary.get("markdown", "") or "").split("\n\n"):
        p = p.strip()
        if p:
            primary_paragraphs.add(p)
            all_paragraphs.add(p)
    for r in success:
        if r is primary:
            continue
        for p in (r.get("markdown", "") or "").split("\n\n"):
            p = p.strip()
            if p and p not in primary_paragraphs and len(p) > 30:
                all_paragraphs.add(p)
    merged_md = "\n\n".join(sorted(all_paragraphs, key=len, reverse=True))
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
            "merged_paragraphs": len(all_paragraphs),
            "primary_paragraphs": len(primary_paragraphs),
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
    strategy: str = "parallel",
    enabled_scrapers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """智能爬取（异步主入口）。"""
    if strategy == "fallback":
        from adapters import scrape_with_fallback as old_fallback

        return old_fallback(url, prompt, max_chars, need_screenshot, need_login)

    results = await _scrape_parallel(
        url,
        prompt,
        max_chars,
        need_screenshot,
        need_login,
        timeout,
        enabled_scrapers=enabled_scrapers,
    )
    return _merge_results(results, max_chars)


def scrape_smart(
    url: str,
    prompt: Optional[str] = None,
    max_chars: int = 50000,
    need_screenshot: bool = False,
    need_login: bool = False,
    timeout: float = 60.0,
    strategy: str = "parallel",
    enabled_scrapers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """智能爬取（同步入口，推荐用这个）。

    用法：
        # 7 个爬虫全开（默认）
        result = scrape_smart("https://example.com/features")

        # 只用 2 个开源最快的
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
    "firecrawl_scraper",
    "crawl4ai_scraper",
    "trafilatura_scraper",
    "newspaper3k_scraper",
    "readability_scraper",
    "markdownify_scraper",
    "playwright_scraper",
]
