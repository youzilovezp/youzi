#!/usr/bin/env python3
"""tests/test_pricing_extract.py · 定价提取回归测试

真实事故驱动的刁钻样例(全部来自历史 bug 报告):
1. 融资额 "$62.5M Series B" 不是价格
2. 划线促销价 ~~$99~~ now $79 → 取现价 $79
3. HK$/NT$/¥ 币种不塌缩、¥ 中文页判 CNY
4. "per user/month" 组合周期
5. 多价摘要行不吞分档集群
6. credits/附加项不算套餐价
7. render 端:pricing_verified 缺失默认 False、占位 SWOT 不进派生板块

运行:python3 tests/test_pricing_extract.py
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.crawl_competitors import (  # noqa: E402
    _cache_fresh,
    _content_hash,
    _detect_currency,
    _extract_price,
    _extract_price_lines,
    _extract_pricing_evidence,
    _normalize_price_token,
)


class TestFundingNotPrice(unittest.TestCase):
    def test_funding_round_excluded(self):
        md = "# About us\nWe just raised $62.5M in Series B funding!\n## Pricing\nGrowth $39/mo"
        got = _extract_price(md)
        self.assertNotIn("$62.5", got.replace("$62.5M", ""))
        self.assertIn("$39", got)

    def test_revenue_excluded(self):
        md = "Trusted by 500+ brands. Earned $600k revenue. Starter $49/month"
        got = _extract_price(md)
        self.assertNotIn("$600", got)
        self.assertIn("$49", got)


class TestPromoPrice(unittest.TestCase):
    def test_strikethrough_old_price_ignored(self):
        md = "## Growth\n~~$99~~ now **$79**/mo\nbilled monthly"
        lines = _extract_price_lines(md)
        toks = [ln["price"] for ln in lines]
        joined = " ".join(toks)
        self.assertNotIn("$99", joined)
        self.assertIn("$79", joined)


class TestCurrency(unittest.TestCase):
    def test_hkd_not_collapsed_to_usd(self):
        self.assertEqual(_normalize_price_token("HK$348/year"), "HK$348")

    def test_ntd_ca_au_prefixed(self):
        self.assertEqual(_normalize_price_token("NT$990"), "NT$990")
        self.assertEqual(_normalize_price_token("CA$29"), "CA$29")
        self.assertEqual(_normalize_price_token("A$39"), "A$39")

    def test_usd_sgd_distinct(self):
        self.assertEqual(_normalize_price_token("US$39"), "US$39")
        self.assertEqual(_normalize_price_token("S$39"), "S$39")
        self.assertEqual(_normalize_price_token("$39"), "$39")

    def test_yuan_cny_when_chinese(self):
        self.assertEqual(_detect_currency("¥999 /月 中文定价"), "CNY")
        self.assertEqual(_detect_currency("¥3,000/mo"), "JPY")

    def test_hkd_currency_detect(self):
        self.assertEqual(_detect_currency("HK$348 月費"), "HKD")


class TestPeriod(unittest.TestCase):
    def test_per_user_month_combined(self):
        md = "Growth $39 per user/month billed annually"
        lines = _extract_price_lines(md)
        periods = [ln["period"] for ln in lines]
        self.assertTrue(
            any("user" in p and "month" in p for p in periods),
            f"组合周期丢失: {periods}",
        )


class TestMultiPriceCluster(unittest.TestCase):
    def test_summary_line_does_not_eat_tiers(self):
        # 导航摘要行(多 token)不应挤掉真正的分档行
        md = (
            "Choose your plan: Starter $39 Pro $99\n"  # 摘要行
            "## Starter\n**$39**\nper month\n"           # 真分档 1
            "## Pro\n**$99**\nper month\n"                # 真分档 2
        )
        r = {"all_results": [
            {"scraper": "e1", "success": True, "markdown": md},
            {"scraper": "e2", "success": True,
             "markdown": "## Starter\n$39 /month\n## Pro\n$99 /month"},
        ]}
        ev = _extract_pricing_evidence(r, "https://x.com/pricing")
        toks = " ".join(t["price"] for t in ev["tiers"])
        self.assertIn("$39", toks)
        self.assertIn("$99", toks)
        self.assertEqual(len([t for t in ev["tiers"] if t["price"] in ("$39", "$99")]), 2,
                         f"分档被吞: {ev['tiers']}")


class TestCreditsAndAddons(unittest.TestCase):
    def test_credits_not_tier(self):
        md = "Free plan includes $15 monthly credits. Developer plan $0/month"
        got = _extract_price(md)
        self.assertNotIn("$15", got)


class TestRenderEvidenceEnforcement(unittest.TestCase):
    """render.py 证据强制:缺失字段不得静默绕过。"""

    def _minimal_analysis(self):
        return {
            "topic": "测试主题",
            "executive_summary": "x",
            "competitors": [
                {
                    "name": "A",
                    "url": "https://a.com",
                    "pricing": "Pro $39/月",
                    # 故意不写 pricing_verified / strengths source
                },
                {"name": "B", "url": "https://b.com", "pricing": "—"},
                {"name": "C", "url": "https://c.com", "pricing": "Free"},
            ],
            "opportunities": [],
            "gaps": [],
        }

    def test_pricing_verified_defaults_false(self):
        from render import normalize

        data = normalize(self._minimal_analysis())
        for c in data["competitors"]:
            self.assertIn("pricing_verified", c)
            self.assertIs(c["pricing_verified"], False)

    def test_placeholder_swot_not_derived(self):
        from render import normalize

        data = normalize(self._minimal_analysis())
        # 占位 strengths 不应派生任何"可借鉴实践"
        for items in data["inspiration_points"].values():
            for it in items:
                self.assertNotIn("待补充", it.get("inspiration", ""))
        for items in data["opportunity_points"].values():
            for it in items:
                self.assertNotIn("待补充", it.get("opportunity", ""))

    def test_evidence_warnings_when_no_sources(self):
        from render import normalize

        data = normalize(self._minimal_analysis())
        self.assertTrue(data["evidence_warnings"], "0 来源必须产生警告")
        self.assertTrue(any("没有任何带 URL" in w for w in data["evidence_warnings"]))
        self.assertTrue(any("未经" in w and "交叉验证" in w for w in data["evidence_warnings"]))

    def test_scores_not_fabricated(self):
        from render import normalize

        raw = self._minimal_analysis()
        raw["competitors"][0]["scores"] = {k: 5 for k in (
            "feature_richness", "ux", "pricing_value", "integration",
            "ai_capability", "momentum",
        )}
        data = normalize(raw)
        # 全相等默认分 → 标记低置信,不得被关键词启发式"发明"出新分数
        self.assertEqual(data["competitors"][0]["scores_confidence"], "low")
        self.assertEqual(set(data["competitors"][0]["scores"].values()), {5})


class TestRealCrawlRegressions(unittest.TestCase):
    """2026-08-26 真实爬取(ycloud/sleekflow/wati/respond.io/meetbot)发现的事故。"""

    def test_usd_not_sgd_on_us_prefix(self):
        # Sleekflow: "US$149" 含子串 "S$" → 整站被标 SGD
        self.assertEqual(_detect_currency("Pro AI · US$149 (/month)", ["US$149"]), "USD")
        self.assertEqual(_detect_currency("S$58", ["S$58"]), "SGD")
        self.assertEqual(_detect_currency("HK$348", ["HK$348"]), "HKD")

    def test_currency_dominant_from_tiers(self):
        # WATI: 页面混 ₹999 充值 promo,tiers 是 $59/$119 → USD 不是 INR
        self.assertEqual(_detect_currency("... ₹999 ... $59 $119 ...", ["$119", "$59"]), "USD")

    def test_addon_window_filter(self):
        # WATI: playwright 把 "$4.99/Month" 与 "Shopify addon" 拆成相邻行
        md = "## Pricing\n$4.99/Month\nShopify addon\nGrowth\n$59/mo"
        lines = _extract_price_lines(md)
        prices = [ln["price"] for ln in lines]
        self.assertNotIn("$4.99", prices)
        self.assertIn("$59", prices)

    def test_credits_deposit_not_tier(self):
        # WATI 印度充值:"Pay ₹999 to get started & get ₹999 back as message credits"
        md = "## **₹999**\nPay ₹999 to get started & get ₹999 back as message credits"
        lines = _extract_price_lines(md)
        self.assertEqual(lines, [], f"充值 promo 混入: {lines}")

    def test_free_trial_not_tier(self):
        # WATI: "7 days free trial" 4 引擎全票但不是定价档
        md = "Growth\n$59/mo\n7 days free trial, zero setup fees and affordable pricing"
        r = {"all_results": [
            {"scraper": f"e{i}", "success": True, "markdown": md} for i in range(3)
        ]}
        ev = _extract_pricing_evidence(r, "https://x.com/pricing")
        names_prices = [(t["name"], t["price"]) for t in ev["tiers"]]
        self.assertNotIn("free trial", " ".join(p for _, p in names_prices).lower())

    def test_plan_name_from_bare_line(self):
        # YCloud: "Enterprise" 是裸行不是 # 标题,套餐名曾全部丢失
        md = "Growth\n$39 /mo\nBilled $468 /yr\n\nEnterprise\n\nFor large companies\n\n$399 /mo"
        lines = _extract_price_lines(md)
        plans = {ln["price"]: ln["plan"] for ln in lines}
        self.assertEqual(plans.get("$399"), "Enterprise")

    def test_marketing_heading_not_plan(self):
        # YCloud: "Find your right plan for business" 含 "business" 被当套餐名
        md = "## Find your right plan for business\n\nBilled $0 /yr (Current plan)"
        lines = _extract_price_lines(md)
        for ln in lines:
            self.assertNotIn("Find your right plan", ln["plan"])

    def test_addon_label_two_lines_above(self):
        # WATI 中文区真实形态:"Shopify Integration" 标签在裸价格行上方 2 行
        md = (
            "};\n\nShopify Integration\n\n$4.99/Month\n\nAdditional WhatsApp Numbers\n"
            "## **Growth**\nReach thousands\n$59/mo"
        )
        lines = _extract_price_lines(md)
        prices = [ln["price"] for ln in lines]
        self.assertNotIn("$4.99", prices)
        self.assertIn("$59", prices)

    def test_addon_does_not_bleed_across_card(self):
        # 套餐标题是卡片边界:上一张卡的 addon 行不能误杀下一张卡的真价格
        md = (
            "$ - Requires purchase of $4.99/month Shopify addon\n"
            "## **Growth**\nReach thousands of customers\n$59/mo\nbilled annually"
        )
        lines = _extract_price_lines(md)
        prices = [ln["price"] for ln in lines]
        self.assertIn("$59", prices)

    def test_tiers_sorted_by_price(self):
        md = "Pro\n$89/mo\nGrowth\n$39/mo\nFree\n$0"
        r = {"all_results": [{"scraper": "e1", "success": True, "markdown": md}]}
        ev = _extract_pricing_evidence(r, "u")
        import re as _re
        vals = []
        for t in ev["tiers"]:
            m = _re.search(r"(\d[\d,]*)", t["price"])
            if m:
                vals.append(float(m.group(1).replace(",", "")))
        self.assertEqual(vals, sorted(vals), f"未按价格升序: {ev['tiers']}")


class TestPromoAndBillingMerge(unittest.TestCase):
    """商业分析板块价格准确性(2026-08-26 第二轮真实爬取发现)。"""

    def test_promo_old_price_first_new_last(self):
        # respond.io 真实格式:"$1,188 $948/yr (billed yearly)" —— 旧价在前
        md = "## starter\n$\n79/month\n$1,188 $948/yr (billed yearly)"
        lines = _extract_price_lines(md)
        got = [(ln["price"], ln["period"]) for ln in lines]
        self.assertIn(("$948", "/yr"), got, f"促销取旧价/周期错: {got}")

    def test_billing_variants_keep_raw_periods(self):
        # 同套餐多计费选项:各自成行,保留原始周期(不再"或"合并 ——
        # $59 billed-annually 与 $69 billed-monthly 合并成 "月付/年付"
        # 会把月价标成年价,历史语义事故)
        md = "## Growth\n$59 (month billed annually)\n$69 (billed monthly)\n## Pro\n$119/month"
        r = {"all_results": [
            {"scraper": "e1", "success": True, "markdown": md},
            {"scraper": "e2", "success": True, "markdown": md},
        ]}
        ev = _extract_pricing_evidence(r, "https://x.com/pricing")
        growth = [t for t in ev["tiers"] if t["name"].lower() == "growth"]
        self.assertEqual(len(growth), 2, f"计费变体应各成行: {ev['tiers']}")
        periods = {t["billing_period"] for t in growth}
        self.assertTrue(any("annually" in p for p in periods), periods)
        self.assertTrue(any("monthly" in p for p in periods), periods)
        for t in ev["tiers"]:
            self.assertNotIn("或", t["price"])

    def test_display_no_duplicate_unpublished(self):
        # Meetbot:三个"未公开"只显示一次
        from render import _derive_commercial_strategies
        c = {
            "name": "M",
            "pricing": "专业版 / 企业版 / 定制版（价格未公开，需联系销售）",
            "pricing_tiers": [
                {"name": "专业版", "price": "未公开", "billing_period": "—"},
                {"name": "企业版", "price": "未公开", "billing_period": "—"},
                {"name": "定制版", "price": "未公开", "billing_period": "—"},
            ],
            "differentiators": [],
        }
        cs = _derive_commercial_strategies(c)
        self.assertEqual(cs["pricing_display"].count("未公开"), 1, cs["pricing_display"])

    def test_per_user_annotation(self):
        from render import _derive_commercial_strategies
        c = {
            "name": "R",
            "pricing": "starter $79/month",
            "pricing_unit": "per user",
            "pricing_tiers": [
                {"name": "starter", "price": "$79", "billing_period": "per user · /month"},
            ],
            "differentiators": [],
        }
        cs = _derive_commercial_strategies(c)
        self.assertIn("per user", cs["pricing_display"])


class TestCitationRefactor(unittest.TestCase):
    """角标系统:每个论断一个来源条目(2026-08-26 重构)。"""

    def test_same_url_different_claims_get_distinct_refs(self):
        from render import normalize
        raw = {
            "topic": "t", "executive_summary": "x",
            "competitors": [
                {"name": "A", "url": "https://a.com", "tagline": "T",
                 "tagline_source": "https://a.com",
                 "gtm_evidence": [{"name": "自助试用", "quote": "q", "source": "https://a.com"}],
                 "moat_evidence": [{"name": "SOC2", "quote": "q2", "source": "https://a.com"}],
                 "pricing": "—"},
                {"name": "B", "url": "https://b.com", "pricing": "—"},
                {"name": "C", "url": "https://c.com", "pricing": "—"},
            ],
            "opportunities": [], "gaps": [],
        }
        data = normalize(raw)
        # 同 URL 三个不同 claim → 3 个不同角标
        refs = set()
        a = data["competitors"][0]
        refs.add(a["_refs"]["tagline"])
        refs |= {e["_ref"] for e in a["gtm_evidence"] if e.get("_ref")}
        refs |= {e["_ref"] for e in a["moat_evidence"] if e.get("_ref")}
        self.assertGreaterEqual(len(refs), 3, f"角标未按论断区分: {refs}")
        # 每个角标在来源区的 claim 与论断一致
        by_idx = {s2["idx"]: s2 for s2 in data["sources"]}
        for e in a["gtm_evidence"]:
            self.assertIn("GTM", by_idx[e["_ref"]]["claim"])
        for e in a["moat_evidence"]:
            self.assertIn("护城河", by_idx[e["_ref"]]["claim"])


class TestCacheTTL(unittest.TestCase):
    """F1: 14 天 TTL 此前定义了但从未生效 —— 过期缓存必须视为 miss。"""

    def test_fresh_cache(self):
        import time as _t
        ts = _t.strftime("%Y-%m-%d %H:%M UTC", _t.gmtime())
        self.assertTrue(_cache_fresh({"scraped_at": ts}))

    def test_stale_cache(self):
        self.assertFalse(_cache_fresh({"scraped_at": "2026-01-01 00:00 UTC"}))

    def test_garbage_timestamp(self):
        self.assertFalse(_cache_fresh({"scraped_at": "???"}))
        self.assertFalse(_cache_fresh({}))


def _mk_result(engine_mds: dict) -> dict:
    """构造 scrape_smart 形态的 result:engine → markdown。"""
    return {
        "success": bool(engine_mds),
        "all_results": [
            {"scraper": e, "success": True, "markdown": md}
            for e, md in engine_mds.items()
        ],
    }


class TestEngineIndependence(unittest.TestCase):
    """F5: 两引擎内容哈希相同(同一反爬变体)不得交叉验证。"""

    PRICING_MD_A = "# Pricing\nGrowth $59/mo\nPro $119/mo\n"
    PRICING_MD_B = "# Plans\nGrowth $59 /mo\nPro $119 /mo\n"

    def test_identical_content_not_verified(self):
        ev = _extract_pricing_evidence(
            _mk_result({"playwright": self.PRICING_MD_A,
                        "crawl4ai": self.PRICING_MD_A}),  # 完全相同 = 变体互证
            "https://x.com/pricing",
        )
        self.assertFalse(ev["verified"])
        self.assertEqual(ev["vote_detail"][0]["independent_votes"], 1)

    def test_different_content_verified(self):
        ev = _extract_pricing_evidence(
            _mk_result({"playwright": self.PRICING_MD_A,
                        "crawl4ai": self.PRICING_MD_B}),
            "https://x.com/pricing",
        )
        self.assertTrue(ev["verified"])
        self.assertEqual(ev["vote_detail"][0]["independent_votes"], 2)

    def test_source_url_empty_when_no_evidence(self):
        """F2: 全引擎无价格行时 source_url 必须为空(不得让 404 URL 当来源)。"""
        ev = _extract_pricing_evidence(
            _mk_result({"playwright": "nothing here",
                        "crawl4ai": "no prices"}),
            "https://x.com/pricing-404",
        )
        self.assertEqual(ev["source_url"], "")

    def test_content_hash_stable(self):
        h1 = _content_hash("a  b\nc")
        h2 = _content_hash("a b   c")
        self.assertEqual(h1, h2)
        self.assertNotEqual(h1, _content_hash("different"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
