# -*- coding: utf-8 -*-
"""
html2text Adapter for /youzi skill

html2text (github.com/Alir3z4/html2text) —— GitHub 15K+ stars 的老牌
HTML → Markdown 转换器。比 BeautifulSoup 更专注于 markdown 输出。
MIT 协议。

依赖:pip install html2text
"""

import requests


def is_available() -> bool:
    try:
        import html2text as _h2t  # noqa: F401

        return bool(getattr(_h2t, "__version__", True))
    except ImportError:
        return False


def scrape(url: str, max_chars: int = 50000, **kwargs):
    # kwargs 由 adapter 调度器统一传入(prompt/screenshot/extract_prompt/timeout),
    # 本 adapter 用不到,这里只是为保持统一签名。
    del kwargs
    """用 requests + html2text 抓取并转 markdown。"""
    try:
        import html2text

        headers = {
            "User-Agent": "youzi-html2text/1.0 (compatible; +https://github.com/youzi)"
        }
        resp = requests.get(url, headers=headers, timeout=30)
        resp.raise_for_status()

        h = html2text.HTML2Text()
        h.ignore_links = False
        h.ignore_images = True
        h.body_width = 0  # 不换行
        h.unicode_snob = True
        markdown = h.handle(resp.text)

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
            "error": f"html2text 抓取失败: {type(e).__name__}: {e}",
            "url": url,
            "markdown": "",
            "html": "",
            "text": "",
            "screenshot": None,
            "extracted": None,
        }


if __name__ == "__main__":
    if is_available():
        print("✓ html2text 可用")
        r = scrape("https://example.com", max_chars=1000)
        print(f"  success: {r['success']}, length: {len(r['markdown'])}")
    else:
        print("✗ html2text 未安装")
