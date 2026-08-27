# tests/test_report_v2.py
# -*- coding: utf-8 -*-
"""V2 报告:定价区优雅降级(无空年付列)+ Custom 语义块。"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _render(analysis: dict) -> str:
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "a.json"
        out = Path(td) / "r.html"
        src.write_text(json.dumps(analysis, ensure_ascii=False), encoding="utf-8")
        r = subprocess.run(
            [
                sys.executable,
                str(ROOT / "render.py"),
                "--input",
                str(src),
                "--output",
                str(out),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert r.returncode == 0, r.stderr[-2000:]
        return out.read_text(encoding="utf-8")


def _minimal_comp(name, tiers, currency="USD"):
    return {
        "name": name,
        "url": f"https://{name.lower()}.io",
        "tagline": "t",
        "pricing": "$39/月起",
        "pricing_currency": currency,
        "pricing_tiers": tiers,
        "core_features": ["f1", "f2", "f3"],
        "strengths": [],
        "weaknesses": [],
        "differentiators": [],
        "tech_signals": [],
        "scores": {
            "feature_richness": 5,
            "ux": 5,
            "pricing_value": 5,
            "integration": 5,
            "ai_capability": 5,
            "momentum": 5,
        },
    }


def _analysis(comps):
    # render.py self_check 门禁(不可降阈值):竞品 ≥ 3 + source_count > 0,
    # 单竞品最小数据 exit 2 → 补 2 个带 pricing_source 的最小填充竞品。
    # Pad B 走 pricing_plans(新数据契约字段)携带空 custom_note 的定制档,
    # 覆盖「Custom 档无备注 → 模板兜底"联系销售报价"」的 V2 语义。
    pads = [
        {
            "name": "Pad A",
            "url": "https://pada.io",
            "tagline": "t",
            "pricing": "$9/月起",
            "pricing_currency": "USD",
            "pricing_source": "https://pada.io/pricing",
            "pricing_tiers": [
                {
                    "name": "Solo",
                    "price": "$9",
                    "billing_period": "/mo",
                    "source_url": "https://pada.io/pricing",
                }
            ],
            "core_features": ["f1"],
            "strengths": [],
            "weaknesses": [],
            "differentiators": [],
            "tech_signals": [],
            "scores": {
                "feature_richness": 5,
                "ux": 5,
                "pricing_value": 5,
                "integration": 5,
                "ai_capability": 5,
                "momentum": 5,
            },
        },
        {
            "name": "Pad B",
            "url": "https://padb.io",
            "tagline": "t",
            "pricing": "—",
            "pricing_currency": "USD",
            "pricing_source": "https://padb.io/pricing",
            "pricing_plans": [
                {
                    "name": "Enterprise",
                    "monthly": "",
                    "annual": "",
                    "other_note": "",
                    "save_pct": None,
                    "annual_monthly_equiv": "",
                    "is_free": False,
                    "is_custom": True,
                    "custom_note": "",
                }
            ],
            "core_features": ["f1"],
            "strengths": [],
            "weaknesses": [],
            "differentiators": [],
            "tech_signals": [],
            "scores": {
                "feature_richness": 5,
                "ux": 5,
                "pricing_value": 5,
                "integration": 5,
                "ai_capability": 5,
                "momentum": 5,
            },
        },
    ]
    opportunities = [{"title": f"机会{i}", "disrupt_score": 8} for i in (1, 2, 3)]
    return {
        "topic": "T",
        "competitors": list(comps) + pads,
        "market_segments": [],
        "gaps": [],
        "opportunities": opportunities,
        "executive_summary": "s",
    }


def test_monthly_only_no_annual_column():
    tiers = [
        {
            "name": "Growth",
            "price": "$39",
            "billing_period": "/mo",
            "source_url": "https://x.io/pricing",
        },
        {
            "name": "Pro",
            "price": "$89",
            "billing_period": "/mo",
            "source_url": "https://x.io/pricing",
        },
    ]
    html = _render(_analysis([_minimal_comp("Alpha", tiers)]))
    # 头行只有 套餐|月付 两列语义:pp-head 内不出现"年付"
    import re

    head = re.search(r'class="pp-row pp-head[^"]*">(.*?)</div>', html, re.S)
    assert head, "pp-head 必须存在"
    assert "年付" not in head.group(1), "无年付数据时不得渲染年付列"


def test_monthly_annual_shows_both_and_save():
    tiers = [
        {
            "name": "Pro",
            "price": "$39",
            "billing_period": "/mo",
            "source_url": "https://x.io/pricing",
        },
        {
            "name": "Pro",
            "price": "$390",
            "billing_period": "/yr",
            "source_url": "https://x.io/pricing",
        },
    ]
    html = _render(_analysis([_minimal_comp("Beta", tiers)]))
    assert "年付" in html and "省 1" in html  # (1-390/468)*100 ≈ 17%


def test_annual_only_no_monthly_column():
    """反向降级:只有年付结算价(billed annually)→ 套餐|年付 两列,无月付空列。"""
    import re

    tiers = [
        {
            "name": "Pro",
            "price": "$24",
            "billing_period": "billed annually",
            "source_url": "https://x.io/pricing",
        },
        {
            "name": "Pro Plus",
            "price": "$48",
            "billing_period": "billed annually",
            "source_url": "https://x.io/pricing",
        },
    ]
    html = _render(_analysis([_minimal_comp("CodeRabbit", tiers)]))
    head = re.search(r'class="pp-row pp-head[^"]*">(.*?)</div>', html, re.S)
    assert head, "pp-head 必须存在"
    assert "月付" not in head.group(1), "无月付数据时不得渲染月付列"
    assert "年付" in head.group(1)
    assert "annual-only" in html
    assert "年付结算月价" in html


def test_no_price_card_shows_notice_not_empty_table():
    """mo-only 卡所有付费计划无任何价格 → 整卡「未获取到公开价格」提示行。"""
    comp = _minimal_comp("CodeGeeX", [])
    comp["pricing"] = "未能获取,请核对官网"
    comp["pricing_plans"] = [
        {
            "name": "个人版",
            "monthly": "",
            "annual": "",
            "other_note": "",
            "is_free": False,
            "is_custom": False,
            "custom_note": "",
        }
    ]
    html = _render(_analysis([comp]))
    seg = html.split("<strong>CodeGeeX</strong>", 1)[1].split("pc-foot", 1)[0]
    assert "未获取到公开价格" in seg
    assert "pp-head" not in seg, "无价格时不得渲染空「—」表格"
    assert "pp-empty" not in seg


def test_custom_tier_semantic_block():
    tiers = [
        {
            "name": "Enterprise",
            "price": "Custom",
            "billing_period": "",
            "source_url": "https://x.io/pricing",
        }
    ]
    html = _render(_analysis([_minimal_comp("Gamma", tiers)]))
    assert "pc-custom" in html and "联系销售" in html
    # custom 不出现在月/年数据行里
    assert (
        'class="pp-row"' not in html
        or "Custom" not in html.split("pc-custom")[0].split("pp-head")[-1]
    )
