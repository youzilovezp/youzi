# -*- coding: utf-8 -*-
"""
Playwright Adapter for /youzi skill

微软官方浏览器自动化（github.com/microsoft/playwright）
作为最后的 fallback（需登录/复杂交互场景）。

两种使用模式：
1. 本地 Python 库模式（youzi 自己跑 Playwright）
2. MCP server 模式（让 Claude Code 通过 MCP 调用）

依赖安装：
    pip install playwright
    playwright install chromium
"""
import asyncio
import os
import json
from typing import Optional


async def _scrape(
    url: str,
    wait_selector: Optional[str] = None,
    screenshot_path: Optional[str] = None,
    extract_prompt: Optional[str] = None,
    timeout: int = 30000,
) -> dict:
    """异步抓取单个 URL（需要登录或复杂交互时用）。

    Args:
        url: 目标 URL
        wait_selector: 等待某个 selector 出现再返回（用于 SPA 加载完成）
        screenshot_path: 保存截图到本地路径
        extract_prompt: 用 LLM 提取结构化字段
        timeout: 超时（毫秒）

    Returns:
        {
            "html": str,
            "text": str,
            "screenshot": str | None,   # 文件路径
            "extracted": dict | None,
            "success": bool,
            "error": str | None,
        }
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {
            "success": False,
            "error": "playwright 未安装。运行: pip install playwright && playwright install chromium",
            "html": "",
            "text": "",
            "screenshot": None,
            "extracted": None,
        }

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-setuid-sandbox"],
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (youzi-playwright/1.0)",
                viewport={"width": 1920, "height": 1080},
            )
            page = await context.new_page()

            # 设置超时
            page.set_default_timeout(timeout)

            # 访问
            await page.goto(url, wait_until="domcontentloaded")

            # 等待特定元素（可选）
            if wait_selector:
                try:
                    await page.wait_for_selector(wait_selector, timeout=timeout)
                except Exception:
                    # 超时也继续（返回当前内容）
                    pass

            # 提取内容
            html = await page.content()
            text = await page.evaluate("() => document.body.innerText")

            # 截图
            screenshot_file = None
            if screenshot_path:
                await page.screenshot(path=screenshot_path, full_page=True)
                screenshot_file = screenshot_path

            # LLM 提取（可选，需要调用 LLM API）
            extracted = None
            if extract_prompt:
                api_key = os.environ.get("ANTHROPIC_API_KEY")
                if api_key:
                    try:
                        import urllib.request
                        # 用 Claude API 提取
                        req_data = {
                            "model": "claude-3-5-sonnet-20241022",
                            "max_tokens": 4000,
                            "messages": [{
                                "role": "user",
                                "content": f"""{extract_prompt}

网页内容（前 8000 字符）：
{text[:8000]}"""
                            }],
                            "tools": [{
                                "name": "extract_data",
                                "description": "Extract structured data based on the prompt",
                                "input_schema": {
                                    "type": "object",
                                    "properties": {},
                                    "additionalProperties": True
                                }
                            }],
                            "tool_choice": {"type": "tool", "name": "extract_data"},
                        }
                        req = urllib.request.Request(
                            "https://api.anthropic.com/v1/messages",
                            data=json.dumps(req_data).encode(),
                            headers={
                                "x-api-key": api_key,
                                "anthropic-version": "2023-06-01",
                                "content-type": "application/json",
                            },
                        )
                        with urllib.request.urlopen(req, timeout=60) as resp:
                            result = json.loads(resp.read())
                            tool_input = result.get("content", [{}])[0].get("input", {})
                            extracted = tool_input
                    except Exception as e:
                        extracted = {"_error": f"LLM extract failed: {e}"}

            await browser.close()

            return {
                "success": True,
                "error": None,
                "html": html,
                "text": text,
                "screenshot": screenshot_file,
                "extracted": extracted,
            }

    except Exception as e:
        return {
            "success": False,
            "error": f"playwright 抓取失败: {type(e).__name__}: {e}",
            "html": "",
            "text": "",
            "screenshot": None,
            "extracted": None,
        }


def scrape(
    url: str,
    wait_selector: Optional[str] = None,
    screenshot_path: Optional[str] = None,
    extract_prompt: Optional[str] = None,
    timeout: int = 30000,
) -> dict:
    """同步接口（兼容已有 event loop）。"""
    try:
        loop = asyncio.get_running_loop()
        future = asyncio.run_coroutine_threadsafe(
            _scrape(url, wait_selector, screenshot_path, extract_prompt, timeout), loop
        )
        return future.result(timeout=timeout / 1000 + 10)
    except RuntimeError:
        return asyncio.run(_scrape(url, wait_selector, screenshot_path, extract_prompt, timeout))


def is_available() -> bool:
    """检查 playwright 是否安装 + 浏览器是否下载。"""
    try:
        from playwright.async_api import async_playwright  # noqa
        return True
    except ImportError:
        return False


# ============================================================
# MCP server 模式（让 Claude Code 通过 MCP 调用）
# ============================================================
MCP_CONFIG = {
    "mcpServers": {
        "playwright": {
            "command": "npx",
            "args": ["-y", "@playwright/mcp@latest"],
            "env": {},
        }
    }
}


def print_mcp_setup():
    """打印 MCP 配置（让用户加到 ~/.claude/settings.json）。"""
    print("# 把以下内容加到 ~/.claude/settings.json 的 mcpServers 中：")
    print(json.dumps(MCP_CONFIG, indent=2))


if __name__ == "__main__":
    if is_available():
        print("✓ Playwright 可用")
        result = scrape("https://example.com", timeout=10000)
        print(f"success: {result['success']}")
        if result["success"]:
            print(f"text length: {len(result['text'])}")
    else:
        print("✗ Playwright 未安装")
        print("安装: pip install playwright && playwright install chromium")
    print()
    print("MCP server 模式：")
    print_mcp_setup()
