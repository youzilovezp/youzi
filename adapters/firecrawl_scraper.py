# -*- coding: utf-8 -*-
"""
Firecrawl Adapter for /youzi skill（最优先的爬虫）

API 文档：https://docs.firecrawl.dev
依赖：firecrawl-py  或  调用 firecrawl CLI
"""

import os
import subprocess
import json
import base64


def is_available() -> bool:
    """检查 firecrawl 是否可用（CLI 或 API key）。"""
    api_key = os.environ.get("FIRECRAWL_API_KEY")
    if api_key:
        return True
    # 检查 CLI
    try:
        result = subprocess.run(
            ["which", "firecrawl"], capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def scrape(url: str, max_chars: int = 50000, screenshot: bool = False) -> dict:
    """用 firecrawl 抓取 URL。

    优先用 CLI（firecrawl scrape ...），fallback 到 REST API。

    Args:
        url: 目标 URL
        max_chars: markdown 最大字符数
        screenshot: 是否保存截图

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
        # === 尝试 1: firecrawl CLI ===
        try:
            subprocess.run(
                ["which", "firecrawl"], capture_output=True, check=True, timeout=5
            )
            # 用 firecrawl CLI 抓取
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

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)

            if result.returncode == 0:
                out = result.stdout
                # 解析输出（单 format 是 raw，multi format 是 JSON）
                if screenshot and out.strip().startswith("{"):
                    data = json.loads(out)
                    markdown = data.get("markdown", "")
                    screenshot_url = data.get("screenshot", "")
                    screenshot_b64 = ""
                    if screenshot_url and screenshot_url.startswith("http"):
                        # 下载截图并转 base64
                        try:
                            import urllib.request

                            with urllib.request.urlopen(
                                screenshot_url, timeout=30
                            ) as resp:
                                screenshot_b64 = (
                                    "data:image/jpeg;base64,"
                                    + base64.b64encode(resp.read()).decode()
                                )
                        except Exception:
                            pass
                else:
                    markdown = out
                    screenshot_b64 = ""

                if len(markdown) > max_chars:
                    markdown = markdown[:max_chars] + "\n\n[... 内容已截断 ...]"

                return {
                    "success": True,
                    "markdown": markdown,
                    "html": "",
                    "screenshot": screenshot_b64,
                    "error": None,
                }
        except (subprocess.CalledProcessError, FileNotFoundError):
            pass

        # === 尝试 2: REST API（如果有 API key）===
        api_key = os.environ.get("FIRECRAWL_API_KEY")
        if api_key:
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
            with urllib.request.urlopen(req, timeout=120) as resp:
                data = json.loads(resp.read())
                markdown = data.get("data", {}).get("markdown", "")
                screenshot_url = data.get("data", {}).get("screenshot", "")
                screenshot_b64 = ""
                if screenshot_url and screenshot_url.startswith("http"):
                    try:
                        with urllib.request.urlopen(
                            screenshot_url, timeout=30
                        ) as sresp:
                            screenshot_b64 = (
                                "data:image/jpeg;base64,"
                                + base64.b64encode(sresp.read()).decode()
                            )
                    except Exception:
                        pass

                if len(markdown) > max_chars:
                    markdown = markdown[:max_chars] + "\n\n[... 内容已截断 ...]"

                return {
                    "success": True,
                    "markdown": markdown,
                    "html": "",
                    "screenshot": screenshot_b64,
                    "error": None,
                }

        return {
            "success": False,
            "markdown": "",
            "html": "",
            "screenshot": None,
            "error": "firecrawl 不可用（既无 CLI 也无 API key）",
        }

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
