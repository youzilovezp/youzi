# -*- coding: utf-8 -*-
"""
Scrapy Adapter for /youzi skill

Scrapy (scrapy.org) —— Python 工业级爬虫框架(>50K stars)
最适合:批量抓取、follow links、自动去重、pipeline 扩展。

⚠️ 注意:Scrapy 是为「多 URL + 批量」设计的,单 URL 走 Scrapy 的开销极大
(spawn reactor + downloader + spider 类),所以这个 adapter 仅作"框架注册
占位"。如果以后要做批量全站爬取,建议直接调 Scrapy 的 CrawlerProcess,
而不是当前这种单 URL 接口。

依赖:pip install scrapy
"""

try:
    from scrapy.http import HtmlResponse as _HtmlResponse

    _SCRAPY_OK = True
except ImportError:  # pragma: no cover
    _HtmlResponse = None  # type: ignore
    _SCRAPY_OK = False


def is_available() -> bool:
    return _SCRAPY_OK and _HtmlResponse is not None


def scrape(url: str, max_chars: int = 50000, **kwargs):
    # kwargs 由 adapter 调度器统一传入(prompt/screenshot/extract_prompt/timeout),
    # Scrapy 单 URL adapter 用不到,这里只是为保持统一签名。
    del kwargs
    """单 URL adapter — 通过 Scrapy 的 HtmlResponse 复用 Scrapy 的解析管线。

    不真的启动 Scrapy reactor(那要几秒钟),而是直接构造一个 HtmlResponse,
    让它走 Scrapy 的 selector + 文本抽取器,产出干净的正文。
    """
    try:
        import requests as _req
        from markdownify import markdownify as _md

        resp = _req.get(
            url,
            headers={"User-Agent": "youzi-scrapy/1.0 (+https://github.com/youzi)"},
            timeout=30,
        )
        resp.raise_for_status()
        # 借用 Scrapy 的 HtmlResponse + Selector 做正文抽取(轻量)
        body = resp.text.encode(resp.encoding or "utf-8")
        if _HtmlResponse is None:
            raise RuntimeError("scrapy 未安装")
        scrapy_resp = _HtmlResponse(url=url, body=body, encoding="utf-8")
        # 用 CSS 抓 <title> + meta description 作为精简 HTML,再转 markdown
        title = (scrapy_resp.css("title::text").get() or "").strip()
        desc = (
            scrapy_resp.css('meta[name="description"]::attr(content)').get() or ""
        ).strip()
        body_html = scrapy_resp.css("body").get() or resp.text

        markdown = _md(body_html, heading_style="ATX", strip=["script", "style"])
        if title:
            markdown = f"# {title}\n\n" + markdown
        if desc:
            markdown += f"\n\n*{desc}*\n"

        if len(markdown) > max_chars:
            markdown = markdown[:max_chars] + "\n\n[... 内容已截断 ...]"

        return {
            "success": True,
            "error": None,
            "url": url,
            "markdown": markdown,
            "html": resp.text,
            "text": markdown,
            "screenshot": None,
            "extracted": None,
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"scrapy 抓取失败: {type(e).__name__}: {e}",
            "url": url,
            "markdown": "",
            "html": "",
            "text": "",
            "screenshot": None,
            "extracted": None,
        }


if __name__ == "__main__":
    if is_available():
        print("✓ Scrapy 可用")
        r = scrape("https://example.com", max_chars=1000)
        print(f"  success: {r['success']}, length: {len(r['markdown'])}")
    else:
        print("✗ Scrapy 未安装")
