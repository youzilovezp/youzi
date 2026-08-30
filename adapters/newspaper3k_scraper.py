# -*- coding: utf-8 -*-
"""
Newspaper3k Adapter for /youzi skill

Newspaper3k (github.com/codelucas/newspaper) —— 老牌新闻/文章抽取库
主提取 article body、authors、publish date、top image、keywords。MIT 协议。

依赖安装：
    pip install newspaper3k lxml_html_clean
"""

from typing import Optional

from adapters import truncate_md


def is_available() -> bool:
    try:
        from newspaper import Article  # noqa: F401

        return True
    except ImportError:
        return False


def scrape(
    url: str,
    prompt: Optional[str] = None,
    max_chars: int = 50000,
    timeout: float = 30.0,
    keep_rx=None,
    **kw,
) -> dict:
    """用 Newspaper3k 抓取 URL 的文章正文。

    Args:
        url: 目标 URL
        prompt: （newspaper3k 不支持 LLM 提取，保留参数兼容性）
        max_chars: 返回内容最大字符数
        timeout: HTTP 下载超时(秒)。C4 修复:原 article.download() 内部
                 requests 无超时,目标站慢连接时线程永久挂起,拖死
                 scrape_smart 的并行组。改为显式 requests.get 带超时。
        keep_rx: 关键 token 保窗正则(定价页 PRICE_TOKEN_RX,截断保窗)

    Returns:
        {"success": bool, "markdown": str, "extracted": dict, "error": str|None, "url": str}
    """
    try:
        import requests
        from newspaper import Article

        resp = requests.get(
            url,
            timeout=timeout,
            headers={
                # 真实 Chrome UA:部分站点对默认 python-requests UA 返回
                # 反爬页/空页
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0 Safari/537.36"
                )
            },
        )
        resp.raise_for_status()

        article = Article(url)
        article.set_html(resp.content)  # set_html 支持 bytes,内部按编码解码
        article.parse()
        try:
            article.nlp()  # 提取关键词 + summary（需 NLTK punkt，无则降级）
        except Exception:
            article.keywords = []
            article.summary = ""

        text = truncate_md(article.text or "", max_chars, keep_rx=keep_rx)

        # 拼成 markdown 风格
        parts = []
        if article.title:
            parts.append(f"# {article.title}\n")
        if article.authors:
            parts.append(f"**作者:** {', '.join(article.authors)}\n")
        if article.publish_date:
            parts.append(f"**发布日期:** {article.publish_date.isoformat()}\n")
        if article.summary:
            parts.append(f"\n> {article.summary}\n")
        parts.append("\n" + text)
        markdown = "\n".join(parts)

        extracted = {
            "title": article.title,
            "authors": article.authors,
            "publish_date": article.publish_date.isoformat()
            if article.publish_date
            else None,
            "keywords": list(article.keywords) if article.keywords else [],
            "summary": article.summary,
            "top_image": article.top_image,
        }

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
            "error": f"newspaper3k 抓取失败: {type(e).__name__}: {e}",
            "url": url,
            "markdown": "",
            "extracted": None,
        }


if __name__ == "__main__":
    if is_available():
        print("✓ Newspaper3k 可用")
        result = scrape("https://example.com", max_chars=1000)
        print(f"success: {result['success']}")
        if result["success"]:
            print(f"markdown length: {len(result['markdown'])}")
    else:
        print("✗ Newspaper3k 未安装")
        print("安装: pip install newspaper3k lxml_html_clean")
