# -*- coding: utf-8 -*-
"""
Trafilatura Adapter for /youzi skill

Trafilatura (github.com/adbar/trafilatura) —— 学术界主流 web 内容抽取库
主打 web 文章/正文抽取 + 元数据 + 结构化输出。比 BeautifulSoup 更精准，
比 newspaper3k 更轻量。GPLv3 协议。

依赖安装：
    pip install trafilatura
"""

from typing import Optional


def is_available() -> bool:
    try:
        import trafilatura  # noqa: F401

        return True
    except ImportError:
        return False


def scrape(url: str, prompt: Optional[str] = None, max_chars: int = 50000) -> dict:
    """用 Trafilatura 抓取 URL 的正文。

    Args:
        url: 目标 URL
        prompt: （trafilatura 不支持 LLM 提取，保留参数兼容性）
        max_chars: 返回内容最大字符数

    Returns:
        {"success": bool, "markdown": str, "extracted": None, "error": str|None, "url": str}
    """
    try:
        import trafilatura

        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return {
                "success": False,
                "error": "trafilatura fetch_url 返回空（网络/反爬失败）",
                "url": url,
                "markdown": "",
                "extracted": None,
            }

        # 输出 markdown 格式，含元数据
        result = trafilatura.extract(
            downloaded,
            output_format="markdown",
            include_links=True,
            include_images=False,
            include_tables=True,
            include_formatting=True,
            with_metadata=True,
        )
        if not result:
            return {
                "success": False,
                "error": "trafilatura extract 返回空（页面无正文）",
                "url": url,
                "markdown": "",
                "extracted": None,
            }

        markdown = result
        if len(markdown) > max_chars:
            markdown = markdown[:max_chars] + "\n\n[... 内容已截断 ...]"

        return {
            "success": True,
            "error": None,
            "url": url,
            "markdown": markdown,
            "extracted": None,
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"trafilatura 抓取失败: {type(e).__name__}: {e}",
            "url": url,
            "markdown": "",
            "extracted": None,
        }


if __name__ == "__main__":
    if is_available():
        print("✓ Trafilatura 可用")
        result = scrape("https://example.com", max_chars=1000)
        print(f"success: {result['success']}")
        if result["success"]:
            print(f"markdown length: {len(result['markdown'])}")
    else:
        print("✗ Trafilatura 未安装")
        print("安装: pip install trafilatura")
