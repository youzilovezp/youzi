#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""门禁对抗元测试(2026-08-30 第 7 轮):每个 gate 必须能被违规样本触发。

背景:G2 旁路事故(pricing_from_cache 的 continue 放错层级,伪造引文免检)
证明门禁自身需要"构造违规 → 必须命中"的对抗用例 —— 打不出来的门禁
比没有门禁更危险(虚假安全感)。G2 的用例在 test_fixes_2026_08_30.py,
本文件补齐 G1/G3/G4/G5/G6/G7。
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gates  # noqa: E402
from verify import Report  # noqa: E402


def _run(gate_fn, analysis, manifest, engine_index=None):
    rep = Report()
    gate_fn(analysis, manifest, engine_index or {}, rep)
    return rep


def _comp(**kw):
    base = {"name": "X", "url": "https://x.io"}
    base.update(kw)
    return base


# ═══════════ G1 来源可回溯 ═══════════


def test_g1_fires_on_unfetched_source():
    a = {
        "competitors": [
            _comp(
                strengths=[
                    {
                        "point": "p",
                        "evidence": "e",
                        "source": "https://never-fetched.io/page",
                    }
                ]
            )
        ]
    }
    rep = _run(gates.g1_source_traceability, a, {"fetched": {}})
    assert any(v["gate"] == "G1" for v in rep.violations), (
        "未抓取的 URL 当来源必须 hard"
    )


def test_g1_fires_on_failed_source():
    a = {
        "competitors": [
            _comp(
                strengths=[
                    {"point": "p", "evidence": "e", "source": "https://x.io/failed"}
                ]
            )
        ]
    }
    m = {"fetched": {"https://x.io/failed": {"status": "failed"}}}
    rep = _run(gates.g1_source_traceability, a, m)
    assert any(v["gate"] == "G1" for v in rep.violations), "失败页 URL 当来源必须 hard"


def test_g1_passes_on_ok_source():
    a = {
        "competitors": [
            _comp(
                strengths=[{"point": "p", "evidence": "e", "source": "https://x.io/ok"}]
            )
        ]
    }
    m = {"fetched": {"https://x.io/ok": {"status": "ok"}}}
    rep = _run(gates.g1_source_traceability, a, m)
    assert not rep.violations


# ═══════════ G3 定价完整性 ═══════════


def test_g3_fires_on_single_hash_verified():
    """verified 但内容独立引擎 <2(同变体页互证)必须 hard。"""
    a = {
        "competitors": [
            _comp(
                pricing_verified=True,
                pricing_source="https://x.io/pricing",
                pricing_engines=["playwright"],
                pricing_tiers=[
                    {"name": "Pro", "price": "$59", "billing_period": "/mo"}
                ],
                pricing_scraped_at="2026-08-30 00:00 UTC",
            )
        ]
    }
    m = {
        "fetched": {
            "https://x.io/pricing": {
                "status": "ok",
                "engines": {"playwright": {"content_hash": "aaa"}},
            }
        }
    }
    rep = _run(gates.g3_pricing_integrity, a, m)
    assert any("独立引擎不足" in v["detail"] for v in rep.violations)


def test_g3_fires_on_stale_scraped_at():
    a = {
        "competitors": [
            _comp(
                pricing_verified=True,
                pricing_source="https://x.io/pricing",
                pricing_engines=["playwright", "jina"],
                pricing_tiers=[
                    {"name": "Pro", "price": "$59", "billing_period": "/mo"}
                ],
                pricing_scraped_at="2026-07-01 00:00 UTC",  # 60 天前 > TTL 14
            )
        ]
    }
    m = {
        "fetched": {
            "https://x.io/pricing": {
                "status": "ok",
                "engines": {
                    "playwright": {"content_hash": "aaa"},
                    "jina": {"content_hash": "bbb"},
                },
            }
        }
    }
    rep = _run(gates.g3_pricing_integrity, a, m)
    assert any("陈旧" in v["detail"] for v in rep.violations)


def test_g3_fires_on_empty_tiers():
    a = {
        "competitors": [
            _comp(
                pricing_verified=True,
                pricing_source="https://x.io/pricing",
                pricing_engines=["playwright", "jina"],
                pricing_tiers=[],
                pricing_scraped_at="2026-08-30 00:00 UTC",
            )
        ]
    }
    m = {
        "fetched": {
            "https://x.io/pricing": {
                "status": "ok",
                "engines": {
                    "playwright": {"content_hash": "aaa"},
                    "jina": {"content_hash": "bbb"},
                },
            }
        }
    }
    rep = _run(gates.g3_pricing_integrity, a, m)
    assert any("tiers 为空" in v["detail"] for v in rep.violations)


# ═══════════ G4 缺失诚实 ═══════════


def test_g4_fires_on_silent_failure():
    """fetched status=failed 但不在 failures 清单(静默吞掉)必须 hard。"""
    a = {"competitors": []}
    m = {"fetched": {"https://x.io/gone": {"status": "failed"}}, "failures": []}
    rep = _run(gates.g4_missing_honesty, a, m)
    assert any("failures" in v["field"] for v in rep.violations)


def test_g4_fires_on_missing_field_with_source():
    a = {"competitors": [_comp(founded="", founded_source="https://x.io/about")]}
    rep = _run(gates.g4_missing_honesty, a, {"fetched": {}, "failures": []})
    assert any("founded" in v["field"] for v in rep.violations), (
        "缺失字段断言来源必须 hard"
    )


# ═══════════ G5 反伪造 ═══════════


def test_g5_fires_on_blacklisted_quote():
    a = {
        "competitors": [
            _comp(
                strengths=[
                    {
                        "point": "Pricing gets expensive at scale",
                        "evidence": "e",
                        "source": "https://x.io",
                    }
                ]
            )
        ]
    }
    rep = _run(gates.g5_antifabrication, a, {"fetched": {}})
    assert any("黑名单" in v["detail"] for v in rep.violations)


def test_g5_fires_on_repr_leak():
    a = {"competitors": [_comp(tagline="['Fast', 'Secure', 'Reliable']")]}
    rep = _run(gates.g5_antifabrication, a, {"fetched": {}})
    assert any("repr 泄漏" in v["detail"] for v in rep.violations)


def test_g5_fires_on_placeholder_in_opportunities():
    a = {"competitors": [], "opportunities": [{"title": "待补充"}]}
    rep = _run(gates.g5_antifabrication, a, {"fetched": {}})
    assert any("待补充" in v["detail"] for v in rep.violations)


# ═══════════ G6 URL 卫生 ═══════════


def test_g6_fires_on_malformed_url():
    a = {
        "competitors": [
            _comp(strengths=[{"point": "p", "evidence": "e", "source": "not-a-url"}])
        ]
    }
    rep = _run(gates.g6_url_hygiene, a, {"fetched": {}})
    assert any(v["gate"] == "G6" for v in rep.violations), "非法 URL 格式必须 hard"


def test_g6_warns_on_cross_domain_evidence():
    a = {
        "competitors": [
            _comp(
                strengths=[{"point": "p", "evidence": "e", "source": "https://g2.io/x"}]
            )
        ]
    }
    rep = _run(gates.g6_url_hygiene, a, {"fetched": {}})
    assert any("不同" in w["detail"] for w in rep.warnings)


# ═══════════ G7 溯源权威性 ═══════════


def test_g7_fires_on_tech_signal_anchored_at_pricing():
    a = {
        "competitors": [
            _comp(
                tech_signals=[
                    {
                        "name": "Kubernetes",
                        "quote": "runs on k8s clusters",
                        "source": "https://x.io/pricing",
                    }
                ]
            )
        ]
    }
    rep = _run(gates.g7_source_authority, a, {"fetched": {}})
    assert any(v["gate"] == "G7" for v in rep.violations), "技术信号锚定价页必须 hard"


def test_g7_passes_pricing_statement_on_pricing_anchor():
    """豁免语义:定价陈述(货币+数字)允许锚定价页。"""
    a = {
        "competitors": [
            _comp(
                tech_signals=[
                    {
                        "name": "起价",
                        "quote": "起步价 $59/月",
                        "source": "https://x.io/pricing",
                    }
                ]
            )
        ]
    }
    rep = _run(gates.g7_source_authority, a, {"fetched": {}})
    assert not any("tech_signals" in v["field"] for v in rep.violations), (
        "定价陈述锚 pricing 是合法的"
    )


def test_g7_fires_on_strength_anchored_at_root():
    a = {
        "competitors": [
            _comp(
                url="https://x.io",
                strengths=[
                    {"point": "协作能力", "evidence": "e", "source": "https://x.io"}
                ],
            )
        ]
    }
    rep = _run(gates.g7_source_authority, a, {"fetched": {}})
    assert any("strengths" in v["field"] for v in rep.violations), (
        "功能语义锚域名根必须 hard"
    )
