# -*- coding: utf-8 -*-
"""
/youzi skill · 爬虫 adapter 集合

设计理念：并行 + 智能合并

每个 adapter 实现统一的 scrape(url, **kwargs) → dict 接口。
统一入口 scrape_smart() 自动并行调用 3 个爬虫 + 合并去重结果。
"""
import asyncio
import hashlib
from typing import Dict, Any, Optional, List


def url_hash(url: str) -> str:
    """生成 URL 的短 hash（用于截图文件名 + dedup）。"""
    return hashlib.md5(url.encode()).hexdigest()[:8]


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
                         None 表示全部
    """
    from adapters import firecrawl_scraper, crawl4ai_scraper, playwright_scraper

    tasks = []
    enabled = enabled_scrapers or ["firecrawl", "crawl4ai", "playwright"]

    if "firecrawl" in enabled and firecrawl_scraper.is_available():
        tasks.append(_scrape_one(
            "firecrawl", firecrawl_scraper.scrape,
            url, max_chars=max_chars, screenshot=need_screenshot,
        ))

    if "crawl4ai" in enabled and crawl4ai_scraper.is_available():
        tasks.append(_scrape_one(
            "crawl4ai", crawl4ai_scraper.scrape,
            url, prompt=prompt, max_chars=max_chars,
        ))

    if "playwright" in enabled and (need_login or playwright_scraper.is_available()):
        screenshot_path = f"/tmp/youzi_{url_hash(url)}.png" if need_screenshot else None
        tasks.append(_scrape_one(
            "playwright", playwright_scraper.scrape,
            url, screenshot_path=screenshot_path,
            extract_prompt=prompt, timeout=int(timeout * 1000),
        ))

    if not tasks:
        return [{
            "success": False,
            "scraper": "none",
            "error": "no scraper available (check install)",
            "markdown": "", "html": "", "text": "",
            "screenshot": None, "extracted": None,
        }]

    # 并行执行（带超时）
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # 处理异常
    final = []
    for r in results:
        if isinstance(r, Exception):
            final.append({
                "success": False,
                "scraper": "unknown",
                "error": str(r),
                "markdown": "", "html": "", "text": "",
                "screenshot": None, "extracted": None,
            })
        else:
            final.append(r)
    return final


def _merge_results(results: List[Dict[str, Any]], max_chars: int = 50000) -> Dict[str, Any]:
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
                for r in results if not r.get("success")
            ) or "all failed",
            "all_results": results,  # 保留所有结果用于调试
        }

    # 选 markdown 最长的（通常更完整）
    primary = max(success, key=lambda r: len(r.get("markdown", "") or ""))
    used_scrapers = [r["scraper"] for r in success]

    # 收集所有独有的 markdown 段落
    all_paragraphs = set()
    primary_paragraphs = set()
    for p in (primary.get("markdown", "") or "").split("\n\n"):
        p = p.strip()
        if p:
            primary_paragraphs.add(p)
            all_paragraphs.add(p)
    # 从其他 scraper 找独有的段落
    for r in success:
        if r is primary:
            continue
        for p in (r.get("markdown", "") or "").split("\n\n"):
            p = p.strip()
            if p and p not in primary_paragraphs and len(p) > 30:  # 太短的不要
                all_paragraphs.add(p)
    # 合并去重后的 markdown
    merged_md = "\n\n".join(sorted(all_paragraphs, key=len, reverse=True))
    if len(merged_md) > max_chars:
        merged_md = merged_md[:max_chars] + "\n\n[... 内容已截断 ...]"

    # 选最佳截图（优先级：firecrawl > playwright > crawl4ai）
    screenshot = None
    for r in success:
        if r.get("screenshot"):
            screenshot = r["screenshot"]
            break

    # 合并 extracted（去重 key，merge 多个来源）
    extracted = {}
    for r in success:
        if r.get("extracted") and isinstance(r["extracted"], dict):
            for k, v in r["extracted"].items():
                if k not in extracted or not extracted[k]:
                    extracted[k] = v

    return {
        "success": True,
        "scraper": "+".join(used_scrapers),  # 多 scraper
        "markdown": merged_md,
        "html": primary.get("html", ""),
        "text": primary.get("text", ""),
        "screenshot": screenshot,
        "extracted": extracted if extracted else None,
        "all_results": results,  # 调试用
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
) -> Dict[str, Any]:
    """智能爬取（异步主入口）。

    Args:
        url: 目标 URL
        prompt: LLM 提取提示
        max_chars: markdown 最大长度
        need_screenshot: 是否需要截图
        need_login: 是否需要登录
        timeout: 单个 scraper 超时
        strategy: "parallel"（并行 + 合并，推荐）/ "fallback"（串行兜底）
    """
    if strategy == "fallback":
        # 退回旧版串行逻辑
        from adapters import scrape_with_fallback as old_fallback
        return old_fallback(url, prompt, max_chars, need_screenshot, need_login)

    # === parallel 模式（默认）===
    results = await _scrape_parallel(
        url, prompt, max_chars, need_screenshot, need_login, timeout,
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
) -> Dict[str, Any]:
    """智能爬取（同步入口，推荐用这个）。

    用法：
        result = scrape_smart("https://example.com/features")
        if result["success"]:
            print(f"用 {result['scraper']} 抓的")
            print(result["markdown"][:500])
    """
    return asyncio.run(
        _scrape_smart_async(url, prompt, max_chars, need_screenshot, need_login, timeout, strategy)
    )


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
    """⚠️ 旧版串行 fallback（保留以兼容）。

    新版请用 scrape_smart(strategy='parallel')。
    """
    from adapters import firecrawl_scraper, crawl4ai_scraper, playwright_scraper

    last_error = None
    if firecrawl_scraper.is_available():
        result = firecrawl_scraper.scrape(url, max_chars=max_chars, screenshot=need_screenshot)
        if result["success"]:
            result["scraper"] = "firecrawl"
            return result
        last_error = result.get("error", "firecrawl failed")
    if crawl4ai_scraper.is_available():
        result = crawl4ai_scraper.scrape(url, prompt=prompt, max_chars=max_chars)
        if result["success"]:
            result["scraper"] = "crawl4ai"
            if need_screenshot and playwright_scraper.is_available():
                ss = playwright_scraper.scrape(url, screenshot_path=f"/tmp/youzi_{url_hash(url)}.png")
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
        "success": False, "scraper": "none",
        "markdown": "", "html": "", "text": "",
        "screenshot": None, "extracted": None,
        "error": last_error or "no scraper available",
    }


__all__ = [
    "scrape_smart",         # 🆕 推荐用这个（并行 + 合并）
    "scrape_with_fallback", # 旧版（串行 fallback）
    "firecrawl_scraper",
    "crawl4ai_scraper",
    "playwright_scraper",
    "url_hash",
]
