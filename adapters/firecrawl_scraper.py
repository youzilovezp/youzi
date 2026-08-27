# -*- coding: utf-8 -*-
"""
Firecrawl Adapter for /youzi skill（最优先的爬虫）

API 文档：https://docs.firecrawl.dev
依赖：firecrawl-py  或  调用 firecrawl CLI
"""

import base64
import json
import os
import shutil
import subprocess
from typing import Optional


def is_available() -> bool:
    """V2:仅 FIRECRAWL_API_KEY 存在时启用(无 key 的 CLI 通道曾长期 402 欠费,
    engine-stats ok=0.12,纯噪声)。"""
    return bool(os.environ.get("FIRECRAWL_API_KEY"))


def _screenshot_to_b64(screenshot_url: str) -> str:
    """下载截图 URL 并转 base64 data URL;失败返回空串(不影响正文)。"""
    if screenshot_url and screenshot_url.startswith("http"):
        try:
            import urllib.request

            with urllib.request.urlopen(screenshot_url, timeout=30) as resp:
                return (
                    "data:image/jpeg;base64," + base64.b64encode(resp.read()).decode()
                )
        except Exception:
            pass
    return ""


def _truncate(markdown: str, max_chars: int) -> str:
    if len(markdown) > max_chars:
        return markdown[:max_chars] + "\n\n[... 内容已截断 ...]"
    return markdown


def _scrape_cli(
    url: str, max_chars: int, screenshot: bool, timeout: float
) -> Optional[dict]:
    """尝试 firecrawl CLI 通道。成功返回结果 dict;任何失败返回 None,
    由调用方 fall through 到 REST 通道(C5 修复:原实现 CLI 非零退出
    不 raise,直接落到函数底部返回"不可用",REST 通道被永久短路)。
    """
    # shutil.which 跨平台(原 subprocess.run(["which", ...]) 在 Windows 上无 which)
    if not shutil.which("firecrawl"):
        return None

    cmd = [
        "firecrawl",
        "scrape",
        url,
        "--only-main-content",
        "--wait-for",
        "2000",
    ]
    if screenshot:
        cmd.extend(["-f", "screenshot", "-f", "markdown"])
    else:
        cmd.extend(["-f", "markdown"])

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            # 显式 utf-8:Windows 默认 cp1252 解码遇到非 ASCII 内容会炸
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return None

    if result.returncode != 0:
        # 402 欠费/限流/网络错误 → fall through 到 REST
        return None

    out = result.stdout or ""
    # 解析输出（单 format 是 raw，multi format 是 JSON）
    if screenshot and out.strip().startswith("{"):
        try:
            data = json.loads(out)
        except ValueError:
            return None  # 输出解析失败 → fall through 到 REST
        markdown = data.get("markdown", "")
        screenshot_b64 = _screenshot_to_b64(data.get("screenshot", ""))
    else:
        markdown = out
        screenshot_b64 = ""

    if not markdown.strip():
        return None  # CLI 成功但无内容 → REST 再试一次

    return {
        "success": True,
        "markdown": _truncate(markdown, max_chars),
        "html": "",
        "screenshot": screenshot_b64,
        "error": None,
    }


def _scrape_rest(url: str, max_chars: int, screenshot: bool, timeout: float) -> dict:
    """REST API 通道（需 FIRECRAWL_API_KEY）。"""
    api_key = os.environ.get("FIRECRAWL_API_KEY")
    if not api_key:
        return {
            "success": False,
            "markdown": "",
            "html": "",
            "screenshot": None,
            "error": "firecrawl 不可用（既无 CLI 也无 API key）",
        }

    import urllib.request

    formats = ["markdown"]
    if screenshot:
        formats.append("screenshot")

    req_data = {
        "url": url,
        "formats": formats,
        "onlyMainContent": True,
    }
    req = urllib.request.Request(
        "https://api.firecrawl.dev/v1/scrape",
        data=json.dumps(req_data).encode(),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())

    markdown = data.get("data", {}).get("markdown", "")
    screenshot_b64 = _screenshot_to_b64(data.get("data", {}).get("screenshot", ""))

    return {
        "success": True,
        "markdown": _truncate(markdown, max_chars),
        "html": "",
        "screenshot": screenshot_b64,
        "error": None,
    }


def scrape(
    url: str,
    max_chars: int = 50000,
    screenshot: bool = False,
    timeout: float = 120.0,
) -> dict:
    """用 firecrawl 抓取 URL。

    优先用 CLI（firecrawl scrape ...），CLI 失败/解析失败时 fall through
    到 REST API(C5 修复:原实现 CLI 非零退出直接返回"不可用",REST 分支
    永远不执行,即使 FIRECRAWL_API_KEY 完好)。

    Args:
        url: 目标 URL
        max_chars: markdown 最大字符数
        screenshot: 是否保存截图
        timeout: 单通道超时(秒)。C4 修复:原硬编码 120s,改为调用方可下发;
                 默认值保持原行为。

    Returns:
        {
            "success": bool,
            "markdown": str,
            "html": str,
            "screenshot": str | None,    # base64 data URL
            "error": str | None,
        }
    """
    try:
        result = _scrape_cli(url, max_chars, screenshot, timeout)
        if result is not None:
            return result
        return _scrape_rest(url, max_chars, screenshot, timeout)
    except Exception as e:
        return {
            "success": False,
            "markdown": "",
            "html": "",
            "screenshot": None,
            "error": f"firecrawl 错误: {type(e).__name__}: {e}",
        }


if __name__ == "__main__":
    if is_available():
        print("✓ Firecrawl 可用")
        result = scrape("https://example.com", max_chars=500)
        print(f"success: {result['success']}")
        if result["success"]:
            print(f"markdown length: {len(result['markdown'])}")
    else:
        print("✗ Firecrawl 未配置")
        print("配置: export FIRECRAWL_API_KEY='fc-xxx' 或 npm install -g firecrawl-cli")
