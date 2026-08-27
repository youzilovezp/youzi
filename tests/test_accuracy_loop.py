#!/usr/bin/env python3
"""§2/§3/§4a 新逻辑单测:充分性契约 / 引擎升级梯 / 深链解析 / 定价归一化。

跑法: python3 -m pytest tests/test_accuracy_loop.py -q
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.sufficiency import (  # noqa: E402
    assess_feedback,
    assess_pricing,
    assess_tech_signals,
    is_custom_tier,
    is_free_tier,
    is_no_period_tier,
    ladder_engines,
)
from scripts.deep_link import _ddg_redirect_url, _looks_like_shell  # noqa: E402
from scripts.crawl_competitors import _extract_pricing_evidence  # noqa: E402
from render import _synthesize_plans_from_tiers  # noqa: E402


class TestTierSemantics(unittest.TestCase):
    def test_free_by_name_and_price(self):
        self.assertTrue(is_free_tier("Free"))
        self.assertTrue(is_free_tier("Free", "$0"))
        self.assertTrue(is_free_tier("Starter Free"))

    def test_paid_enterprise_is_not_custom(self):
        # YCloud/WATI 都有真实标价的 Enterprise 付费档(历史误判事故)
        self.assertFalse(is_free_tier("Enterprise", "$399"))
        self.assertFalse(is_custom_tier("Enterprise", "$399"))
        self.assertFalse(is_no_period_tier("Enterprise", "$399"))

    def test_custom_by_contact_sales(self):
        self.assertTrue(is_custom_tier("Enterprise", "Contact sales"))
        self.assertTrue(is_custom_tier("定制版", ""))
        self.assertFalse(is_custom_tier("Growth", "$39"))


class TestAssessPricing(unittest.TestCase):
    def test_free_period_bug_fails(self):
        a = assess_pricing(
            [{"name": "Free", "price": "$0", "billing_period": "/yr"}], []
        )
        self.assertFalse(a["sufficient"])
        self.assertTrue(any("周期" in p for p in a["problems"]))

    def test_clean_plans_pass_with_cross_validation(self):
        tiers = [
            {"name": "Free", "price": "$0", "billing_period": "—"},
            {"name": "Pro", "price": "$89", "billing_period": "/mo"},
        ]
        votes = [{"engines": ["playwright", "trafilatura"], "independent_votes": 2}]
        self.assertTrue(assess_pricing(tiers, votes)["sufficient"])

    def test_single_engine_not_sufficient(self):
        tiers = [{"name": "Pro", "price": "$89", "billing_period": "/mo"}]
        votes = [{"engines": ["playwright"], "independent_votes": 1}]
        a = assess_pricing(tiers, votes)
        self.assertFalse(a["sufficient"])

    def test_honest_no_public_price_not_flagged(self):
        # 套餐名降级档(未能提取)不应触发"价格无货币符号"
        tiers = [{"name": "专业版", "price": "未能提取(见注)", "billing_period": "—"}]
        a = assess_pricing(tiers, [])
        self.assertTrue(all("货币" not in p for p in a["problems"]))


class TestLadderEngines(unittest.TestCase):
    def test_excludes_used(self):
        # 2026-08-27 重规划:pricing 首棒 = playwright+trafilatura+jina,
        # 升级梯增援 = crawl4ai/firecrawl/readability
        used = ["playwright", "trafilatura", "jina"]
        ladder = ladder_engines("pricing", used)
        self.assertEqual(set(ladder), {"crawl4ai", "firecrawl", "readability"})

    def test_docs_ladder(self):
        ladder = ladder_engines("docs", ["trafilatura"])
        self.assertEqual(ladder[:2], ["firecrawl", "crawl4ai"])


class TestAssessSignals(unittest.TestCase):
    def test_docs_index_page_insufficient(self):
        a = assess_tech_signals(
            [{"name": "Webhooks", "source": "https://docs.wati.io"}]
        )
        self.assertFalse(a["sufficient"])
        a2 = assess_tech_signals(
            [{"name": "Webhooks", "source": "https://docs.wati.io/"}]
        )
        self.assertFalse(a2["sufficient"])

    def test_docs_subpage_sufficient(self):
        a = assess_tech_signals(
            [
                {
                    "name": "Webhooks",
                    "source": "https://docs.wati.io/api-reference/webhook-events",
                }
            ]
        )
        self.assertTrue(a["sufficient"])

    def test_feedback_needs_source(self):
        self.assertFalse(assess_feedback([{"quote": "很好用"}])["sufficient"])
        self.assertTrue(
            assess_feedback([{"quote": "很好用", "source": "https://g2.com/x"}])[
                "sufficient"
            ]
        )


class TestDeepLinkParsing(unittest.TestCase):
    def test_ddg_redirect_decode(self):
        url = _ddg_redirect_url(
            "//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.wati.io%2Fwebhooks&rut=x"
        )
        self.assertEqual(url, "https://docs.wati.io/webhooks")

    def test_plain_url_passthrough(self):
        self.assertEqual(
            _ddg_redirect_url("https://example.com/a"), "https://example.com/a"
        )

    def test_shell_detection(self):
        self.assertTrue(_looks_like_shell("short"))
        self.assertTrue(
            _looks_like_shell("x" * 500 + " enable javascript and retry " + "y" * 100)
        )
        self.assertFalse(_looks_like_shell("# Docs\n\n" + "real content here. " * 50))


def _ev(markdowns):
    """构造多引擎 all_results 输入。"""
    return {
        "all_results": [
            {"scraper": f"e{i}", "success": True, "markdown": md}
            for i, md in enumerate(markdowns)
        ]
    }


class TestPricingNormalization(unittest.TestCase):
    def test_free_tier_period_stripped_and_plans_grouped(self):
        md = (
            "# Pricing\n## Free\n$0 /year\n## Growth\n$39 /month\n$468 /year\n"
            "## Pro\n$99 /month\n$999 /year\n"
        )
        ev = _extract_pricing_evidence(_ev([md, md]), "https://x.com/pricing")
        free = [t for t in ev["tiers"] if t["name"] == "Free"]
        self.assertTrue(free)
        for t in free:
            self.assertEqual(t["billing_period"], "—", "Free 档不得携带周期")
        plans = {p["name"]: p for p in ev["plans"]}
        self.assertEqual(plans["Growth"]["monthly"], "$39")
        self.assertEqual(plans["Growth"]["annual"], "$468")
        self.assertEqual(plans["Growth"]["annual_monthly_equiv"], "$39")
        self.assertNotIn("save_pct", plans["Growth"], "$39×12=$468 无折扣")
        # Pro: $99×12=$1188 > 年付 $999 → 省 16%
        self.assertEqual(plans["Pro"]["save_pct"], 16)

    def test_no_dangling_pipe_in_display(self):
        md = "## Growth\n$39 /month\n$468 /year\n"
        ev = _extract_pricing_evidence(_ev([md, md]), "https://x.com/pricing")
        self.assertNotIn("| ", ev["pricing"])
        self.assertNotIn(" |", ev["pricing"])

    def test_paid_enterprise_keeps_period(self):
        md = "## Enterprise\n$399 /month\n$4788 /year\n"
        ev = _extract_pricing_evidence(_ev([md, md]), "https://x.com/pricing")
        ent = [p for p in ev["plans"] if p["name"] == "Enterprise"]
        self.assertTrue(ent)
        self.assertFalse(ent[0].get("is_custom"))
        self.assertEqual(ent[0]["monthly"], "$399")

    def test_compound_period_kept_raw(self):
        md = "## Growth\n$59 (month billed annually)\n$69 (billed monthly)\n"
        ev = _extract_pricing_evidence(_ev([md, md]), "https://x.com/pricing")
        periods = {t["billing_period"] for t in ev["tiers"] if t["name"] == "Growth"}
        # billed annually = 年结算月价,保留原文;绝配不出假年付折扣
        self.assertEqual(periods, {"billed annually", "/mo"})
        g = next(p for p in ev["plans"] if p["name"] == "Growth")
        self.assertEqual(g["monthly"], "$69")
        self.assertEqual(g.get("monthly_billed"), "$59")
        self.assertEqual(g.get("annual"), "")


class TestRenderSynthesizePlans(unittest.TestCase):
    def test_from_old_tiers(self):
        tiers = [
            {"name": "Growth", "price": "$39", "billing_period": "/mo"},
            {"name": "Growth", "price": "$468", "billing_period": "/yr"},
            {"name": "Free", "price": "$0", "billing_period": "—"},
            {"name": "Enterprise", "price": "Contact sales", "billing_period": "—"},
        ]
        plans = {p["name"]: p for p in _synthesize_plans_from_tiers(tiers)}
        self.assertEqual(plans["Growth"]["monthly"], "$39")
        self.assertEqual(plans["Growth"]["annual"], "$468")
        # $39×12=$468 无折扣 → 不出 save_pct 键
        self.assertNotIn("save_pct", plans["Growth"])
        self.assertTrue(plans["Free"]["is_free"])
        self.assertTrue(plans["Enterprise"]["is_custom"])

    def test_raw_period_texts_normalized(self):
        tiers = [
            {"name": "Team", "price": "$79", "billing_period": "monthly"},
            {"name": "Team", "price": "$948", "billing_period": "annually"},
        ]
        plans = {p["name"]: p for p in _synthesize_plans_from_tiers(tiers)}
        self.assertEqual(plans["Team"]["monthly"], "$79")
        self.assertEqual(plans["Team"]["annual"], "$948")


if __name__ == "__main__":
    unittest.main()
