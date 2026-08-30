#!/usr/bin/env python3
"""2026-08-30 质量回归复盘的修复回归测试(用户反馈「报告质量不如之前」)。

每条对应本次复盘发现的退化机制(见会话审计):
  R1 audit._price_tokens_per_engine 的 pricing_urls 未按竞品域名过滤
     —— 他域定价页约束本竞品统计 → docs 定价的竞品被误判 gap 烧预算
  R2 truncate_md 固定 2K 头窗浪费窗口预算 —— 2 价聚簇 111K 页输出仅
     32K(比无 keep_rx 还少 18K),Step 3 可读素材骤减
  R3 normalize_billing_period 对 /user /seat(sufficiency 祝福值)与中文
     周期词原样放行 → G8 hard → run_youzi 不交付(契约冲突)
  R4 month+year 同现("monthly billed annually")误入 /yr 通道
  R5 resolver websearch 子串采纳 —— "hub" 命中 hubspot.com,整竞品证据
     来自错误站点
  R6 run_youzi 空竞品列表 → ThreadPoolExecutor(max_workers=0) ValueError
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters import truncate_md  # noqa: E402
from audit import _price_tokens_per_engine  # noqa: E402
from gates import g8_structure_contract  # noqa: E402
from pricing_tokens import (  # noqa: E402
    PRICE_TOKEN_RX,
    VALID_PERIODS,
    normalize_billing_period,
)
from verify import Report  # noqa: E402


# ═══════════ R1 audit 定价页集合必须按竞品域名过滤 ═══════════


def test_price_tokens_docs_fallback_not_blocked_by_other_competitor():
    """A 家有 /pricing,B 家定价藏在 docs:B 的 docs 页必须仍被统计。

    事故形态:B 永远不触发全页回退 → engines_with_price 空 → 误判 gap
    → 按 SKILL.md 闭环去补爬不存在的 /pricing 页,烧预算。
    """
    comp = {"name": "B", "url": "https://b-saas.io"}
    raw_index = {
        "https://b-saas.io/docs/billing": {
            "jina": "usage billing at $0.4 per unit\n" * 3,
            "firecrawl": "$0.4/unit example\n",
        },
        "https://a-other.com/pricing": {"jina": "$99/mo $199/mo"},
    }
    manifest = {
        "fetched": {
            # 他域(A 家)的定价页 —— 不该约束 B 家的统计口径
            "https://a-other.com/pricing": {"kinds": ["pricing"]},
            "https://b-saas.io/docs/billing": {"kinds": ["docs"]},
        }
    }
    counts = _price_tokens_per_engine(comp, raw_index, manifest)
    assert counts.get("jina", 0) > 0, (
        "B 家 docs 页价格必须被统计(全页回退不被他域定价页挡死)"
    )


# ═══════════ R2 truncate_md 头窗自适应:预算必须吃满 ═══════════


def test_truncate_head_window_reuses_unused_window_budget():
    """2 价聚簇 111K 页:输出必须接近 max_chars(修复前仅 ~32K)。"""
    filler = (
        "Premium plan includes unlimited contacts and advanced analytics. "
        "我们的高级套餐包含无限联系人与高级分析。FAQ here. "
    ) * 20
    md = list(filler * 55)
    md[3000], md[3100] = md[3000] + " $29/mo ", md[3100] + " $29/mo "
    out = "".join(md)
    assert len(out) > 90000, "测试前提:确实触发截断"
    r = truncate_md(out, keep_rx=PRICE_TOKEN_RX)
    assert len(r) >= 45000, f"头窗预算必须回流(输出 {len(r)} < 45000)"
    assert len(PRICE_TOKEN_RX.findall(r)) >= 2, "价格 token 不许丢"


def test_truncate_price_heavy_page_unchanged():
    """价格较多的页(60 价):窗口吃满预算,头窗维持最小 stub,总量受约束。"""
    filler = ("plan feature text " * 40) + "$39/mo "
    md = filler * 60  # ~44K 触发截断,60 个价格
    md = md + "tail padding " * 4000  # 拉长到 ~95K 确保超预算
    r = truncate_md(md, keep_rx=PRICE_TOKEN_RX)
    n_in = len(PRICE_TOKEN_RX.findall(md))
    n_out = len(PRICE_TOKEN_RX.findall(r))
    assert n_out >= min(n_in, 55), f"价格 token 保留 {n_out}/{n_in}"
    assert len(r) <= 52000, f"总量仍受 max_chars 约束({len(r)})"


# ═══════════ R3+R4 周期归一化:sufficiency 祝福值/中文词/双通道语义 ═══════════


def test_normalize_period_unit_qualifiers_blessed_by_sufficiency():
    """/user /seat 是单位不是周期 —— 剥离后归无周期,G8 不再拦死。"""
    for raw in ("/user", "/seat", "per user", "/users"):
        assert normalize_billing_period(raw) in VALID_PERIODS, raw
    assert normalize_billing_period("/user/mo") == "/mo"
    assert normalize_billing_period("/seats/yr") == "/yr"


def test_normalize_period_chinese_words():
    for raw, want in (
        ("按月", "/mo"),
        ("月付", "/mo"),
        ("每月", "/mo"),
        ("包月", "/mo"),
        ("按年", "/yr"),
        ("年付", "/yr"),
        ("包年", "/yr"),
        ("年度", "/yr"),
    ):
        assert normalize_billing_period(raw) == want, raw


def test_normalize_period_month_plus_year_is_billed_channel():
    """月价 × 年语义 = 年结算月价 → billed(修复前误入 /yr)。"""
    for raw in ("monthly billed annually", "monthly (annual discount)"):
        assert normalize_billing_period(raw) == "billed", raw


def test_normalize_period_unknown_still_passthrough():
    """真正非法的值仍原样返回(G8 hard 语义保留)。"""
    assert normalize_billing_period("一次性") == "一次性"


def test_g8_no_longer_hard_fails_blessed_unit_periods():
    """端到端:sufficiency 祝福的 /user 周期不再阻断交付。"""
    a = {
        "competitors": [
            {
                "name": "X",
                "url": "https://x.io",
                "pricing_tiers": [
                    {"name": "Free", "price": "$0", "billing_period": "/user"},
                    {"name": "Pro", "price": "₹999", "billing_period": "按月"},
                ],
            }
        ],
        "opportunities": [],
        "gaps": [],
    }
    rep = Report()
    g8_structure_contract(a, {"fetched": {}}, {}, rep)
    assert not any("非法周期" in v["detail"] for v in rep.violations), (
        "/user 与中文周期词不允许再 hard(契约冲突,会整份报告拦死)"
    )


# ═══════════ R5 resolver websearch 分段精确采纳 ═══════════


def test_websearch_resolve_rejects_substring_domain(monkeypatch):
    """产品名 "hub" 不得因子串采纳 hubspot.com(错误站点污染全部证据)。"""
    import scripts.deep_link as dl
    from adapters.competitor_resolver import _websearch_resolve

    def fake_search(q, n=5):
        return [
            {"url": "https://hubspot.com/pricing"},
            {"url": "https://timezone.io"},
        ]

    monkeypatch.setattr(dl, "search_web", fake_search)
    assert _websearch_resolve("hub") is None


def test_websearch_resolve_accepts_full_segment_domain(monkeypatch):
    import scripts.deep_link as dl
    from adapters.competitor_resolver import _websearch_resolve

    def fake_search(q, n=5):
        return [{"url": "https://meetbot.io/features"}]

    monkeypatch.setattr(dl, "search_web", fake_search)
    r = _websearch_resolve("meetbot")
    assert r and r["url"].startswith("https://meetbot.io")


# ═══════════ R6 空竞品列表不崩 ═══════════


def test_step2_empty_competitors_returns_empty(tmp_path, capsys):
    from scripts.run_youzi import step2_fetch

    assert step2_fetch([], tmp_path) == []


# ═══════════ R7 firecrawl 引擎级 keep_rx 接线(证据口径一致性) ═══════════


def test_firecrawl_scrape_accepts_keep_rx():
    """dispatch 按签名探测下发 keep_rx —— firecrawl 不收则被静默跳过,
    engines.json 里中段价格仍会丢(与 jina/trafilatura 口径不一致)。"""
    import inspect

    from adapters import firecrawl_scraper

    assert "keep_rx" in inspect.signature(firecrawl_scraper.scrape).parameters


def test_firecrawl_truncate_keeps_midpage_prices():
    from adapters.firecrawl_scraper import _truncate
    from pricing_tokens import PRICE_TOKEN_RX

    filler = ("plan feature text " * 40) + "\n"
    md = filler * 100 + "$399/yr mid-page price\n" + filler * 100
    r = _truncate(md, 5000, keep_rx=PRICE_TOKEN_RX)
    assert "$399/yr" in r, "中段价格必须保窗保留"


# ═══════════ R8 G8 对字符串形态 feature_catalog 条目:拦而不崩 ═══════════


def test_g8_string_feature_entry_is_gated_not_crash():
    a = {
        "competitors": [
            {
                "name": "X",
                "url": "https://x.io",
                "feature_catalog": {"X": ["团队收件箱", "AI 客服"]},
            }
        ],
        "opportunities": [],
        "gaps": [],
    }
    rep = Report()
    g8_structure_contract(a, {"fetched": {}}, {}, rep)  # 此前 AttributeError
    assert any("feature_catalog" in v["field"] for v in rep.violations)


# ═══════════ R9 标配能力疑点检查(第 25 轮:权限管理巡检思路工具化) ═══════════


def test_common_feature_gaps_flags_minority_missing():
    """4/5 厂商有的能力,缺失的那家必须被标疑点(权限管理事故的泛化)。"""
    from audit import _common_feature_gaps

    def comp(name, feats):
        return {
            "name": name,
            "feature_catalog": {
                name: [{"name": f, "category": "c", "source": ""} for f in feats]
            },
        }

    comps = [
        comp("A", ["权限与团队管理 (RBAC)", "团队共享收件箱"]),
        comp("B", ["权限与团队管理 (RBAC)", "团队共享收件箱"]),
        comp("C", ["权限与团队管理 (RBAC)", "团队共享收件箱"]),
        comp("D", ["权限与团队管理 (RBAC)", "团队共享收件箱"]),
        comp("E", ["团队共享收件箱"]),  # 缺 RBAC
    ]
    gaps = _common_feature_gaps(comps)
    assert gaps.get("E") == ["权限与团队管理 (RBAC)"], gaps
    assert "A" not in gaps


def test_common_feature_gaps_no_false_positive_on_unique():
    """少数厂商独有的能力不算标配(把独家报成标配会诱导硬凑)。"""
    from audit import _common_feature_gaps

    def comp(name, feats):
        return {
            "name": name,
            "feature_catalog": {
                name: [{"name": f, "category": "c", "source": ""} for f in feats]
            },
        }

    comps = [
        comp("A", ["JSON API 插件"]),
        comp("B", ["团队共享收件箱"]),
        comp("C", ["团队共享收件箱"]),
        comp("D", ["团队共享收件箱"]),
    ]
    gaps = _common_feature_gaps(comps)
    # 「JSON API 插件」1/4 独有 → 不算标配,不得出现在任何家的疑点里
    assert all("JSON API 插件" not in v for v in gaps.values()), gaps
    # 「团队共享收件箱」3/4(=ratio 阈值)→ 缺失的 A 被标(与权限管理
    # 4/5 同型,且疑点名是别名库归一后的规范名),这是期望行为
    assert gaps.get("A") == ["团队收件箱 (Team Inbox)"]
