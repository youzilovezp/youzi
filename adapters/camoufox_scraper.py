# -*- coding: utf-8 -*-
"""
Camoufox Adapter for /youzi skill

Camoufox (github.com/daijro/camoufox) — 基于 Firefox 的隐身浏览器自动化
反爬能力比 Playwright / Selenium 强 10×：
- 浏览器指纹随机化（canvas / WebGL / audio / font）
- TLS 指纹混淆（抵抗 Cloudflare / DataDome / fingerprint.com）
- 行为模拟（鼠标轨迹 + 滚动模式）

适合被 Cloudflare 拦截的竞品站、需要登录的复杂 SPA。

依赖：
    pip install camoufox
    camoufox fetch        # 下载 Firefox 二进制
"""

import asyncio
from typing import Any, Dict, Optional


async def _scrape(
    url: str,
    wait_selector: Optional[str] = None,
    screenshot_path: Optional[str] = None,
    extract_prompt: Optional[str] = None,
    timeout: int = 30000,
) -> Dict[str, Any]:
    """异步抓取单个 URL（高反爬场景 / 隐身需求）。"""
    try:
        # 避免 Pyright 把 AsyncCamoufox 当成模块（camoufox 没装时无法解析）
        from camoufox.async_api import AsyncCamoufox as _AsyncCamoufox  # type: ignore[import-not-found,misc]
        AsyncCamoufox = _AsyncCamoufox
    except ImportError:
        return {
            "success": False,
            "error": "camoufox 未安装。运行: pip install camoufox && camoufox fetch",
            "html": "",
            "text": "",
            "screenshot": None,
            "extracted": None,
        }

    try:
        async with AsyncCamoufox(headless=True) as browser:  # type: ignore[call-arg]
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (youzi-camoufox/1.0)",
                viewport={"width": 1920, "height": 1080},
                # 开启指纹随机化（camoufox 默认就开启）
                geoip=True,
            )
            page = await context.new_page()
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

            # 转 markdown（与其他 adapter 一致）
            try:
                import markdownify as _md

                markdown = _md.markdownify(
                    html, heading_style="ATX", strip=["script", "style"]
                )
            except Exception:
                markdown = text

            # 截图
            screenshot_file = None
            if screenshot_path:
                await page.screenshot(path=screenshot_path, full_page=True)
                screenshot_file = screenshot_path

            # LLM 提取（与 playwright_scraper 一致）
            extracted: Optional[Dict[str, Any]] = None
            if extract_prompt:
                import os
                import json
                import urllib.request

                api_key = os.environ.get("ANTHROPIC_API_KEY")
                if api_key:
                    try:
                        req_data = {
                            "model": "claude-3-5-sonnet-20241022",
                            "max_tokens": 4000,
                            "messages": [
                                {
                                    "role": "user",
                                    "content": f"""{extract_prompt}

网页内容（前 8000 字符）：
{text[:8000]}""",
                                }
                            ],
                            "tools": [
                                {
                                    "name": "extract_data",
                                    "description": "Extract structured data based on the prompt",
                                    "input_schema": {
                                        "type": "object",
                                        "properties": {},
                                        "additionalProperties": True,
                                    },
                                }
                            ],
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
                            tool_input = (
                                result.get("content", [{}])[0].get("input", {})
                            )
                            extracted = tool_input
                    except Exception as e:
                        extracted = {"_error": f"LLM extract failed: {e}"}

            await context.close()

            return {
                "success": True,
                "error": None,
                "html": html,
                "text": text,
                "markdown": markdown,
                "screenshot": screenshot_file,
                "extracted": extracted,
            }

    except Exception as e:
        return {
            "success": False,
            "error": f"camoufox 抓取失败: {type(e).__name__}: {e}",
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
) -> Dict[str, Any]:
    """同步接口（兼容已有 event loop）。"""
    try:
        loop = asyncio.get_running_loop()
        future = asyncio.run_coroutine_threadsafe(
            _scrape(url, wait_selector, screenshot_path, extract_prompt, timeout),
            loop,
        )
        return future.result(timeout=timeout / 1000 + 10)
    except RuntimeError:
        return asyncio.run(
            _scrape(url, wait_selector, screenshot_path, extract_prompt, timeout)
        )


def is_available() -> bool:
    """检查 camoufox 是否安装。"""
    try:
        __import__("camoufox.async_api")
        return True
    except ImportError:
        return False


if __name__ == "__main__":
    if is_available():
        print("✓ Camoufox 可用")
        result = scrape("https://example.com", timeout=10000)
        print(f"  success: {result['success']}")
        if result["success"]:
            print(f"  text length: {len(result['text'])}")
    else:
        print("✗ Camoufox 未安装")
        print("  安装: pip install camoufox && camoufox fetch")