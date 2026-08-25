# -*- coding: utf-8 -*-
"""
Jina Reader Adapter for /youzi skill

Jina Reader (jina.ai/reader) —— LLM 友好的网页读取器
返回干净的 markdown,无需 API key 即可使用(限流使用)。
GitHub: https://github.com/jina-ai/reader

依赖:requests(已有),无其他额外包
"""

import os
import requests


def is_available() -> bool:
    """Jina Reader 是公开 HTTP 服务,只要能 import requests 就算可用。

    真实可用性由 scrape() 内部的网络错误兜底 —— 不要在这里发探测请求,
    否则每次 list_scrapers() / scrape_smart() 都会阻塞一次 5s 网络往返。
    """
    try:
        import requests as _req  # noqa: F401

        # 仅作可用性探测,不实际发请求
        return bool(_req.__version__)
    except ImportError:
        return False


def scrape(url: str, max_chars: int = 50000, **kwargs) -> dict:
    """用 Jina Reader 抓取 URL(返回 LLM 友好的 markdown)。

    免费层:无 API key 时可限流使用(建议加 API key 提高限额)。
    """
    try:
        # r.jina.ai 是公开的 reader 入口,加 X-Return-Format 头
        reader_url = f"https://r.jina.ai/{url}"
        headers = {
            "X-Return-Format": "markdown",
            "User-Agent": "youzi-jina-reader/1.0",
        }
        api_key = kwargs.get("api_key") or os.environ.get("JINA_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        resp = requests.get(reader_url, headers=headers, timeout=45)
        resp.raise_for_status()
        markdown = resp.text

        if len(markdown) > max_chars:
            markdown = markdown[:max_chars] + "\n\n[... 内容已截断 ...]"

        return {
            "success": True,
            "error": None,
            "url": url,
            "markdown": markdown,
            "html": "",
            "text": markdown,
            "screenshot": None,
            "extracted": None,
        }
    except Exception as e:
        return {
            "success": False,
            "error": f"jina_reader 抓取失败: {type(e).__name__}: {e}",
            "url": url,
            "markdown": "",
            "html": "",
            "text": "",
            "screenshot": None,
            "extracted": None,
        }


if __name__ == "__main__":
    if is_available():
        print("✓ Jina Reader 可用")
        r = scrape("https://example.com", max_chars=1000)
        print(f"  success: {r['success']}, length: {len(r['markdown'])}")
    else:
        print("✗ Jina Reader 不可用")
