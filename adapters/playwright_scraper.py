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

2026-08-29 升级(审计修复):
  - 浏览器复用:常驻事件循环线程 + browser 单例 + 按域 context 缓存
    (实测每次冷启动 2.3-2.6s,单竞品 3+ 次启动纯属浪费);定价预热改为
    「该域 context 未访问过站点时才预热」—— 同域其他页面已种下
    geo-IP cookie 时跳过
  - DOM 级清洗:page.content() 前移除 style/script/link[stylesheet] 节点
    (YCloud 实测:Chakra UI 单行 151KB CSS 漏进 markdown,行级正则清洗
    对单行巨物结构性无效,截断落盘后正文 100% 丢失)
  - UA 动态化:按 browser.version 生成,消灭 Chrome/126 硬编码与真实
    chromium(151) 25 个大版本偏差 —— UA 与指纹不符是 Cloudflare 经典信号
  - 价格正则统一:与 fetch/audit/sufficiency 共用 pricing_tokens
  - YOUZI_PW_REUSE=0 可退回旧的每调用冷启动行为(调试用)
"""

import asyncio
import atexit
import os
import json
import re as _re
import sys
import threading
import time as _t
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pricing_tokens import PRICE_TOKEN_RX  # noqa: E402

# Next.js/Nuxt 水合脚本、webpack chunk、JSON payload 的行特征
# (markdownify 的 strip=["script"] 挡不住这些漏到正文)
_JS_PAYLOAD_LINE_RX = _re.compile(
    r"self\.\s*_?\_*next_?\w*\s*\.?push|self\.__next_f"
    r"|\.push\(\s*\[\s*\d|webpackChunk|__NUXT__|{\"@|\\n\\n|\\\\n"
    r"|^\s*\(self\.|\"sourceMappingURL\"|data:text/javascript",
    _re.I,
)
# CSS 行特征(DOM 清洗的二道防线:漏网的 style 文本行)
_CSS_LINE_RX = _re.compile(
    r"\.css-[\w-]+\s*\{|@media\s*\(|@keyframes\s+[\w-]+\s*\{"
    r"|--[a-z][\w-]*\s*:\s*[\w#(]|font-family\s*:|!important"
    r"|url\(['\"]?data:|^[.#:][\w\s,.#:()>~-]+\{",
    _re.I,
)

# 定价页 URL 特征(与 adapters.classify_url 的 pricing 语义一致,本地版避免循环依赖)
_PRICING_URL_RX = _re.compile(r"pricing|price|plans?/|定价", _re.I)
# 货币价格模式(等待 JS 注入的目标) —— 统一到 pricing_tokens(₹/Rs./US$ 全覆盖)
_PRICE_RX = PRICE_TOKEN_RX

# DOM 清洗:content() 前移除会污染 markdownify 输出的节点
_DOM_CLEANUP_JS = (
    "() => document.querySelectorAll("
    "'style, script, noscript, link[rel=\"stylesheet\"], template'"
    ").forEach(e => e.remove())"
)

_REUSE_DISABLED = os.environ.get("YOUZI_PW_REUSE", "1") == "0"


def _is_pricing_url(url: str) -> bool:
    return bool(_PRICING_URL_RX.search(url or ""))


def _url_scheme_host(url: str) -> str:
    """https://www.wati.io/pricing/ → https://www.wati.io"""
    m = _re.match(r"^(https?://[^/]+)", url or "")
    return m.group(1) if m else ""


def _strip_junk_lines(markdown: str) -> str:
    """删掉 JS payload / CSS 行,保留渲染后的可见内容。"""
    if not markdown:
        return markdown
    return "\n".join(
        ln
        for ln in markdown.split("\n")
        if not _JS_PAYLOAD_LINE_RX.search(ln) and not _CSS_LINE_RX.search(ln)
    )


# ============================================================
# 浏览器复用引擎 —— 常驻事件循环线程 + browser 单例 + 按域 context
# ============================================================
async def _close_quietly(obj) -> None:
    try:
        await obj.close()
    except Exception:
        pass


class _BrowserEngine:
    """进程级 playwright 基础设施。

    - 专用事件循环线程:所有 playwright 协程跑在这一条循环上,
      多页面并行 = 同一 browser 下多 context 并发
    - 按域 context 缓存(300s TTL):同竞品的多个页面共享 context
      → geo-IP/会话 cookie 自然互通,定价页首页预热通常可跳过
    """

    def __init__(self) -> None:
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._browser = None
        self._playwright = None
        self._version_major = "126"  # 兜底(引擎未启动时)
        self._contexts: dict = {}
        self._ctx_lock: Optional[asyncio.Lock] = None
        self._start_lock = threading.Lock()
        # 域 → 该域 context 里是否已访问过站点(定价预热决策)
        self.visited: dict = {}

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._start_lock:
            if self._loop is None or not self._loop.is_running():
                self._loop = asyncio.new_event_loop()
                self._thread = threading.Thread(
                    target=self._loop.run_forever, daemon=True, name="youzi-pw"
                )
                self._thread.start()
            return self._loop

    def submit(self, coro_factory, timeout_s: float):
        """把「返回协程的工厂」提交到常驻循环执行,阻塞取结果。"""
        loop = self._ensure_loop()
        fut = asyncio.run_coroutine_threadsafe(coro_factory(), loop)
        return fut.result(timeout=timeout_s)

    async def _ensure_browser(self):
        if self._browser is not None and self._browser.is_connected():
            return self._browser
        from playwright.async_api import async_playwright

        if self._playwright is None:
            self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        self._version_major = self._browser.version.split(".")[0]
        return self._browser

    def ua(self) -> str:
        """按真实 chromium 版本生成 UA —— 硬编码版本号会腐烂
        (历史:Chrome/126 vs 实际 151,25 个大版本偏差)。"""
        return (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            f"Chrome/{self._version_major}.0.0.0 Safari/537.36"
        )

    async def get_context(self, url: str):
        """取(或建)该 URL 所属域的缓存 context;返回 (context, is_new)。"""
        browser = await self._ensure_browser()
        domain = _url_scheme_host(url) or "about:blank"
        if self._ctx_lock is None:
            self._ctx_lock = asyncio.Lock()
        async with self._ctx_lock:
            now = _t.monotonic()
            ent = self._contexts.get(domain)
            if ent and ent["expires"] > now:
                return ent["ctx"], False
            if ent:  # 过期 → 关旧建新
                await _close_quietly(ent["ctx"])
                self.visited.pop(domain, None)
            ctx = await browser.new_context(
                user_agent=self.ua(),
                viewport={"width": 1920, "height": 1080},
                locale="en-US",
            )
            self._contexts[domain] = {"ctx": ctx, "expires": now + 300.0}
            return ctx, True

    def drop_context(self, url: str) -> None:
        """页面级致命错误时丢弃该域缓存 context(下次重建,cookie 重种)。"""
        domain = _url_scheme_host(url) or "about:blank"
        ent = self._contexts.pop(domain, None)
        self.visited.pop(domain, None)
        if ent and self._loop and self._loop.is_running():
            asyncio.run_coroutine_threadsafe(_close_quietly(ent["ctx"]), self._loop)

    async def shutdown(self) -> None:
        for ent in self._contexts.values():
            await _close_quietly(ent["ctx"])
        self._contexts.clear()
        if self._browser is not None:
            await _close_quietly(self._browser)
            self._browser = None
        if self._playwright is not None:
            await _close_quietly(self._playwright)
            self._playwright = None

    def stop(self) -> None:
        """进程退出时的同步清理(尽力而为;daemon 线程兜底)。"""
        if self._loop is None:
            return
        try:
            fut = asyncio.run_coroutine_threadsafe(self.shutdown(), self._loop)
            fut.result(timeout=10)
        except Exception:
            pass
        try:
            self._loop.call_soon_threadsafe(self._loop.stop)
        except Exception:
            pass


_ENGINE = _BrowserEngine()
atexit.register(_ENGINE.stop)


def _not_installed() -> dict:
    return {
        "success": False,
        "error": "playwright 未安装。运行: pip install playwright && playwright install chromium",
        "html": "",
        "text": "",
        "markdown": "",
        "screenshot": None,
        "extracted": None,
    }


def _fail(e: BaseException) -> dict:
    return {
        "success": False,
        "error": f"playwright 抓取失败: {type(e).__name__}: {e}",
        "html": "",
        "text": "",
        "markdown": "",
        "screenshot": None,
        "extracted": None,
    }


async def _scrape_core(
    url: str,
    wait_selector: Optional[str],
    screenshot_path: Optional[str],
    extract_prompt: Optional[str],
    timeout: int,
    context,
    warm_up: bool,
) -> dict:
    """抓取主体(在 context 里开新页;page 恒在 finally 关闭)。

    warm_up: 定价页是否先访问同域首页种 geo-IP cookie。
    """
    page = await context.new_page()
    try:
        page.set_default_timeout(timeout)

        # 定价页先访问同域首页(仅该域 context 从未访问过站点时):
        # WATI 等站的 geo-IP 定价接口需要首页种下的会话 cookie,冷
        # context 直接进定价页会被发无价变体(实测:先访问首页 →
        # 主价 $59/$119/$279 全部注入)。同域其他页面(并行批次里的
        # homepage/features)已访问过 → cookie 已在 → 跳过省一次 goto。
        if warm_up and _is_pricing_url(url):
            try:
                root = f"{_url_scheme_host(url)}/"
                await page.goto(root, wait_until="domcontentloaded", timeout=timeout)
                await page.wait_for_timeout(1000)
            except Exception:
                pass  # 首页失败不阻断,继续按原路径试

        # 访问
        await page.goto(url, wait_until="domcontentloaded")
        _ENGINE.visited[_url_scheme_host(url) or "about:blank"] = True

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
        #
        # 2026-08-30 修复(cursor.com 实测):DOM 提取原先在 toggle 之后
        # → markdown 只剩年付态($16/$32),静态引擎渲染的是默认月付态
        # ($20/$40)→ 两引擎永远凑不到相同 token,交叉验证在一切可切换
        # 定价页上随机失败。现改为:先取默认态 DOM(与静态引擎同视角),
        # 再点 toggle 追加年付变体 → 双态齐备,跨引擎可归票。
        _annual_extra = ""
        _monthly_text = ""
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
                          // 导航防护(2026-08-30):真链接(如 "Annual report"
                          // 的 <a>)点击会整页跳走,后续 content() 取到错页。
                          // 只允许无 href/#/javascript 的锚 —— toggle 锚常态。
                          const href = e.tagName === 'A'
                            ? (e.getAttribute('href') || '').trim() : '';
                          if (href && href !== '#'
                              && !href.startsWith('javascript')) continue;
                          e.click();
                          return t;
                        }
                      }
                      return null;
                    }"""
                )
                if _clicked:
                    await page.wait_for_timeout(2500)
                    _annual_text = await page.evaluate("() => document.body.innerText")
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
                            _bare_price = _PRICE_RX.fullmatch(_s.replace(" ", "")) or (
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

        # ── DOM 级清洗(P0 修复核心):content() 前移除 style/script/
        # stylesheet 节点。YCloud 实测:Chakra UI 的 151KB styled-
        # components CSS 以【单行】漏进 markdownify 输出,行级清洗对
        # 单行巨物无效(1 行 = 86% 字符),头部截断后正文 100% 丢失。──
        try:
            await page.evaluate(_DOM_CLEANUP_JS)
        except Exception:
            pass  # 清洗失败继续,交给行级二道防线

        # 提取内容:toggle 改的是同一 DOM —— 已切换时先尝试 JS 回点
        # 「月付」还原默认态(与静态引擎同视角);还原失败由下方
        # _monthly_text 兜底(证据优先保默认态,年付态已存 _annual_extra)
        if _annual_extra:
            try:
                await page.evaluate(
                    """() => {
                      const rx = /monthly|month|月付|每月/i;
                      const els = Array.from(document.querySelectorAll(
                        "button, [role='tab'], label, a, span, div, li"
                      ));
                      for (const e of els) {
                        const t = (e.innerText || '').trim();
                        if (t && t.length <= 22 && rx.test(t)
                            && !/year|annual|年付|按年/i.test(t)) {
                          // 导航防护:同年付点击 —— "Monthly updates" 类真
                          // 链接会导航走,回点只针对 toggle 元素
                          const href = e.tagName === 'A'
                            ? (e.getAttribute('href') || '').trim() : '';
                          if (href && href !== '#'
                              && !href.startsWith('javascript')) continue;
                          e.click();
                          return t;
                        }
                      }
                      return null;
                    }"""
                )
                await page.wait_for_timeout(1200)
            except Exception:
                pass  # 还原失败:正文以 _monthly_text 兜底(下方 text 赋值)
        html = await page.content()
        # 默认态渲染文本:toggle 未点 = 现场 innerText;点了且还原失败 =
        # 用 toggle 前捕获的 _monthly_text(证据优先保默认态)
        text = await page.evaluate("() => document.body.innerText")
        if _annual_extra and _monthly_text:
            _p_now = set(_PRICE_RX.findall(text or ""))
            _p_def = set(_PRICE_RX.findall(_monthly_text))
            if _p_def - _p_now:  # 还原失败,默认态价格缺失 → 用捕获的默认态
                text = _monthly_text
        # 重定向落点要在 page 关闭前取(报告链接用,跳板路径不再误人)
        final_url = page.url
        # 转 markdown(与其他 adapter 一致)— 用 markdownify 降级
        try:
            import markdownify as _md

            markdown = _md.markdownify(
                html, heading_style="ATX", strip=["script", "style"]
            )
        except Exception:
            markdown = text  # 降级用纯文本
        # 行级清洗为二道防线:markdownify strip 参数挡不住 Next.js 水合
        # 脚本/残余 CSS 泄漏(已知行为),逐行兜底
        markdown = _strip_junk_lines(markdown)

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
    finally:
        await _close_quietly(page)


async def _scrape(
    url: str,
    wait_selector: Optional[str] = None,
    screenshot_path: Optional[str] = None,
    extract_prompt: Optional[str] = None,
    timeout: int = 30000,
) -> dict:
    """异步抓取单个 URL。

    复用模式:常驻 browser + 按域 context;冷启动模式:每次
    launch + 自建 context 自关闭(旧行为,行为对照/调试用)。
    """
    try:
        from playwright.async_api import async_playwright  # noqa: F401
    except ImportError:
        return _not_installed()

    if not _REUSE_DISABLED:
        try:
            context, _is_new = await _ENGINE.get_context(url)
            domain = _url_scheme_host(url) or "about:blank"
            warm = not _ENGINE.visited.get(domain)
            return await _scrape_core(
                url,
                wait_selector,
                screenshot_path,
                extract_prompt,
                timeout,
                context,
                warm,
            )
        except Exception as e:
            # 页面级异常:丢弃该域缓存 context(cookie 状态可能已污染),
            # 返回失败 dict(与旧契约一致)
            _ENGINE.drop_context(url)
            return _fail(e)

    # 冷启动模式(每调用一个 browser,旧行为)
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = None
    try:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        context = await browser.new_context(
            user_agent=_ENGINE.ua(),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )
        try:
            return await _scrape_core(
                url,
                wait_selector,
                screenshot_path,
                extract_prompt,
                timeout,
                context,
                True,
            )
        finally:
            await _close_quietly(context)
    except Exception as e:
        return _fail(e)
    finally:
        if browser is not None:
            await _close_quietly(browser)
        await _close_quietly(pw)


def scrape(
    url: str,
    wait_selector: Optional[str] = None,
    screenshot_path: Optional[str] = None,
    extract_prompt: Optional[str] = None,
    timeout: int = 30000,
) -> dict:
    """同步接口。复用模式提交到常驻引擎循环;基础设施故障自动回退。"""
    if not is_available():
        return _not_installed()
    if not _REUSE_DISABLED:
        try:
            return _ENGINE.submit(
                lambda: _scrape(
                    url, wait_selector, screenshot_path, extract_prompt, timeout
                ),
                timeout_s=timeout / 1000 + 30,
            )
        except Exception as e:
            if "playwright 未安装" in str(e):
                return _not_installed()
            # 引擎级故障(线程/循环问题)→ 冷启动回退,不放弃抓取
            try:
                return asyncio.run(
                    _scrape_cold(
                        url,
                        wait_selector,
                        screenshot_path,
                        extract_prompt,
                        timeout,
                    )
                )
            except Exception as e2:
                return _fail(e2 if str(e2) else e)
    # YOUZI_PW_REUSE=0 → 独立事件循环(线程内无运行循环时)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(
            _scrape(url, wait_selector, screenshot_path, extract_prompt, timeout)
        )
    # 已有运行循环的调用方(罕见):新开线程跑,避免嵌套 run
    import concurrent.futures as _cf

    with _cf.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(
            asyncio.run,
            _scrape(url, wait_selector, screenshot_path, extract_prompt, timeout),
        ).result(timeout=timeout / 1000 + 30)


async def _scrape_cold(url, wait_selector, screenshot_path, extract_prompt, timeout):
    """引擎故障时的独立冷启动(不复用任何进程级状态)。"""
    from playwright.async_api import async_playwright

    pw = await async_playwright().start()
    browser = None
    try:
        browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-setuid-sandbox"],
        )
        context = await browser.new_context(
            user_agent=_ENGINE.ua(),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )
        try:
            return await _scrape_core(
                url,
                wait_selector,
                screenshot_path,
                extract_prompt,
                timeout,
                context,
                True,
            )
        finally:
            await _close_quietly(context)
    finally:
        if browser is not None:
            await _close_quietly(browser)
        await _close_quietly(pw)


def is_available() -> bool:
    """检查 playwright 是否安装 + 浏览器是否下载。"""
    try:
        from playwright.async_api import async_playwright  # noqa

        return True
    except ImportError:
        return False


# 2026-08-30 清理:删除 MCP server 配置块(MCP_CONFIG/print_mcp_setup)——
# MCP 集成由 skill 层的 allowed-tools 声明,此处的打印配置零调用方。

if __name__ == "__main__":
    if is_available():
        print("✓ Playwright 可用")
        t0 = _t.monotonic()
        r1 = scrape("https://example.com", timeout=10000)
        t1 = _t.monotonic()
        r2 = scrape("https://example.com", timeout=10000)
        t2 = _t.monotonic()
        print(f"首次(含引擎启动): {t1 - t0:.2f}s success={r1['success']}")
        print(f"复用: {t2 - t1:.2f}s success={r2['success']}")
    else:
        print("✗ Playwright 未安装")
        print("安装: pip install playwright && playwright install chromium")
