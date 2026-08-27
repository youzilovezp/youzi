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
import re as _re
from typing import Optional

# Next.js/Nuxt 水合脚本、webpack chunk、JSON payload 的行特征
# (markdownify 的 strip=["script"] 挡不住这些漏到正文)
_JS_PAYLOAD_LINE_RX = _re.compile(
    r"self\.\s*_?\_*next_?\w*\s*\.?push|self\.__next_f"
    r"|\.push\(\s*\[\s*\d|webpackChunk|__NUXT__|{\"@|\\n\\n|\\\\n"
    r"|^\s*\(self\.|\"sourceMappingURL\"|data:text/javascript",
    _re.I,
)

# 定价页 URL 特征(与 adapters.classify_url 的 pricing 语义一致,本地版避免循环依赖)
_PRICING_URL_RX = _re.compile(r"pricing|price|plans?/|定价", _re.I)
# 货币价格模式(等待 JS 注入的目标)
_PRICE_RX = _re.compile(r"(?:US\$|\$|€|£|₹|¥)\s?\d[\d,\.]*|Rs\.?\s?\d", _re.I)


def _is_pricing_url(url: str) -> bool:
    return bool(_PRICING_URL_RX.search(url or ""))


def _url_scheme_host(url: str) -> str:
    """https://www.wati.io/pricing/ → https://www.wati.io"""
    m = _re.match(r"^(https?://[^/]+)", url or "")
    return m.group(1) if m else ""


def _strip_js_payload_lines(markdown: str) -> str:
    """删掉 JS payload 行,保留渲染后的可见内容。"""
    if not markdown:
        return markdown
    return "\n".join(
        ln for ln in markdown.split("\n") if not _JS_PAYLOAD_LINE_RX.search(ln)
    )


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
                # 真实 Chrome UA:自定义 UA(youzi-playwright/1.0)会被
                # WATI 等站的 geo-IP 定价接口识别为 bot,返回无价变体
                # (实测 3/3 换 UA 后主价 $59/$119/$279 全部注入)
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
            )
            page = await context.new_page()

            # 设置超时
            page.set_default_timeout(timeout)

            # 定价页先访问同域首页:WATI 等站的 geo-IP 定价接口需要
            # 首页种下的会话 cookie,冷请求直接进定价页会被发无价变体
            # (实测:先访问首页 → 主价 $59/$119/$279 全部注入)
            if _is_pricing_url(url):
                try:
                    root = f"{_url_scheme_host(url)}/"
                    await page.goto(
                        root, wait_until="domcontentloaded", timeout=timeout
                    )
                    await page.wait_for_timeout(1000)
                except Exception:
                    pass  # 首页失败不阻断,继续按原路径试

            # 访问
            await page.goto(url, wait_until="domcontentloaded")

            # 等待特定元素（可选）
            if wait_selector:
                try:
                    await page.wait_for_selector(wait_selector, timeout=timeout)
                except Exception:
                    # 超时也继续（返回当前内容）
                    pass

            # 定价页价格等待:WATI 类站点价格由 geo-IP API 延迟注入,
            # 且分批($24 addon 是服务端渲染一开始就在,$59 主价 2-8s
            # 后注入)。addon 价立即可见 → 「集合稳定」会在主价注入前
            # 误判完成。先固定等 5s 让注入器跑,再判稳定(连续两次采样
            # 相同),上限 18s
            if _is_pricing_url(url):
                await page.wait_for_timeout(5000)
                prev_prices: set = set()
                stable = 0
                for _ in range(26):
                    body_txt = await page.evaluate("() => document.body.innerText")
                    cur = set(_PRICE_RX.findall(body_txt or ""))
                    if cur and cur == prev_prices:
                        stable += 1
                        if stable >= 2:
                            break
                    else:
                        stable = 0
                    prev_prices = cur
                    await page.wait_for_timeout(500)
            else:
                await page.wait_for_timeout(1500)

            # 提取内容前:定价页尝试点击「年付/Annually」计费切换,捕获
            # 两种计费态的价格(YCloud 事故:默认渲染 Monthly,年付 $468
            # 在 toggle 后面 —— 只抓默认态 = 年价全丢)。
            _annual_extra = ""
            if _is_pricing_url(url):
                try:
                    _monthly_text = await page.evaluate("() => document.body.innerText")
                    _clicked = await page.evaluate(
                        """() => {
                          const rx = /annually|annual|yearly|按年|年付/i;
                          const els = Array.from(document.querySelectorAll(
                            "button, [role='tab'], label, a, span, div, li"
                          ));
                          for (const e of els) {
                            const t = (e.innerText || '').trim();
                            if (t && t.length <= 22 && rx.test(t)
                                && !/month|月付|每月|billed/i.test(t)) {
                              e.click();
                              return t;
                            }
                          }
                          return null;
                        }"""
                    )
                    if _clicked:
                        await page.wait_for_timeout(2500)
                        _annual_text = await page.evaluate(
                            "() => document.body.innerText"
                        )
                        _p_month = set(_PRICE_RX.findall(_monthly_text or ""))
                        _p_annual = set(_PRICE_RX.findall(_annual_text or ""))
                        if _p_annual and _p_annual != _p_month:
                            # 变体文本行级过滤:孤立价格行(纯价格数字、
                            # 无套餐词/周期/长度)只制造假档 —— 它们形成
                            # 单 token 簇挤掉默认态的融合价格行(Sleekflow
                            # 事故:US$399 孤立行挤掉真实 US$349)
                            _keep = []
                            for _ln in (_annual_text or "").split("\n"):
                                _s = _ln.strip()
                                if not _s:
                                    continue
                                _bare_price = _PRICE_RX.fullmatch(
                                    _s.replace(" ", "")
                                ) or (
                                    len(_s) < 14
                                    and _PRICE_RX.search(_s)
                                    and not _re.search(
                                        r"/|month|year|annual|billed|pro|ai|premium|team|starter|growth|business|enterprise",
                                        _s,
                                        _re.I,
                                    )
                                )
                                if _bare_price:
                                    continue
                                _keep.append(_s)
                            _annual_extra = (
                                "\n\n<!-- annual-billing variant (toggled) -->\n"
                                + "\n".join(_keep)
                            )
                except Exception:
                    pass  # 切换失败不影响默认态抓取

            # 提取内容
            html = await page.content()
            text = await page.evaluate("() => document.body.innerText")
            # 重定向落点要在 browser.close() 前取(报告链接用,跳板路径不再误人)
            final_url = page.url
            # 转 markdown(与其他 adapter 一致)— 用 markdownify 降级
            try:
                import markdownify as _md

                markdown = _md.markdownify(
                    html, heading_style="ATX", strip=["script", "style"]
                )
            except Exception:
                markdown = text  # 降级用纯文本
            # strip 参数挡不住 Next.js 水合脚本漏出(markdownify 已知行为),
            # 逐行清洗 JS payload —— 否则整份 markdown 被 __next_f 垃圾污染,
            # 质量分暴跌,playwright 渲染 SPA 的成果被 merge 层错杀
            # (Meetbot 事故:69 条干净行被 JS 行拖死,primary 落到空爬的引擎)
            markdown = _strip_js_payload_lines(markdown)

            # 年付变体附加(切换后捕获的完整渲染文本,含 toggle 后价格)
            if _annual_extra:
                markdown += _annual_extra
                text = (text or "") + _annual_extra

            # 定价页 markdown 无价而渲染文本有价 → 用渲染后可见文本兜底。
            # WATI 事故:价格 span 在区域 CSS 变体里,markdownify 转换丢弃,
            # markdown 只剩 addon 价($24/$69)没有主价($59/$119/$279);
            # 判定标准 = 独立价格数字集合,text 比 markdown 多就换
            # (innerText 是浏览器真实渲染结果,含 "$59\nmonth\nbilled annually")
            if _is_pricing_url(url) and markdown and text:
                _md_prices = {m.group(0) for m in _PRICE_RX.finditer(markdown)}
                _txt_prices = {m.group(0) for m in _PRICE_RX.finditer(text)}
                if _txt_prices - _md_prices:
                    text_clean = "\n".join(
                        ln.strip() for ln in text.split("\n") if ln.strip()
                    )
                    markdown = (
                        f"<!-- playwright rendered-text fallback "
                        f"(markdownify dropped price nodes) -->\n{text_clean}"
                    )

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
                "markdown": markdown,
                "screenshot": screenshot_file,
                "extracted": extracted,
                # 重定向后落点 —— 报告链接用 final_url,跳板路径
                # (wati.io/product → /product-overview/)不再让读者觉得"不准"
                "final_url": final_url,
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
        return asyncio.run(
            _scrape(url, wait_selector, screenshot_path, extract_prompt, timeout)
        )


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
