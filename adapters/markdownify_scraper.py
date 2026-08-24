# -*- coding: utf-8 -*-
"""
Markdownify Adapter for /youzi skill

markdownify (github.com/matthewwithanm/python-markdownify) —— 轻量 HTML→Markdown
本身不是爬虫，但作为通用 HTML→MD fallback 链路使用。
MIT 协议。

依赖安装：
    pip install markdownify requests beautifulsoup4
"""

from typing import Optional


def is_available() -> bool:
    try:
        import markdownify  # noqa: F401

        return True
    except ImportError:
        return False


def scrape(url: str, prompt: Optional[str] = None, max_chars: int = 50000) -> dict:
    """直接抓 HTML → 转 markdown（最朴素的 fallback）。"""
    try:
        import requests
        from bs4 import BeautifulSoup
        import markdownify as _md

        resp = requests.get(
            url,
            timeout=30,
            headers={"User-Agent": "Mozilla/5.0 (youzi-markdownify/1.0)"},
        )
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "html.parser")
        # 移除脚本/样式
        for tag in soup(["script", "style", "nav", "footer", "header"]):
            tag.decompose()

        title = (soup.title.string or "").strip() if soup.title else ""
        markdown = _md.markdownify(str(soup), heading_style="ATX")
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
            "error": f"markdownify 抓取失败: {type(e).__name__}: {e}",
            "url": url,
            "markdown": "",
            "extracted": None,
        }


if __name__ == "__main__":
    if is_available():
        print("✓ Markdownify 可用")
        result = scrape("https://example.com", max_chars=1000)
        print(f"success: {result['success']}")
        if result["success"]:
            print(f"markdown length: {len(result['markdown'])}")
    else:
        print("✗ Markdownify 未安装")
        print("安装: pip install markdownify beautifulsoup4 requests")
