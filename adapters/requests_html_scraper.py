# -*- coding: utf-8 -*-
"""
requests-html Adapter for /youzi skill

requests-html (github.com/psf/requests-html) —— PyPI 主流(>15K stars)
基于 requests + PyQuery + reparser 的易用爬虫,自动跟随 JS 重定向。
MIT 协议。

依赖:pip install requests-html
(首次运行会自动下载 chromium)
"""


def is_available() -> bool:
    try:
        import requests_html as _rh  # noqa: F401

        return bool(getattr(_rh, "__version__", True))
    except ImportError:
        return False


def scrape(url: str, max_chars: int = 50000, **kwargs):
    """用 requests-html 抓取(支持 JS 渲染)。"""
    # kwargs 由 adapter 调度器统一传入(prompt/screenshot/extract_prompt/timeout),
    # 本 adapter 用不到,这里只是为保持统一签名。
    del kwargs
    try:
        from requests_html import HTML, HTMLSession

        session: HTMLSession = HTMLSession()
        headers = {
            "User-Agent": "youzi-requests-html/1.0 (compatible; +https://github.com/youzi)"
        }
        resp = session.get(url, headers=headers, timeout=30)
        resp.raise_for_status()

        # Pyright 把 session.get() 推断成 requests.Response,实际返回的是 HTMLResponse
        page: HTML = getattr(resp, "html")

        # 尝试 JS 渲染(如 chromium 已装)
        try:
            page.render(timeout=15, sleep=1)
        except Exception:
            pass  # 渲染失败就退到静态 HTML

        # 转 markdown
        from markdownify import markdownify as _md

        try:
            markdown = _md(
                str(page.html), heading_style="ATX", strip=["script", "style"]
            )
        except Exception:
            markdown = page.text

        if len(markdown) > max_chars:
            markdown = markdown[:max_chars] + "\n\n[... 内容已截断 ...]"

        return {
            "success": True,
            "error": None,
            "url": url,
            "markdown": markdown,
            "html": str(page.html),
            "text": page.text,
            "screenshot": None,
            "extracted": None,
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"requests_html 抓取失败: {type(e).__name__}: {e}",
            "url": url,
            "markdown": "",
            "html": "",
            "text": "",
            "screenshot": None,
            "extracted": None,
        }


if __name__ == "__main__":
    if is_available():
        print("✓ requests-html 可用")
        r = scrape("https://example.com", max_chars=1000)
        print(f"  success: {r['success']}, length: {len(r['markdown'])}")
    else:
        print("✗ requests-html 未安装")
