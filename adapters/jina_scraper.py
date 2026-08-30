# -*- coding: utf-8 -*-
"""
Jina Reader Adapter for /youzi skill

Jina Reader (jina.ai/reader) —— LLM 友好的网页读取器
返回干净的 markdown,无需 API key 即可使用(限流使用)。
GitHub: https://github.com/jina-ai/reader

依赖:requests(已有),无其他额外包

2026-08-29 升级(审计修复):
  - 全局限速(免 key 档 ~20 RPM):页面级并行后 jina 调用会突发集中,
    429 概率随并发度上升 —— 模块级节流阀把请求隔开
  - 429/5xx 指数退避重试 1 次(历史实现 429 即失败,交叉验证丢一票)
  - 头尾截断(truncate_md):jina 输出头部是 Title/URL Source 元数据、
    尾部常承载正文/价格表,纯头部截断会砍掉真实内容
"""

import os
import threading
import time

import requests

from adapters import truncate_md

# 免 key 档全局限速:两次调用最小间隔(秒)。20 RPM ≈ 3.0s;
# 取 2.0s 留余量(实测 6 连发通过,但并行页面下突发更猛)。
_JINA_MIN_INTERVAL_S = 2.0
_THROTTLE_LOCK = threading.Lock()
_THROTTLE_LAST = {"t": 0.0}


def _throttle() -> None:
    """全局节流:保证相邻两次 jina 请求间隔 ≥ _JINA_MIN_INTERVAL_S。"""
    with _THROTTLE_LOCK:
        now = time.monotonic()
        wait = _JINA_MIN_INTERVAL_S - (now - _THROTTLE_LAST["t"])
        if wait > 0:
            time.sleep(wait)
        _THROTTLE_LAST["t"] = time.monotonic()


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


def scrape(url: str, max_chars: int = 50000, timeout: float = 45.0, **kwargs) -> dict:
    """用 Jina Reader 抓取 URL(返回 LLM 友好的 markdown)。

    免费层:无 API key 时可限流使用(建议加 API key 提高限额)。

    Args:
        url: 目标 URL
        max_chars: 返回内容最大字符数
        timeout: HTTP 超时(秒)。C4 修复:原硬编码 45s,改为调用方可下发
                 (scrape_smart 会把单引擎 timeout 传进来);默认值保持原行为。
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

        _throttle()
        resp = requests.get(reader_url, headers=headers, timeout=timeout)
        attempt = 0
        while resp.status_code == 429 or resp.status_code >= 500:
            if attempt >= 1:  # 429/5xx 退避重试 1 次
                break
            attempt += 1
            time.sleep(5.0 * attempt)
            _throttle()
            resp = requests.get(reader_url, headers=headers, timeout=timeout)
        resp.raise_for_status()
        markdown = resp.text

        markdown = truncate_md(markdown, max_chars)

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
