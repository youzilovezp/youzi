# -*- coding: utf-8 -*-
"""
/youzi skill · 爬虫 adapter 集合

设计理念：并行 + 智能合并

每个 adapter 实现统一的 scrape(url, **kwargs) → dict 接口。
统一入口 scrape_smart() 自动并行调用爬虫 + 合并去重结果。

V2 引擎白名单（2026-08-27 重构，13 → 5，依据 engine-stats n=700+）：

  🔒 商业（需 FIRECRAWL_API_KEY，有 key 时插组合首位）：
    1. firecrawl         —— 96% 网页覆盖 + JS 重度 + 截图（业界最强）

  🆓 开源（本地运行）：
    2. playwright        —— JS 页引擎（定价页价格覆盖最稳）
    3. trafilatura       —— 静态正文抽取（docs 主力）
    4. newspaper3k       —— 文章型（blog/customer）
    5. jina              —— 轻量 URL→Markdown，第三方渲染交叉验证票

  ⚠ 质量分快照会腐烂:engine-stats.json 才是唯一事实源(2026-08-30 实测:
  playwright pricing q=0.38 / trafilatura 0.59 / newspaper3k 0.84-n3 仅
  幸存样本;562B 空壳可拿 q=0.76 —— q 度量洁净度不度量完整性,详见
  _merge_results 的定价页内容完整性维度)。引用历史数字必须带日期。
"""

import asyncio
import hashlib
import json
import os
import threading
import time as _time_mod
from typing import Dict, Any, Optional, List


def url_hash(url: str) -> str:
    """生成 URL 的短 hash（用于截图文件名 + dedup）。"""
    return hashlib.md5(url.encode()).hexdigest()[:8]


# ============================================================
# 智能 URL 路由 —— 按 URL 类型 + 特征自动选择最佳爬虫组合
# ============================================================
import re as _re_route  # noqa: E402

# URL 类型识别模式(按优先级匹配)
_URL_TYPE_PATTERNS = [
    # 文档站(技术文档,需要深读,trafilatura 最强)
    ("docs", _re_route.compile(r"^https?://docs?\.", _re_route.I)),
    ("docs", _re_route.compile(r"/docs?/", _re_route.I)),
    ("docs", _re_route.compile(r"/reference/", _re_route.I)),
    ("docs", _re_route.compile(r"/api[-_]?(docs|reference)", _re_route.I)),
    # Dashboard / App (登录后内容,必须 playwright)
    ("dashboard", _re_route.compile(r"^https?://app\.", _re_route.I)),
    ("dashboard", _re_route.compile(r"^https?://(console|panel|admin)\.", _re_route.I)),
    # 定价页 (JS 重,firecrawl/playwright 强)
    (
        "pricing",
        _re_route.compile(r"/pricing[-_]?(plan|plans|tier|tiers)?$", _re_route.I),
    ),
    ("pricing", _re_route.compile(r"/plan[-_]?s?$", _re_route.I)),
    ("pricing", _re_route.compile(r"/price", _re_route.I)),
    # 公司信息
    (
        "about",
        _re_route.compile(
            r"/(about|company|our[-_]?story|team|leadership|careers)", _re_route.I
        ),
    ),
    # 集成 / 合作伙伴
    ("integration", _re_route.compile(r"/integrations?(?:/|$)", _re_route.I)),
    ("integration", _re_route.compile(r"/marketplace(?:/|$)", _re_route.I)),
    # 客户案例
    (
        "customer",
        _re_route.compile(
            r"/(customer[-_]?stor(y|ies)|case[-_]?stud(y|ies))", _re_route.I
        ),
    ),
    # 博客 / 文章
    ("blog", _re_route.compile(r"/(blog|news|article|press|release)", _re_route.I)),
    # 产品 / 功能页
    (
        "feature",
        _re_route.compile(
            r"/(feature|features|product|capabilities|whatsapp[-_]?business[-_]?api)",
            _re_route.I,
        ),
    ),
    # changelog
    (
        "changelog",
        _re_route.compile(r"/(changelog|release[-_]?notes?|updates?)", _re_route.I),
    ),
]

# 各 URL 类型推荐的爬虫组合(按优先级,前几个 = 主力,后面 = 补充)。
# 设计原则:
#   - pricing: JS 渲染组(价格几乎都是前端渲染) + 1 个静态引擎做交叉对照
#     —— 双通道独立取证,是定价可信度判定的基础(≥2 独立引擎一致才 verified)
#   - docs/feature: 内容型页面,trafilatura 主力
#   - about/blog/customer: 静态文章型,轻量正文抽取器就够(省资源、快)
_URL_TYPE_SCRAPERS = {
    # V2 白名单(2026-08-27 重构,依据 engine-stats n=700+):
    #   playwright = JS 页王者(pricing q=0.50 / homepage q=0.42)
    #   trafilatura = 静态正文王者(docs q=0.57)
    #   newspaper3k = 文章型(blog/customer q=0.67)
    #   jina = 第三方渲染交叉验证票
    #   firecrawl 由 recommend_scrapers 动态插首(需 FIRECRAWL_API_KEY)
    "pricing": ["playwright", "trafilatura", "jina"],
    "docs": ["trafilatura", "jina"],
    "dashboard": ["playwright"],
    "about": ["trafilatura", "jina"],
    "integration": ["trafilatura", "jina"],
    "customer": ["newspaper3k", "trafilatura"],
    "blog": ["newspaper3k", "trafilatura"],
    "feature": ["playwright", "trafilatura", "jina"],
    "changelog": ["trafilatura", "jina"],
    "testimonials": ["trafilatura", "newspaper3k"],
    "homepage": ["playwright", "trafilatura", "jina"],
}

# 证据敏感页面:跨引擎"补充段落"会把不同引擎的碎片拼在一起(张冠李戴
# 温床),且拼接段落无法归属哪个引擎看到原文 → 全部证据页禁拼接。
# 各引擎完整原文始终保留在 all_results / manifest.engines_by_url。
_NO_SUPPLEMENT_TYPES = {
    "pricing",
    "feature",
    "docs",
    "about",
    "customer",
    "blog",
    "testimonials",
    "changelog",
    "integration",
    "homepage",
    "dashboard",
}

# 引擎历史统计文件(成功率 + 质量分,按 url_type 分桶) —— 智能路由的学习数据
_ENGINE_STATS_PATH = (
    __import__("pathlib").Path(__file__).resolve().parent.parent
    / "storage"
    / "engine-stats.json"
)
# 桶有效期:窗口外的历史数据按陈旧清除(V1 时代的 firecrawl CLI 402 噪声
# 曾与现状数据永久混桶 —— ok=0.12 的死通道拉低排序,白名单校准被污染)
_ENGINE_STATS_WINDOW_DAYS = 90.0

# 进程内缓存 + 锁:页面级并行/竞品级并行后,record/outcome 会从多线程
# 并发进入。历史实现每次全文件读+全文件写(实测单竞品 25 读 7 写),
# 既放大 IO 又在并发写时丢失更新(last-write-wins)。
_STATS_LOCK = threading.RLock()
_STATS_MEM: Optional[Dict[str, Any]] = None


def _prune_stale(stats: Dict[str, Any]) -> Dict[str, Any]:
    """清除窗口外/无时间戳(legacy)的桶 —— 质量口径变更后的自净迁移。"""
    cutoff = _time_mod.time() - _ENGINE_STATS_WINDOW_DAYS * 86400
    out: Dict[str, Any] = {}
    for eng, types in stats.items():
        if not isinstance(types, dict):
            continue
        for url_type, b in types.items():
            if not isinstance(b, dict) or "last" not in b:
                continue  # legacy 桶(2026-08-29 质量分修正前):度量失真,弃
            if b.get("last", 0) >= cutoff:
                out.setdefault(eng, {})[url_type] = b
    return out


def _load_engine_stats() -> Dict[str, Any]:
    global _STATS_MEM
    with _STATS_LOCK:
        if _STATS_MEM is not None:
            return _STATS_MEM
        try:
            raw = json.loads(_ENGINE_STATS_PATH.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        _STATS_MEM = _prune_stale(raw if isinstance(raw, dict) else {})
        return _STATS_MEM


def _save_engine_stats(stats: Dict[str, Any]) -> None:
    """原子写(tmp + os.replace):并发进程/崩溃不留下半截 JSON。"""
    try:
        _ENGINE_STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _ENGINE_STATS_PATH.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(stats, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        os.replace(tmp, _ENGINE_STATS_PATH)
    except Exception:
        pass  # 统计写失败不影响爬取


def record_engine_outcome(url_type: str, outcomes: Dict[str, Any]) -> None:
    """记录一轮爬取中各引擎的表现,供 recommend_scrapers 学习排序。

    outcomes: {engine_name: {"success": bool, "quality": float 0-1}}
    """
    now = _time_mod.time()
    with _STATS_LOCK:
        stats = _load_engine_stats()  # RLock 可重入,首记录时从磁盘载入
        for eng, oc in outcomes.items():
            bucket = stats.setdefault(eng, {}).setdefault(
                url_type, {"n": 0, "ok": 0, "q_sum": 0.0, "last": now}
            )
            bucket["n"] += 1
            bucket["last"] = now
            if oc.get("success"):
                bucket["ok"] += 1
            bucket["q_sum"] += float(oc.get("quality") or 0.0)
        _save_engine_stats(stats)


def _engine_stats_score(eng: str, url_type: str) -> Optional[float]:
    """该引擎在该 URL 类型上的历史平均质量(0-1);无历史返回 None。"""
    b = _load_engine_stats().get(eng, {}).get(url_type)
    if not b or not b.get("n"):
        return None
    ok_rate = b["ok"] / b["n"]
    avg_q = b["q_sum"] / b["n"]
    return ok_rate * 0.6 + avg_q * 0.4


def classify_url(url: str) -> str:
    """根据 URL 模式识别页面类型。

    先去掉尾斜杠/query/fragment 再匹配 —— "/pricing/" 尾斜杠会破坏
    模式的 $ 锚点,导致定价页被误判为 homepage(路由不走定价引擎组,
    真实事故:WATI /pricing/ 拿到的是 homepage 组合)。
    """
    url_clean = url.lower().split("?")[0].split("#")[0].rstrip("/")
    for url_type, pattern in _URL_TYPE_PATTERNS:
        if pattern.search(url_clean):
            return url_type
    return "homepage"


def recommend_scrapers(url: str, need_login: bool = False) -> List[str]:
    """根据 URL 类型 + 引擎历史表现返回最合适的爬虫组合。

    智能路由 = 静态规则(该类型页面用什么引擎) + 动态排序(该引擎在该类型
    页面上的历史成功率/质量)。历史数据不足(<3 次)的引擎保持静态位次 ——
    避免一两次偶发失败把新引擎永久踢出局。

    Args:
        url: 目标 URL
        need_login: 是否需要登录(强制用 playwright)

    Returns:
        爬虫名称列表(有序,前几个 = 主力)
    """
    if need_login:
        return ["playwright"]
    url_type = classify_url(url)
    base = list(_URL_TYPE_SCRAPERS.get(url_type, _URL_TYPE_SCRAPERS["homepage"]))
    scored = []
    for i, eng in enumerate(base):
        hist = _engine_stats_score(eng, url_type)
        # 历史分为主(权重 0.7),静态位次为辅(0.3);无历史 = 中性 0.5
        dynamic = hist if hist is not None else 0.5
        static = 1.0 - i * 0.15
        scored.append((dynamic * 0.7 + static * 0.3, i, eng))
    scored.sort(reverse=True)
    ordered = [eng for _, _, eng in scored]
    # firecrawl 有 key 时插首位(商业 API 覆盖最强);无 key 不出现
    from adapters import firecrawl_scraper as _fc

    if _fc.is_available() and "firecrawl" not in ordered:
        ordered.insert(0, "firecrawl")
    return ordered


# ============================================================
# Adapter 注册表 —— 新增爬虫只需在这里加一行
# ============================================================
def _build_adapter_registry():
    """懒加载：每个 adapter 在第一次访问时才 import（避免硬依赖）"""
    from adapters import (
        firecrawl_scraper,
        trafilatura_scraper,
        newspaper3k_scraper,
        jina_scraper,
        playwright_scraper,
    )

    return {
        "firecrawl": (firecrawl_scraper, True, False, False),
        "trafilatura": (trafilatura_scraper, False, False, False),
        "newspaper3k": (newspaper3k_scraper, False, False, False),
        "jina": (jina_scraper, False, False, False),
        "playwright": (playwright_scraper, True, True, True),
    }


# ============================================================
# 核心：并行爬取 + 智能合并
# ============================================================
def _accepts_kwarg(fn, name: str) -> bool:
    """fn 是否接受名为 name 的关键字参数(显式声明或 **kwargs)。"""
    import inspect

    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
    )


async def _scrape_one(name: str, scrape_fn, url: str, **kwargs) -> Dict[str, Any]:
    """包装单个 scraper 调用，捕获异常。

    sync scraper 在线程池中跑（避免阻塞 event loop）；
    async scraper 直接 await。
    """
    import inspect

    try:
        if inspect.iscoroutinefunction(scrape_fn):
            result = await scrape_fn(url, **kwargs)
        else:
            # 同步 scraper 在默认 executor 跑（线程池）
            result = await asyncio.to_thread(scrape_fn, url, **kwargs)
        result["scraper"] = name
        return result
    except Exception as e:
        return {
            "success": False,
            "scraper": name,
            "error": f"{name} 异常: {type(e).__name__}: {e}",
            "markdown": "",
            "html": "",
            "text": "",
            "screenshot": None,
            "extracted": None,
        }


async def _scrape_parallel(
    url: str,
    prompt: Optional[str] = None,
    max_chars: int = 50000,
    need_screenshot: bool = False,
    need_login: bool = False,
    timeout: float = 60.0,
    enabled_scrapers: Optional[List[str]] = None,
    keep_rx=None,
) -> List[Dict[str, Any]]:
    """并行调用所有启用的 scraper。

    Args:
        url: 目标 URL
        prompt: LLM 提取提示
        max_chars: markdown 最大长度
        need_screenshot: 是否需要截图
        need_login: 是否需要登录（强制用 Playwright）
        timeout: 单个 scraper 超时（秒）
        enabled_scrapers: 指定启用的 scraper（如 ['firecrawl', 'trafilatura']）
                         None 表示全部（按注册表顺序）
        keep_rx: 关键 token 保窗正则(定价页 PRICE_TOKEN_RX)—— 引擎级
                 截断时匹配窗口强制保留(tidio 实测:价格在长页中段,
                 引擎级 50K 截断把价格全切掉 → 交叉验证随机失败)
    """
    registry = _build_adapter_registry()
    enabled = enabled_scrapers or list(registry.keys())

    tasks = []
    for name in enabled:
        if name not in registry:
            continue
        module, supports_screenshot, supports_login, _supports_prompt = registry[name]

        if not module.is_available():
            continue
        # need_login 强制走 playwright
        if need_login and not supports_login:
            continue

        kwargs: Dict[str, Any] = {}
        # max_chars —— 仅对接受的 scraper 传
        if name not in ("playwright",):
            kwargs["max_chars"] = max_chars
        # C3 修复:此处曾给 playwright 传 kwargs["prompt"],但
        # playwright_scraper.scrape() 无 prompt 参数 → TypeError 被
        # _scrape_one 的 except 吞掉,主力引擎静默缺席。prompt 已由下方
        # extract_prompt=prompt 正确传递,哑弹分支删除。
        if supports_screenshot and need_screenshot:
            if name == "playwright":
                kwargs["screenshot_path"] = f"/tmp/youzi_{url_hash(url)}.png"
            else:
                kwargs["screenshot"] = True
        if supports_login:
            kwargs["extract_prompt"] = prompt
            kwargs["timeout"] = int(timeout * 1000)  # playwright 用毫秒
        elif _accepts_kwarg(module.scrape, "timeout"):
            # C4 修复:timeout 原先只传给 playwright,jina/firecrawl/
            # newspaper3k 用各自硬编码值。签名接受的引擎统一下发(秒)。
            kwargs["timeout"] = timeout
        if keep_rx is not None and _accepts_kwarg(module.scrape, "keep_rx"):
            # 定价页关键 token 保窗:引擎级截断不再随机切掉中段价格表
            kwargs["keep_rx"] = keep_rx

        tasks.append((name, _scrape_one(name, module.scrape, url, **kwargs)))

    if not tasks:
        return [
            {
                "success": False,
                "scraper": "none",
                "error": "no scraper available (check install)",
                "markdown": "",
                "html": "",
                "text": "",
                "screenshot": None,
                "extracted": None,
            }
        ]

    # 并行执行,带总预算(C4 修复:原 gather 无总超时,一个慢引擎 —
    # 如 newspaper3k 的无超时 download —— 会永久挂起拖死整个 scrape_smart)。
    # 总预算 = 单引擎超时的 1.5 倍,给最慢引擎留余量;超时的引擎记为
    # 失败结果并取消,已完成引擎的结果保留。
    overall_timeout = timeout * 1.5
    task_objs = [asyncio.ensure_future(c) for _, c in tasks]
    done, pending = await asyncio.wait(task_objs, timeout=overall_timeout)
    for t in pending:
        t.cancel()
    if pending:
        # 等取消落定,避免 "Task was destroyed but it is pending" 警告
        await asyncio.gather(*pending, return_exceptions=True)

    final = []
    for (name, _), t in zip(tasks, task_objs):
        if t in pending or t.cancelled():
            final.append(
                {
                    "success": False,
                    "scraper": name,
                    "error": f"{name} 超时(总预算 {overall_timeout:.0f}s),已取消",
                    "markdown": "",
                    "html": "",
                    "text": "",
                    "screenshot": None,
                    "extracted": None,
                }
            )
        elif t.exception() is not None:
            final.append(
                {
                    "success": False,
                    "scraper": name,
                    "error": str(t.exception()),
                    "markdown": "",
                    "html": "",
                    "text": "",
                    "screenshot": None,
                    "extracted": None,
                }
            )
        else:
            final.append(t.result())
    return final


# 引擎质量优先级(数字越小越可信):商业 API > 专用正文抽取 > 通用 fallback
# primary 按此顺序选,长度只做 tie-break —— "最长"经常是 nav/JS 垃圾最多的那份
_ENGINE_QUALITY = {
    "firecrawl": 0,
    "playwright": 1,
    "trafilatura": 2,
    "jina": 3,
    "newspaper3k": 4,
}

_CODE_JUNK_RX = __import__("re").compile(
    r"(function\s*\(|var\s+\w+\s*=|jQuery|document\.|window\.|=>\s*\{"
    r"|\.css-|!important|@media|<script|\\n\s*[{}]|\"@context\"|\{\"@"
    r"|--[\w-]+\s*:|gradient\(|animation\s*:|:root|@import|\.woff2?"
    r"|#[0-9a-fA-F]{3,8}\s*[;}]|self\.__next_f|partytown|pointer-events"
    r"|border-radius|radial-gradient|position\s*:\s*absolute)"
)
# markdown 链接 [text](url) —— 用于计算链接密度
_LINK_RX = __import__("re").compile(r"\[([^\]]*)\]\([^)]+\)")


def _md_quality(md: str) -> float:
    """粗估 markdown 质量:代码垃圾/链接密度(导航菜单)越低越好,结构适度加分。

    垃圾占比按【字符】加权而非行数 —— YCloud 实测事故:Chakra UI 把
    151KB styled-components CSS 压成【单行】,行级 junk_ratio=1/380≈0.3%
    → 质量分虚高 0.62 → CSS 垃圾当选 primary,截断落盘后正文全丢。
    字符加权后同一样本 junk_ratio≈0.86 → q≈0.05,永无上位资格。
    """
    if not md:
        return 0.0
    lines = [ln for ln in md.split("\n") if ln.strip()]
    if not lines:
        return 0.0
    total_chars = max(len(md), 1)
    junk_chars = sum(len(ln) for ln in lines if _CODE_JUNK_RX.search(ln))
    junk_ratio = min(junk_chars / total_chars, 1.0)
    structured = sum(
        1 for ln in lines if ln.lstrip().startswith(("#", "- ", "* ", "## "))
    )
    structure_ratio = structured / len(lines)
    # 链接密度:正文里链接字符占比。nav/footer 菜单 >60% 都是链接,定价正文 <25%
    link_chars = sum(len(m.group(0)) for m in _LINK_RX.finditer(md))
    link_density = link_chars / total_chars
    # 纯文本长度奖励(对数,防长垃圾)
    import math

    length_bonus = min(math.log10(max(len(md), 10)) / 5, 1.0)
    base = (
        max(0.0, 1.0 - junk_ratio * 1.5) * 0.5
        + structure_ratio * 0.3
        + length_bonus * 0.2
    )
    return base * max(0.05, 1.0 - link_density * 1.2)


def truncate_md(
    md: str, max_chars: int = 50000, tail_chars: int = 10000, keep_rx=None
) -> str:
    """头尾截断:保前 (max-tail) + 省略号 + 保尾 tail。

    纯头部截断的历史事故:playwright 完整 markdown = 一行巨型 CSS +
    后续 23,993 字符正文,[:50000] 把截断点正好落在 CSS 尾部 → 证据库
    (engines.json)与合并视图只存下 CSS,正文 100% 丢失,G2 quote 回查
    对该引擎失效。尾部往往承载 pricing 页的套餐表/FAQ,必须保留。

    keep_rx(2026-08-30,tidio 实测):关键 token 保窗正则(定价页传
    PRICE_TOKEN_RX)。tidio 定价页 5 个价格全在 111K 页面的 65K-95K
    【中段】—— 头(40K)尾(10K)截断后 5 价全丢:合并视图无价、证据库
    无价、jina 引擎级截断无价 → 交叉验证随机失败。带 keep_rx 时匹配
    ±context 字符窗口强制保留,预算按窗口数均分,超量窗口诚实标注丢弃。

    头窗自适应(2026-08-30 复盘):固定 2K 头窗在「价格少而聚簇」的
    长定价页上浪费窗口预算 —— 2 价 111K 页输出仅 32K(比无 keep_rx 的
    50K 还少 18K),套餐功能矩阵/FAQ 等非价格上下文被砍,Step 3 可读
    素材骤减。改为:窗口实际用掉的预算之外,剩余回流给头窗(上限为
    无 keep_rx 路径的头量),与头窗重叠的窗口段裁掉不重复占预算。
    """
    if not md or len(md) <= max_chars:
        return md
    if keep_rx is None:
        head = max_chars - tail_chars
        return md[:head] + "\n\n[... 中间内容已截断 ...]\n\n" + md[-tail_chars:]

    # ── 保窗截断:自适应头 + 关键窗口(重叠合并) + 尾 ──
    matches = list(keep_rx.finditer(md))
    if not matches:
        head = max_chars - tail_chars
        return md[:head] + "\n\n[... 中间内容已截断 ...]\n\n" + md[-tail_chars:]

    head_stub = 2000
    budget_windows = max(max_chars - tail_chars - head_stub, len(matches) * 260)
    match_chars = sum(m.end() - m.start() for m in matches)
    ctx = max(120, (budget_windows - match_chars) // (2 * len(matches)))
    # 窗口超预算(价格极多的页):保留前 N 个窗口,其余诚实标注
    spans: list = []
    kept, dropped = 0, 0
    for m in matches:
        if sum(e - s for s, e in spans) + (m.end() - m.start()) + 2 * ctx > (
            budget_windows + len(spans) * 64
        ):
            dropped += 1
            continue
        s, e = max(0, m.start() - ctx), min(len(md), m.end() + ctx)
        if spans and s <= spans[-1][1] + 64:
            spans[-1] = (spans[-1][0], max(spans[-1][1], e))
        else:
            spans.append((s, e))
        kept += 1

    # 头窗自适应:窗口没花掉的预算回流给头(留 128/窗口的分隔符余量)。
    # 头放大又会吞掉与其重叠的窗口(内容已在头里,裁掉不重复占预算),
    # 裁掉的预算可再回流 —— 迭代到稳定(头单调增、窗口单调缩,数轮收敛)。
    head_actual = head_stub
    for _ in range(4):
        spans_eff = [(max(s, head_actual), e) for s, e in spans if e > head_actual]
        used_eff = sum(e - s for s, e in spans_eff)
        head_next = max(
            head_stub,
            min(
                max_chars - tail_chars - used_eff - 128 * max(len(spans_eff), 1),
                max_chars - tail_chars,
            ),
        )
        if head_next <= head_actual:
            break
        head_actual = head_next
    spans = [(max(s, head_actual), e) for s, e in spans if e > head_actual]

    parts = [md[:head_actual], "\n\n[... 截断:以下为关键内容保窗 ...]\n\n"]
    prev_end = None
    for s, e in spans:
        if prev_end is not None:
            parts.append("\n\n[... 中间内容已截断 ...]\n\n")
        parts.append(md[s:e])
        prev_end = e
    parts.append("\n\n[... 中间内容已截断 ...]\n\n")
    if dropped:
        parts.append(
            f"[... 另有 {dropped} 个关键 token 窗口超出保留预算被丢弃 ...]\n\n"
        )
    parts.append(md[-tail_chars:])
    return "".join(parts)


def _norm_para(p: str) -> str:
    """段落归一化(去空白/大小写)用于跨引擎去重。"""
    return " ".join(p.lower().split())


def _merge_results(
    results: List[Dict[str, Any]],
    max_chars: int = 50000,
    allow_supplements: bool = False,
    url_type: Optional[str] = None,
) -> Dict[str, Any]:
    """智能合并多个 scraper 的结果。

    策略(顺序敏感 —— 乱序会破坏 "套餐名 ↔ 价格" 的对应关系):
    - primary:定价页按"内容完整性优先"(有价格 token 才有资格),其余
      按引擎质量优先级,垃圾占比高的一票否决;长度只做 tie-break
    - primary markdown 原序保留为文档主体
    - allow_supplements 默认 False(全部证据页禁拼接 —— 跨引擎拼正文
      = 张冠李戴的温床,拼接段落无法归属引擎)。每个引擎的完整原文
      保留在 all_results 里供上层逐引擎取证。
    - 截图:优先 firecrawl > playwright > 其他
    """
    success = [r for r in results if r.get("success") and r.get("markdown")]
    failed = [r for r in results if not r.get("success")]

    if not success:
        return {
            "success": False,
            "scraper": "none",
            "markdown": "",
            "html": "",
            "text": "",
            "screenshot": None,
            "extracted": None,
            "error": "; ".join(
                f"[{r['scraper']}] {r.get('error', '?')}"
                for r in results
                if not r.get("success")
            )
            or "all failed",
            "all_results": results,
        }

    def primary_key(r):
        md = r.get("markdown", "")
        q = _md_quality(md)
        # 定价页内容完整性维度(2026-08-30 修复,tidio 实测):_md_quality
        # 度量的是洁净度,562B 空壳能拿 q=0.76、111KB 全量价格表只有
        # q=0.34 —— 零价格的高洁净正文当选 primary 后,合并视图丢失
        # 全部价格(仅靠各引擎原文 + vote_detail 兜底)。定价页第一维
        # 改为"是否含价格 token";全员无价时回退通用规则。
        has_content = True
        if url_type == "pricing":
            try:
                from pricing_tokens import PRICE_TOKEN_RX as _PTX
            except ImportError:
                _PTX = None
            if _PTX is not None:
                has_content = bool(_PTX.search(md))
        # 垃圾占比过高(quality < 0.5)的引擎没有资格当 primary。
        # 阈值沿革:0.3 → 0.35(crawl4ai docs q≈0.05 混入) → 0.5(YCloud
        # 实测:playwright CSS 垃圾 q=0.62(行级度量虚高)压过 trafilatura
        # 真实定价表 q=0.635 —— 引擎排名(-1>-2)在双方过线时直接定胜负)。
        # 门槛提到 0.5:引擎排名只在"都过线"的高质量带内 tie-break;
        # 全员 <0.5 时第一维同为 False,回退到质量分+排名的原始序。
        return (
            has_content,
            q >= 0.50,
            -_ENGINE_QUALITY.get(r["scraper"], 99),
            q,
            len(md),
        )

    primary = max(success, key=primary_key)
    used_scrapers = [r["scraper"] for r in success]

    # 主体 = primary 原序;补充 = 其他引擎的独有段落(按质量排序追加)。
    # 全部证据页 allow_supplements=False → 补充段落循环整段跳过
    # (历史实现先算完再整体丢弃,纯白算)。
    primary_md = primary.get("markdown", "") or ""
    merged_md = primary_md
    supplements: List[str] = []
    if allow_supplements:
        seen = {_norm_para(p) for p in primary_md.split("\n\n") if p.strip()}
        ordered_others = sorted(
            (r for r in success if r is not primary),
            key=lambda r: _ENGINE_QUALITY.get(r["scraper"], 99),
        )
        for r in ordered_others:
            for p in (r.get("markdown", "") or "").split("\n\n"):
                p = p.strip()
                if len(p) <= 40:  # 太短的"独有段落"几乎都是噪音碎片
                    continue
                key = _norm_para(p)
                if key in seen:
                    continue
                seen.add(key)
                supplements.append(p)
        if supplements:
            merged_md += "\n\n<!-- 以下为其他引擎补充段落 -->\n\n" + "\n\n".join(
                supplements
            )
    # 定价页截断带价格保窗(tidio 实测:价格表在长页中段,头尾截断全丢)
    _keep_rx = None
    if url_type == "pricing":
        try:
            from pricing_tokens import PRICE_TOKEN_RX

            _keep_rx = PRICE_TOKEN_RX
        except ImportError:
            _keep_rx = None
    merged_md = truncate_md(merged_md, max_chars, keep_rx=_keep_rx)

    screenshot = None
    for r in success:
        if r.get("screenshot"):
            screenshot = r["screenshot"]
            break

    extracted: Dict[str, Any] = {}
    for r in success:
        if r.get("extracted") and isinstance(r["extracted"], dict):
            for k, v in r["extracted"].items():
                if k not in extracted or not extracted[k]:
                    extracted[k] = v

    return {
        "success": True,
        "scraper": "+".join(used_scrapers),
        "markdown": merged_md,
        "html": primary.get("html", ""),
        "text": primary.get("text", ""),
        "screenshot": screenshot,
        "extracted": extracted if extracted else None,
        "all_results": results,
        "stats": {
            "total_scrapers": len(results),
            "successful": len(success),
            "failed": len(failed),
            "scrapers_used": used_scrapers,
            "primary_scraper": primary["scraper"],
            "primary_quality": round(_md_quality(primary_md), 2),
            "supplement_paragraphs": len(supplements) if allow_supplements else 0,
        },
        "error": None,
    }


async def _scrape_smart_async(
    url: str,
    prompt: Optional[str] = None,
    max_chars: int = 50000,
    need_screenshot: bool = False,
    need_login: bool = False,
    timeout: float = 60.0,
    strategy: str = "auto",
    enabled_scrapers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """智能爬取（异步主入口）。

    默认 auto 策略(智能路由): 根据 URL 类型(classify_url) + 引擎历史表现
    自动选择最合适的爬虫组合(5 引擎白名单;有 FIRECRAWL_API_KEY 时
    firecrawl 插组合首位)。
    - pricing/feature/home 页 → JS 渲染组 + 静态对照引擎,且禁止跨引擎合并正文
    - docs 站 → trafilatura + jina
    - dashboard → playwright
    - about/blog/customer 页 → newspaper3k + trafilatura

    其他策略:
    - "parallel": 全部可用引擎并行(覆盖最大,只用于 auto 失败后的兜底)
    """
    # ── 智能路由 ──
    url_type = classify_url(url)
    if enabled_scrapers is None and strategy != "all":
        # auto(默认):按 URL 类型 + 历史表现选引擎组合
        if strategy == "auto":
            enabled_scrapers = recommend_scrapers(url, need_login=need_login)
        # 否则(parallel)用全开模式,保证覆盖

    # 定价页:引擎级 + 合并级截断都带价格保窗(关键 token 不落中段截断)
    _keep_rx = None
    if url_type == "pricing":
        try:
            from pricing_tokens import PRICE_TOKEN_RX

            _keep_rx = PRICE_TOKEN_RX
        except ImportError:
            _keep_rx = None

    results = await _scrape_parallel(
        url,
        prompt,
        max_chars,
        need_screenshot,
        need_login,
        timeout,
        enabled_scrapers=enabled_scrapers,
        keep_rx=_keep_rx,
    )
    merged = _merge_results(
        results,
        max_chars,
        allow_supplements=url_type not in _NO_SUPPLEMENT_TYPES,
        url_type=url_type,
    )
    merged["url_type"] = url_type

    # ── 引擎表现学习:喂给下一轮智能路由 ──
    try:
        record_engine_outcome(
            url_type,
            {
                r.get("scraper", "?"): {
                    "success": bool(r.get("success") and r.get("markdown")),
                    "quality": _md_quality(r.get("markdown", "")),
                }
                for r in results
                if r.get("scraper") not in (None, "none", "unknown")
            },
        )
    except Exception:
        pass  # 统计失败绝不影响爬取结果

    return merged


def scrape_smart(
    url: str,
    prompt: Optional[str] = None,
    max_chars: int = 50000,
    need_screenshot: bool = False,
    need_login: bool = False,
    timeout: float = 60.0,
    strategy: str = "auto",
    enabled_scrapers: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """智能爬取（同步入口，推荐用这个）。

    默认 auto(智能路由):按 URL 类型 + 引擎历史表现选组合;pricing 页
    自动加静态对照引擎并隔离各引擎原文(供交叉验证)。

    用法：
        # 智能路由(默认,推荐)
        result = scrape_smart("https://example.com/pricing")

        # 全部引擎并行(兜底,覆盖最大)
        result = scrape_smart("https://example.com", strategy="parallel")

        # 指定引擎
        result = scrape_smart(
            "https://example.com",
            enabled_scrapers=["trafilatura", "jina"],
        )

        # 截图
        result = scrape_smart(
            "https://example.com", need_screenshot=True,
        )

        # 登录态
        result = scrape_smart(
            "https://app.example.com",
            need_login=True,
        )
    """
    return asyncio.run(
        _scrape_smart_async(
            url,
            prompt,
            max_chars,
            need_screenshot,
            need_login,
            timeout,
            strategy,
            enabled_scrapers=enabled_scrapers,
        )
    )


def list_scrapers() -> Dict[str, bool]:
    """返回所有注册的爬虫及其可用状态（用于 CLI 显示）。"""
    registry = _build_adapter_registry()
    return {name: mod.is_available() for name, (mod, *_) in registry.items()}


# 2026-08-30 清理:scrape_with_fallback(旧版串行回退)与 strategy="fallback"
# 分支删除 —— 全库零调用方(grep 证实),保留只会让维护面虚增。
# 历史注记:C-bug 修复时确立的「调用资格只取决于 is_available()」原则
# 已被 scrape_smart 路由继承。

__all__ = [
    "scrape_smart",
    "list_scrapers",
    "url_hash",
    "classify_url",
    "recommend_scrapers",
    "record_engine_outcome",
    "truncate_md",
]
