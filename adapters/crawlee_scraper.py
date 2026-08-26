# -*- coding: utf-8 -*-
"""
Crawlee Adapter for /youzi skill

Crawlee (crawlee.dev) — Apify 出品的现代 Python 爬虫框架
>50K stars，自带反爬（指纹伪装 / UA 轮换 / 代理管理 / 智能重试），
是 Scrapy 的现代替代品，特别适合大规模 + 高反爬场景。

⚠️ 注意：Crawlee 是为「多 URL + 批量」设计的，单 URL 走 Crawlee 的开销
比直接 requests 大（启动 crawler + 配置 router），所以这个 adapter 仅作
"框架注册占位 + 抗反爬的快速通道"。如果以后要做批量整站爬取，建议直接
调 Crawlee 的 Crawler.run()，而不是当前这种单 URL 接口。

依赖：pip install crawlee
"""

import asyncio
from typing import Any, Dict

try:
    # crawlee-python 1.0+ 新 import 路径
    from crawlee.crawlers import HttpCrawler  # type: ignore

    _CRAWLEE_OK = True
except ImportError:  # pragma: no cover
    try:
        # crawlee-python 0.x 旧 import 路径
        from crawlee import HttpCrawler  # type: ignore

        _CRAWLEE_OK = True
    except ImportError:
        HttpCrawler = None  # type: ignore
        _CRAWLEE_OK = False


def is_available() -> bool:
    """检查 crawlee 是否安装。"""
    return _CRAWLEE_OK and HttpCrawler is not None


def _empty_result(url: str, error: str) -> Dict[str, Any]:
    return {
        "success": False,
        "error": error,
        "url": url,
        "markdown": "",
        "html": "",
        "text": "",
        "screenshot": None,
        "extracted": None,
    }


async def _scrape_async(url: str, max_chars: int = 50000) -> Dict[str, Any]:
    """异步抓取单 URL —— 复用 Crawlee 的反爬管道（指纹/UA/重试/代理）。

    用 handler 注册模式而非子类继承，避免和 crawlee 不同版本的构造函数签名冲突。
    """
    if not _CRAWLEE_OK or HttpCrawler is None:
        return _empty_result(url, "crawlee 未安装。运行: pip install crawlee")

    captured: Dict[str, Any] = {}

    # 构造 crawler —— 兼容新旧两个版本的 __init__ 签名
    crawler = None
    init_errors = []
    for init_kwargs in (
        {"max_requests_per_crawl": 1},  # 1.0+ 显式参数
        {},  # 0.x / 1.0+ 都允许空构造
    ):
        try:
            crawler = HttpCrawler(**init_kwargs)  # type: ignore[call-arg]
            break
        except TypeError as e:
            init_errors.append(f"kwargs={init_kwargs}: {e}")
        except Exception as e:  # pragma: no cover
            return _empty_result(url, f"crawlee 初始化失败: {type(e).__name__}: {e}")

    if crawler is None:
        return _empty_result(
            url,
            "crawlee 初始化失败（无匹配签名）: " + "; ".join(init_errors),
        )

    async def _handler(context: Any) -> None:
        try:
            body_bytes = await context.http_response.read()
            captured["html"] = body_bytes.decode("utf-8", errors="ignore")
            captured["url"] = context.request.url
        except Exception as e:  # pragma: no cover
            captured["error"] = f"crawlee 抓取失败: {type(e).__name__}: {e}"

    # 注册 handler —— 新旧 router API 都尝试
    try:
        if hasattr(crawler, "router") and hasattr(crawler.router, "default_handler"):
            crawler.router.default_handler(_handler)
        else:  # pragma: no cover
            return _empty_result(url, "crawlee API 不兼容（无 router.default_handler）")
    except Exception as e:
        return _empty_result(url, f"crawlee handler 注册失败: {type(e).__name__}: {e}")

    try:
        await crawler.run([url])
    except Exception as e:
        return _empty_result(url, f"crawlee 运行失败: {type(e).__name__}: {e}")

    if "error" in captured:
        return _empty_result(url, captured["error"])

    html: str = captured.get("html", "")
    if not html:
        return _empty_result(url, "crawlee 抓取为空")

    # 借用 markdownify 转换（与 firecrawl / scrapy adapter 一致）
    try:
        from markdownify import markdownify as _md

        title = None
        try:
            from bs4 import BeautifulSoup  # noqa

            soup = BeautifulSoup(html, "lxml")
            if soup.title and soup.title.string:
                title = soup.title.string.strip()
        except Exception:
            title = None

        markdown: str = _md(
            html, heading_style="ATX", strip=["script", "style", "noscript"]
        )
        if title:
            markdown = f"# {title}\n\n" + markdown
    except Exception:
        markdown = html

    if len(markdown) > max_chars:
        markdown = markdown[:max_chars] + "\n\n[... 内容已截断 ...]"

    return {
        "success": True,
        "error": None,
        "url": url,
        "markdown": markdown,
        "html": html,
        "text": markdown,
        "screenshot": None,
        "extracted": None,
    }


def scrape(url: str, max_chars: int = 50000, **kwargs: Any) -> Dict[str, Any]:
    """同步入口（与其它 adapter 保持统一签名）。

    kwargs 由 adapter 调度器统一传入（prompt/screenshot/extract_prompt/timeout），
    Crawlee 单 URL adapter 用不到，这里只是为保持统一签名。
    """
    del kwargs
    try:
        loop = asyncio.get_running_loop()
        future = asyncio.run_coroutine_threadsafe(
            _scrape_async(url, max_chars), loop
        )
        return future.result(timeout=60)
    except RuntimeError:
        # 没有运行中的 event loop —— 自己起一个
        return asyncio.run(_scrape_async(url, max_chars))


if __name__ == "__main__":
    if is_available():
        print("✓ Crawlee 可用")
        r = scrape("https://example.com", max_chars=1000)
        print(f"  success: {r['success']}, length: {len(r['markdown'])}")
    else:
        print("✗ Crawlee 未安装")
        print("  安装: pip install crawlee")