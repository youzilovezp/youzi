# -*- coding: utf-8 -*-
"""
Crawl4AI Adapter for /youzi skill

开源 LLM-ready 网页爬虫（github.com/unclecode/crawl4ai）
作为 firecrawl 的免费 fallback + 本地化部署选项。

依赖安装：
    pip install crawl4ai
    crawl4ai-setup    # 安装 Playwright + 浏览器
"""
import asyncio
import os
from typing import Optional


async def _scrape(url: str, prompt: Optional[str] = None, max_chars: int = 50000) -> dict:
    """异步抓取单个 URL。

    Args:
        url: 目标 URL
        prompt: LLM 提取提示（None = 仅返回 Markdown）
        max_chars: 返回内容最大字符数

    Returns:
        {
            "markdown": str,       # 页面 Markdown
            "extracted": dict,     # LLM 提取的结构化字段（如果提供 prompt）
            "success": bool,
            "error": str | None,
            "url": str,
        }
    """
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig, LLMConfig
    except ImportError:
        return {
            "success": False,
            "error": "crawl4ai 未安装。运行: pip install crawl4ai && crawl4ai-setup",
            "url": url,
            "markdown": "",
            "extracted": None,
        }

    try:
        # 配置：使用 Chromium headless，支持 JS 重度
        browser_config = BrowserConfig(
            headless=True,
            user_agent="Mozilla/5.0 (youzi-crawl4ai/1.0)",
        )

        # LLM 配置（可选 - 用于结构化提取）
        extraction_strategy = None
        if prompt:
            try:
                from crawl4ai.extraction_strategy import LLMExtractionStrategy
                api_key = os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY")
                if api_key:
                    if "ANTHROPIC" in os.environ:
                        llm_config = LLMConfig(provider="anthropic", api_key=api_key, model="claude-3-5-sonnet-20241022")
                    else:
                        llm_config = LLMConfig(provider="openai", api_key=api_key, model="gpt-4o-mini")
                    extraction_strategy = LLMExtractionStrategy(
                        llm_config=llm_config,
                        instruction=prompt,
                    )
            except Exception:
                extraction_strategy = None

        # crawl4ai 1.x: extraction_strategy 接收对象（不是字符串）
        run_config = CrawlerRunConfig(
            extraction_strategy=extraction_strategy,
        )

        async with AsyncWebCrawler(config=browser_config) as crawler:
            result = await crawler.arun(url=url, config=run_config)

            markdown = result.markdown or ""
            # 截断（防止单页过大）
            if len(markdown) > max_chars:
                markdown = markdown[:max_chars] + "\n\n[... 内容已截断 ...]"

            extracted = None
            if hasattr(result, "extracted_content") and result.extracted_content:
                try:
                    import json
                    if isinstance(result.extracted_content, str):
                        extracted = json.loads(result.extracted_content)
                    else:
                        extracted = result.extracted_content
                except Exception:
                    extracted = {"raw": str(result.extracted_content)}

            return {
                "success": True,
                "error": None,
                "url": url,
                "markdown": markdown,
                "extracted": extracted,
            }

    except Exception as e:
        return {
            "success": False,
            "error": f"crawl4ai 抓取失败: {type(e).__name__}: {e}",
            "url": url,
            "markdown": "",
            "extracted": None,
        }


def scrape(url: str, prompt: Optional[str] = None, max_chars: int = 50000) -> dict:
    """同步接口（包装异步）。

    示例：
        result = scrape("https://example.com/features")
        if result["success"]:
            print(result["markdown"])
    """
    # 兼容已有 event loop 的调用（如 scrape_smart 并行调用）
    try:
        loop = asyncio.get_running_loop()
        future = asyncio.run_coroutine_threadsafe(_scrape(url, prompt, max_chars), loop)
        return future.result(timeout=60)
    except RuntimeError:
        return asyncio.run(_scrape(url, prompt, max_chars))


def is_available() -> bool:
    """检查 crawl4ai 是否安装可用。"""
    try:
        from crawl4ai import AsyncWebCrawler  # noqa
        return True
    except ImportError:
        return False


if __name__ == "__main__":
    # 简单自测
    if is_available():
        print("✓ Crawl4AI 可用")
        result = scrape("https://example.com", max_chars=1000)
        print(f"success: {result['success']}")
        if result["success"]:
            print(f"markdown length: {len(result['markdown'])}")
    else:
        print("✗ Crawl4AI 未安装")
        print("安装: pip install crawl4ai && crawl4ai-setup")
