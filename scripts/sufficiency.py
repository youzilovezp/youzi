# -*- coding: utf-8 -*-
"""字段级充分性契约 + 引擎升级梯 —— 准 > 快 的核心闭环。

每类信息定义「什么算抓准了」,`_crawl_page` 爬完立即评估;不达标 →
沿引擎升级梯换引擎重爬 → 仍不达标 → 交给 deep_link 搜索定位具体页。
预算:每竞品 5 分钟墙钟,超时诚实标「未验证」(绝不伪造)。
"""

import re
import sys
from pathlib import Path
from typing import Dict, Any, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pricing_tokens import PRICE_TOKEN_RX  # noqa: E402

# ── V2 引擎升级梯(白名单内,排除已用) ──
_ENGINE_LADDER_EXTRA = {
    "pricing": ["trafilatura", "jina", "newspaper3k"],
    "docs": ["jina", "playwright", "newspaper3k"],
    "homepage": ["jina", "newspaper3k"],
    "feature": ["jina", "newspaper3k"],
    "about": ["playwright", "newspaper3k"],
    "blog": ["jina", "playwright"],
    "customer": ["jina", "playwright"],
    "testimonials": ["jina", "playwright"],
    "changelog": ["playwright", "newspaper3k"],
    "integration": ["playwright", "newspaper3k"],
    "dashboard": [],
}

# 每竞品墙钟预算(秒) —— 准 > 快,但不无限等
COMPETITOR_BUDGET_SECONDS = 300.0

# ── 定价充分性 ──

# Free 档:名字明确 free(价格 $0 或空)
_FREE_NAME_RX = re.compile(r"^(free|starter free|free plan|免费)", re.I)
# 定制/联系销售档:无数字价格的报价档(带真实价格的 Enterprise 是付费档!)
_CUSTOM_NAME_RX = re.compile(
    r"custom|contact|quote|面议|定制|联系|咨询|get in touch", re.I
)


def is_free_tier(name: str, price: str = "") -> bool:
    """Free 档:名字明确 free,或价格就是 $0/free。"""
    n = (name or "").strip()
    p = (price or "").strip()
    if _FREE_NAME_RX.search(n):
        return True
    return p.lower() in ("free", "$0", "¥0", "€0", "£0", "0", "免费")


def is_custom_tier(name: str, price: str = "") -> bool:
    """定制/联系销售档:名字或价格表达"报价",且无数字价格。"""
    n = (name or "").strip()
    p = (price or "").strip()
    if is_free_tier(n, p):
        return False
    if re.search(r"\d", p):
        return False
    return bool(_CUSTOM_NAME_RX.search(n) or _CUSTOM_NAME_RX.search(p))


def is_no_period_tier(name: str, price: str = "") -> bool:
    """语义上无周期:Free / 定制报价档。付费 Enterprise 不算。"""
    return is_free_tier(name, price) or is_custom_tier(name, price)


def ladder_engines(url_type: str, already_used: List[str]) -> List[str]:
    """返回升级梯下一棒引擎(白名单内、未用过的)。

    firecrawl 有 key 时排最前(商业 API 是最强增援)。
    注意:梯子含"首棒组合里的引擎"没关系 —— already_used 会把它们排除,
    首棒失败/低质的引擎换不同引擎重试才是目的。
    """
    pool = list(_ENGINE_LADDER_EXTRA.get(url_type, []))
    from adapters import firecrawl_scraper as _fc

    if _fc.is_available():
        pool = ["firecrawl"] + pool
    used = set(already_used or [])
    seen, out = set(), []
    for e in pool:
        if e not in used and e not in seen:
            seen.add(e)
            out.append(e)
    return out


# 价格 token 统一 pricing_tokens(单一事实源;历史三套口径并存导致
# US$59 截断成 US$5、₹ 漏检等假阴性 —— 2026-08-29 审计修复)
_PRICE_RX = PRICE_TOKEN_RX


def assess_pricing(
    tiers: List[Dict[str, Any]], vote_detail: Optional[List] = None
) -> Dict[str, Any]:
    """定价充分性评估。

    达标标准:
    - 至少 1 个付费档带 货币价格 + 明确周期(或 Free/Custom 档语义正确)
    - Free/Custom 档不得携带周期/价格(历史 bug: Free · $0 (/yr))
    - ≥2 独立引擎看到相同价格(vote_detail 交叉验证,沿用 verify G3 语义)

    返回 {sufficient: bool, problems: [str], engines_with_prices: [str]}
    """
    problems: List[str] = []
    paid_ok = 0
    for t in tiers or []:
        name = (t.get("name") or "").strip()
        price = (t.get("price") or "").strip()
        period = (t.get("billing_period") or "").strip()
        if is_no_period_tier(name, price):
            if period and period not in ("—", "", "/user", "/seat"):
                problems.append(f"free/custom 档携带周期: {name} · {price} {period}")
            continue
        if price and _PRICE_RX.search(price):
            paid_ok += 1
            if not period:
                problems.append(f"付费档缺周期: {name} · {price}")
        elif price and "未能提取" not in price and price != "—":
            problems.append(f"价格无货币符号: {name} · {price}")
    if not tiers:
        problems.append("无任何套餐档")
    elif paid_ok == 0 and not any(
        is_no_period_tier((t.get("name") or ""), (t.get("price") or "")) for t in tiers
    ):
        problems.append("无付费档且无 free 档")
    # 交叉验证:vote_detail 每条 {line, engines: [..], independent_votes: n}
    # —— 有价格行且被 ≥2 独立内容引擎看到才算交叉验证
    engines_with_prices: List[str] = []
    for v in vote_detail or []:
        for eng in v.get("engines") or []:
            if eng and eng not in engines_with_prices:
                engines_with_prices.append(eng)
    if paid_ok > 0 and len(engines_with_prices) < 2:
        problems.append(f"仅 {len(engines_with_prices)} 个引擎看到价格(需 ≥2 交叉验证)")
    return {
        "sufficient": not problems,
        "problems": problems,
        "engines_with_prices": engines_with_prices,
    }


def assess_page_content(kind: str, markdown: str) -> bool:
    """页面级内容充分性:正文非空且非 JS 壳/404(足够进入字段提取)。"""
    if not markdown or len(markdown.strip()) < 200:
        return False
    low = markdown.lower()
    if low.count("<script") > 5 or "enable javascript" in low[:2000]:
        return False
    return True


def assess_tech_signals(signals: List[Dict[str, Any]]) -> Dict[str, Any]:
    """tech_signals 充分性:每条锚定 docs 子页(非栏目首页)。"""
    problems = []
    for s in signals or []:
        src = s.get("source") or s.get("source_url") or ""
        if not src:
            problems.append(f"信号无来源: {s.get('name')}")
            continue
        path = src.split("?")[0].rstrip("/")
        segs = [p for p in re.sub(r"^https?://[^/]+", "", path).split("/") if p]
        if not segs:
            problems.append(f"信号锚定域名根而非具体页: {s.get('name')} → {src}")
        elif segs[-1].lower() in (
            "docs",
            "documentation",
            "developers",
            "api",
            "reference",
            "en",
            "zh-cn",
            "v1",
            "v2",
        ):
            problems.append(f"信号锚定栏目首页而非具体页: {s.get('name')} → {src}")
    return {"sufficient": bool(signals) and not problems, "problems": problems}


def assess_feedback(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    """user_feedback 充分性:每条引语锚定具体评论/帖子页。"""
    problems = []
    for it in items or []:
        src = it.get("source") or it.get("source_url") or it.get("url") or ""
        if not src:
            problems.append(f"反馈无来源: {str(it.get('quote', ''))[:30]}")
    return {"sufficient": bool(items) and not problems, "problems": problems}
