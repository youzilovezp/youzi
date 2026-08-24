# -*- coding: utf-8 -*-
"""
Readability-lxml Adapter for /youzi skill

Mozilla Readability 算法的 Python 移植 (github.com/buriy/python-readability)
用于提取网页"主要可读内容"，类似 Safari Reader / Firefox Reader View 效果。
Apache 2.0 协议。

依赖安装：
    pip install readability-lxml requests
"""

from typing import Optional


def is_available() -> bool:
    try:
        from readability import Document  # noqa: F401

        return True
    except ImportError:
        return False


def scrape(url: str, prompt: Optional[str] = None, max_chars: int = 50000) -> dict:
    """用 Readability-lxml 提取网页主体内容。

    Args:
        url: 目标 URL
        prompt: （readability 不支持 LLM 提取，保留参数兼容性）
        max_chars: 返回内容最大字符数

    Returns:
        {"success": bool, "markdown": str, "extracted": None, "error": str|None, "url": str}
    """
    try:
        import requests
        from readability import Document

        resp = requests.get(
            url,
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (youzi-readability/1.0)"},
        )
        resp.raise_for_status()

        doc = Document(resp.text)
        # clean_html 是清洗后的 HTML，需要再转 markdown
        clean_html = doc.summary()
        title = doc.title()

        # 转 markdown
        import markdownify as _md

        markdown = _md.markdownify(
            clean_html, heading_style="ATX", strip=["script", "style"]
        )
        if title:
            markdown = f"# {title}\n\n" + markdown

        if len(markdown) > max_chars:
            markdown = markdown[:max_chars] + "\n\n[... 内容已截断 ...]"

        return {
            "success": True,
            "error": None,
            "url": url,
            "markdown": markdown,
            "extracted": {"title": title},
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"readability 抓取失败: {type(e).__name__}: {e}",
            "url": url,
            "markdown": "",
            "extracted": None,
        }


if __name__ == "__main__":
    if is_available():
        print("✓ Readability-lxml 可用")
        result = scrape("https://example.com", max_chars=1000)
        print(f"success: {result['success']}")
        if result["success"]:
            print(f"markdown length: {len(result['markdown'])}")
    else:
        print("✗ Readability-lxml 未安装")
        print("安装: pip install readability-lxml requests markdownify")
