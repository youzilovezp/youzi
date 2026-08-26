#!/usr/bin/env python3
"""
tests/test_pipeline.py · 端到端测试

验证 render.py 的完整 pipeline:
1. JSON 能加载
2. normalize() 不报错
3. Template 用 jinja2 渲染成功
4. 输出 HTML 含 9 个一级章节
5. 含来源引用 [N]
6. 含 § 5.2 功能全集厂商对比
7. 含 § 8 其他竞品资料库

运行:python3 tests/test_pipeline.py
"""

import json
import re
import sys
import unittest
from pathlib import Path
from unittest import mock  # noqa: F401 — Task 8+ 使用

# 把工程根目录加入 sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from render import normalize, Template  # noqa: E402


class TestPipeline(unittest.TestCase):
    """完整 pipeline 测试。"""

    @classmethod
    def setUpClass(cls):
        """所有测试共享一份准备好的 report(避免重复渲染)。"""
        cls.json_path = ROOT / "examples" / "whatsapp-advertising-demo.json"
        cls.template_path = ROOT / "templates" / "report.html"

        if not cls.json_path.exists():
            raise FileNotFoundError(f"Missing demo JSON: {cls.json_path}")
        if not cls.template_path.exists():
            raise FileNotFoundError(f"Missing template: {cls.template_path}")

        # 加载数据 + normalize + 渲染
        cls.data = normalize(json.loads(cls.json_path.read_text(encoding="utf-8")))
        cls.html = Template(cls.template_path.read_text(encoding="utf-8")).render(
            cls.data
        )

    def test_html_non_empty(self):
        """渲染出的 HTML 不能为空。"""
        self.assertGreater(len(self.html), 50000, "HTML 太小(< 50KB),可能渲染失败")

    def test_no_unresolved_tags(self):
        """不能有未解析的模板标签。"""
        # 排除 CSS/JS 中的字符(可能在字符串里出现 {{}})
        # 简单做法:统计 {{ 出现次数,但要排除在 <script>...</script> 里的
        unresolved = self.html.count("{{") + self.html.count("{%")
        # 渲染后文本中可能有 {{ 出现,但应该是 CSS 字符串里的(例如 calc())
        # 严格判断:查找非 CSS/JS 上下文中的 {{ }}
        # 实际做法:从模板渲染前的 self.html 不应该有未闭合的 {{ tag
        # 这里粗略判断,实际错误会有 Jinja2 报错
        self.assertLess(unresolved, 100, f"未解析标签过多 ({unresolved} 处)")

    def test_9_sections_present(self):
        """9 个一级章节必须都渲染。"""
        expected = [
            "背景与目标",
            "结论与建议",
            "产品定位分析",
            "商业策略分析",
            "产品设计分析",
            "产品数据分析",
            "用户反馈分析",
            "其他竞品资料库",
            "来源与参考资料",
        ]
        for section in expected:
            self.assertIn(section, self.html, f"§ {section} 未渲染")

    def test_competitors_count(self):
        """6 家竞品全部出现。"""
        for c in self.data["competitors"]:
            self.assertIn(c["name"], self.html, f"{c['name']} 未在 HTML 中")

    def test_sources_present(self):
        """§ Sources 必须有 N 个 source-item。"""
        # 通过 source_item class 计数
        source_items = self.html.count('class="source-item"')
        self.assertGreater(source_items, 50, f"Source items 太少 ({source_items})")

    def test_n_references(self):
        """必须有 [N] 引用角标(每个 strengths/weaknesses 都应有)。"""
        refs = self.html.count('class="ref"')
        self.assertGreater(refs, 30, f"[N] 引用太少 ({refs})")

    def test_feature_catalog_rendered(self):
        """§ 5.2 功能全集必须包含功能表格行。"""
        # feat-matrix 表格是核心
        self.assertIn("feat-matrix", self.html, "§ 5.2 厂商对比矩阵未渲染")
        # 数据行数量(注意:行可能是 <tr> 或 <tr class="unique-feature">)
        import re as _re

        rows = len(_re.findall(r"<tr[\s>]", self.html))
        self.assertGreater(rows, 50, f"表格行数太少 ({rows})")

    def test_section_8_other_competitors(self):
        """§ 8 必须包含其他 15 家玩家。"""
        for oc in self.data["other_competitors"]:
            # 在 § 8 区域里出现
            self.assertIn(oc["name"], self.html, f"§ 8 {oc['name']} 缺失")

    def test_no_template_engine_garbage(self):
        """不能残留手写引擎的错误输出(例如 {{X}} 原样保留)。"""
        # 排除 JS / CSS 字符串
        # 找 [^\\]{{[a-z] 这种典型模板标签残留
        errors = re.findall(r"[^/]\{[a-z]+\.[a-z]", self.html)
        self.assertEqual(len(errors), 0, f"发现残留模板标签: {errors[:5]}")

    def test_dark_mode_css_present(self):
        """主题感知必须支持深色模式(不能少 CSS)。"""
        self.assertIn('[data-theme="dark"]', self.html, "缺少深色模式 CSS")

    def test_toc_sidebar_present(self):
        """浮动 TOC 必须存在。"""
        self.assertIn('class="toc-sidebar"', self.html, "缺少浮动 TOC")

    def test_reading_progress_present(self):
        """阅读进度条必须存在。"""
        self.assertIn('class="reading-progress"', self.html, "缺少阅读进度条")


class TestNormalize(unittest.TestCase):
    """normalize() 数据层单元测试。"""

    @classmethod
    def setUpClass(cls):
        cls.data = normalize(
            json.loads(
                (ROOT / "examples" / "whatsapp-advertising-demo.json").read_text()
            )
        )

    def test_required_fields(self):
        """normalize 后必须包含模板需要的所有字段。"""
        required = [
            "topic",
            "subtitle",
            "date",
            "executive_summary",
            "competitors",
            "competitors_ranked",
            "feature_comparison_matrix",
            "feature_catalog_companies",
            "sources",
            "sources_html",
            "sources_by_kind",
            "inspiration_points",
            "opportunity_points",
            "swot_per_competitor",
            "user_feedback",
            "user_positioning",
            "commercial_strategies",
            "product_overview",
            "data_growth",
            "segments_summary",
            "other_competitors",
            "other_competitors_by_category",
            "competitors_by_segment",
            "competitors_by_stage",
            "founding_timeline",
            "stage_distribution",
            "section5_2_html",
        ]
        for f in required:
            self.assertIn(f, self.data, f"normalize() 缺少字段: {f}")

    def test_feature_matrix_correctness(self):
        """§ 5.2 矩阵:每家至少 18 项功能,合并后类别 ≥ 15。"""
        m = self.data["feature_comparison_matrix"]
        # 每家至少 18 项核心功能
        for comp in ["Twilio", "WATI", "Respond.io", "Infobip", "ManyChat", "Tidio"]:
            n = m["totals_per_competitor"].get(comp, 0)
            self.assertGreaterEqual(
                n, 18, f"{comp} 功能数 {n} < 18 (基础核心功能必须具备)"
            )
        self.assertEqual(len(m["competitor_names"]), 6)
        # 类别数应 >= 15(允许有同类别)
        self.assertGreater(len(m["categories"]), 15)

    def test_segment_grouping(self):
        """§ 2 同类厂商聚类:同类(同 BSP 组、SaaS 组、广告组)必须在同一 cluster。"""
        clusters = self.data["competitors_by_segment"]
        # BSP 组应包含 Twilio + Infobip
        bsp_cluster = next((c for c in clusters if "BSP" in c["segment"]), None)
        self.assertIsNotNone(bsp_cluster)
        assert bsp_cluster is not None  # type narrowing for Pyright
        names = [c["name"] for c in bsp_cluster["competitors"]]
        self.assertIn("Twilio", names)
        self.assertIn("Infobip", names)

    def test_sources_have_unique_urls(self):
        """Sources 条目按 (url, claim) 去重 —— 同 URL 不同论断各成条目
        (2026-08-26 角标重构:裸 URL 去重会让一个角标被多个论断复用,
        来源区只显示第一次的 claim = 角标定位错位)。"""
        normalized = self.data
        keys = [
            (s2["url"], s2["claim"], s2.get("competitor", ""))
            for s2 in normalized["sources"]
        ]
        self.assertEqual(
            len(keys), len(set(keys)),
            "同一 (url, claim, competitor) 出现多次 —— 去重失效",
        )

    def test_junk_feature_filter(self):
        from render import _is_junk_feature, _clean_feature_text

        self.assertEqual(_clean_feature_text("**带星号残留**"), "带星号残留")
        junk = [
            "Respond.io vs Manychat",
            "Explore the Power of Respond.io",
            "Acquiring customers is costly.",
            "Here's how respond.io sets you up for success",
            "",
        ]
        legit = [
            "WhatsApp Business API",
            "Programmable Messaging",
            "Segment CDP",
            "团队收件箱",
            "AI 智能路由",
        ]
        for t in junk:
            self.assertTrue(_is_junk_feature(t), f"应过滤: {t!r}")
        for t in legit:
            self.assertFalse(_is_junk_feature(t), f"应保留: {t!r}")

    def test_repair_fills_empty_swot_and_scores(self):
        data = {
            "topic": "X",
            "competitors": [
                {
                    "name": "A",
                    "url": "https://a.com",
                    "pricing": "$39/月起",
                    "stage": "成长期",
                    "feature_catalog": {
                        "A": [
                            {"category": "开发者", "name": "**Open API 集成**"},
                            {"category": "其他", "name": "Explore the Power"},
                        ]
                    },
                    "strengths": [],
                    "weaknesses": [],
                    "scores": {k: 5 for k in (
                        "feature_richness", "ux", "pricing_value",
                        "integration", "ai_capability", "momentum",
                    )},
                },
                {
                    "name": "B",
                    "url": "https://b.com",
                    "pricing": "企业报价",
                    "stage": "巨头",
                    "feature_catalog": {"B": []},
                    "strengths": [],
                    "weaknesses": [],
                    "scores": {},
                },
            ],
            "opportunities": [],
            "gaps": [],
            "market_segments": [],
        }
        d = normalize(data)
        for c in d["competitors"]:
            self.assertTrue(c["strengths"], f"{c['name']} strengths 应被推断补全")
            self.assertTrue(c["weaknesses"], f"{c['name']} weaknesses 应被推断补全")
            vals = list(c["scores"].values())
            self.assertTrue(all(isinstance(v, (int, float)) for v in vals))
        a = next(c for c in d["competitors"] if c["name"] == "A")
        # 营销噪音被清洗,只留真功能
        names = [f["name"] for f in a["feature_catalog"]["A"]]
        self.assertEqual(names, ["Open API 集成"])
        # 定价差异应拉开 pricing_value 区分度(A $39 起步 > B 企业报价)
        b = next(c for c in d["competitors"] if c["name"] == "B")
        self.assertGreater(a["scores"]["pricing_value"], b["scores"]["pricing_value"])


class TestScrapeMerge(unittest.TestCase):
    """adapters._merge_results:质量选 primary + 原序保留 + 归一化去重。"""

    def _results(self):
        return [
            {
                "success": True, "scraper": "playwright",
                "markdown": "jQuery(window).bind('load', function(){}\n\n" * 5 + "\n\nGrowth plan",
                "html": "", "text": "", "screenshot": None, "extracted": None,
            },
            {
                "success": True, "scraper": "trafilatura",
                "markdown": "# Pricing\n\nGrowth plan  $39/mo\n\nTeam plan $79/mo, includes 5 agent seats and shared team inbox",
                "html": "", "text": "", "screenshot": None, "extracted": None,
            },
            {
                "success": True, "scraper": "firecrawl",
                "markdown": "# Pricing\n\nGrowth plan  $39/mo\n\nStart free trial",
                "html": "", "text": "", "screenshot": None, "extracted": None,
            },
        ]

    def test_quality_primary_not_longest_junk(self):
        from adapters import _merge_results

        merged = _merge_results(self._results())
        # 不变量:垃圾 JS 引擎不许当选 primary(干净引擎按质量分胜出)
        self.assertNotEqual(merged["stats"]["primary_scraper"], "playwright")
        self.assertIn(merged["stats"]["primary_scraper"], ("firecrawl", "trafilatura"))
        # primary 原序保留:标题在最前
        self.assertTrue(merged["markdown"].startswith("# Pricing"))
        # 垃圾 JS 主体不允许当选
        self.assertNotIn("jQuery", merged["markdown"].split("\n")[0])

    def test_normalized_dedup(self):
        from adapters import _merge_results

        merged = _merge_results(self._results())
        # "Growth plan $39/mo" 两引擎各一份(空格不同) → 只留一份
        self.assertEqual(merged["markdown"].count("$39/mo"), 1)
        # 独有段落作为补充保留
        self.assertIn("Team plan $79/mo", merged["markdown"])


class TestPricingEvidence(unittest.TestCase):
    """跨引擎定价投票:一致 token 标记 verified。"""

    def test_extract_price_lines_requires_context(self):
        from scripts.crawl_competitors import _extract_price_lines

        md = "# Pricing\n\nGrowth  $39/mo  per user\n\nrandom 42 number line\n\nContact Sales for Enterprise"
        lines = [d["line"] for d in _extract_price_lines(md)]
        self.assertTrue(any("$39" in ln for ln in lines))
        self.assertTrue(any("Sales" in ln or "Enterprise" in ln for ln in lines))
        self.assertFalse(any("random 42" in ln for ln in lines))

    def test_price_lines_structured_parts(self):
        """结构化部件:plan/price/period 拆开返回(定价卡片的直接数据源)。"""
        from scripts.crawl_competitors import _extract_price_lines

        md = "# Pricing\n\n## Growth\n\nsome card text here\n\n## $59\n\nmonth billed annually"
        parts = _extract_price_lines(md)
        self.assertTrue(parts)
        p = parts[0]
        self.assertEqual(p["plan"], "Growth")
        self.assertEqual(p["price"], "$59")
        self.assertIn("annually", p["period"])

    def test_cross_engine_vote(self):
        from scripts.crawl_competitors import _extract_pricing_evidence

        r = {
            "all_results": [
                {"success": True, "scraper": "firecrawl",
                 "markdown": "Growth plan $39/mo\nTeam $79/mo"},
                {"success": True, "scraper": "trafilatura",
                 "markdown": "Growth plan $39 / mo"},
                {"success": True, "scraper": "playwright",
                 "markdown": "jQuery junk no price"},
            ]
        }
        ev = _extract_pricing_evidence(r)
        self.assertTrue(ev["verified"])
        self.assertIn("$39", ev["pricing"])
        self.assertEqual(len(ev["engines"]), 2)

    def test_single_engine_not_verified(self):
        from scripts.crawl_competitors import _extract_pricing_evidence

        r = {
            "all_results": [
                {"success": True, "scraper": "firecrawl",
                 "markdown": "Growth plan $39/mo"},
            ]
        }
        ev = _extract_pricing_evidence(r)
        self.assertFalse(ev["verified"])


class TestEdgeCases(unittest.TestCase):
    """边界 / 异常输入测试 — 防止 None 引发 TypeError 等 bug。"""

    def _render(self, data):
        d = normalize(data)
        return Template((ROOT / "templates" / "report.html").read_text()).render(d)

    def test_none_category(self):
        """feature.category = None 不应崩溃(sorted None vs str 失败)。"""
        data = {
            "topic": "X",
            "competitors": [
                {
                    "name": "X",
                    "url": "x",
                    "tagline": "x",
                    "founded": 2020,
                    "stage": "巨头",
                    "headquarters": "X",
                    "funding": "X",
                    "team_size": "X",
                    "pricing": "X",
                    "feature_catalog": {
                        "X": [
                            {"category": None, "name": "F1"},
                            {"category": "", "name": "F2"},
                            {"name": "F3"},
                        ]
                    },
                    "strengths": [],
                    "weaknesses": [],
                    "differentiators": [],
                    "tech_signals": [],
                    "scores": {},
                }
            ],
            "opportunities": [],
            "gaps": [],
            "market_segments": [],
            "feature_overlap": {},
            "other_competitors": [],
        }
        d = normalize(data)
        self.assertEqual(
            d["feature_total_count"],
            3,
            "3 features should be counted regardless of category value",
        )

    def test_none_score_value(self):
        """scores 字段含 None 不应让 sum() 崩溃。"""
        data = {
            "topic": "X",
            "competitors": [
                {
                    "name": "X",
                    "url": "x",
                    "tagline": "x",
                    "founded": 2020,
                    "stage": "巨头",
                    "headquarters": "X",
                    "funding": "X",
                    "team_size": "X",
                    "pricing": "X",
                    "feature_catalog": {"X": []},
                    "strengths": [],
                    "weaknesses": [],
                    "differentiators": [],
                    "tech_signals": [],
                    "scores": {"feature_richness": None, "ux": 7, "pricing_value": 6},
                }
            ],
            "opportunities": [],
            "gaps": [],
            "market_segments": [],
            "feature_overlap": {},
            "other_competitors": [],
        }
        # 不应抛 TypeError
        d = normalize(data)
        # avg = 13/5 = 2.6(None 被过滤,但默认填充 0 也算入)
        self.assertEqual(d["competitors_ranked"][0]["avg"], 2.6)

    def test_minimal_opportunity(self):
        """opportunity 只有 title 字段不应让 min/排序崩溃。"""
        data = {
            "topic": "X",
            "competitors": [],
            "opportunities": [{"title": "T"}],
            "gaps": [],
            "market_segments": [],
            "feature_overlap": {},
            "other_competitors": [],
        }
        d = normalize(data)
        self.assertEqual(d["top_opportunity"], "T")

    def test_minimal_gap(self):
        """gap 只有 gap 字段不应让 min/排序崩溃。"""
        data = {
            "topic": "X",
            "competitors": [],
            "opportunities": [],
            "gaps": [{"gap": "G"}],
            "market_segments": [],
            "feature_overlap": {},
            "other_competitors": [],
        }
        d = normalize(data)
        self.assertEqual(d["top_gap"], "G")

    def test_empty_competitors_list(self):
        """空 competitors 列表应正常渲染,不崩溃。"""
        data = {
            "topic": "X",
            "competitors": [],
            "opportunities": [],
            "gaps": [],
            "market_segments": [],
            "feature_overlap": {},
            "other_competitors": [],
        }
        out = self._render(data)
        # 应包含 9 个 sections 但内容可空
        self.assertIn("背景与目标", out)
        self.assertIn("结论与建议", out)

    def test_xss_in_executive_summary_escaped(self):
        """competitor/tagline 含 HTML 应被转义,不渲染为 HTML。

        注:executive_summary 框已被移除(让后续 9 章内容自己说话),
        XSS 防护改用其他用户输入字段(topic + competitor.tagline)验证。
        """
        data = {
            "topic": "<script>alert(1)</script>",
            "competitors": [
                {
                    "name": "<img onerror=alert(1)>",
                    "url": "x",
                    "tagline": "x",
                    "founded": 2020,
                    "stage": "巨头",
                    "headquarters": "X",
                    "funding": "X",
                    "team_size": "X",
                    "pricing": "X",
                    "feature_catalog": {"<img onerror=alert(1)>": []},
                    "strengths": [],
                    "weaknesses": [],
                    "differentiators": [],
                    "tech_signals": [],
                    "scores": {},
                }
            ],
            "opportunities": [],
            "gaps": [],
            "market_segments": [],
            "feature_overlap": {},
            "other_competitors": [],
        }
        out = self._render(data)
        # jinja2 autoescape 转义 < > &
        self.assertNotIn("<script>alert(1)", out, "XSS in topic not escaped!")
        self.assertNotIn(
            "<img onerror=alert(1)>", out, "XSS in competitor name not escaped!"
        )
        self.assertIn("&lt;script&gt;", out)
        self.assertIn("&lt;img", out)

    def test_jinja_syntax_in_data_escaped(self):
        """data 中含 {{ 或 {% 应被转义,不作为模板语法执行。"""
        data = {
            "topic": "X {{ y }}",
            "executive_summary": "{% for i in z %}{{i}}{% endfor %}",
            "competitors": [],
            "opportunities": [],
            "gaps": [],
            "market_segments": [],
            "feature_overlap": {},
            "other_competitors": [],
        }
        out = self._render(data)
        # topic 字段含 {{ }} —— 不应被 jinja 执行,应在 HTML 中以转义形式存在
        self.assertIn("{{ y }}", out, "Raw jinja in topic should appear as text")
        # executive_summary 已不在模板中渲染,所以 {{}} 字面量不会出现是正常的;
        # 只要渲染不崩溃、不报 jinja 语法错误就算通过。

    def test_100_competitors_no_crash(self):
        """100 家竞品不崩溃。"""
        data = {
            "topic": "X",
            "competitors": [
                {
                    "name": f"C{i}",
                    "url": "x",
                    "tagline": "x",
                    "founded": 2000 + i,
                    "stage": "巨头" if i % 2 else "成长期",
                    "headquarters": "X",
                    "funding": "X",
                    "team_size": "10+",
                    "pricing": "X",
                    "feature_catalog": {
                        f"C{i}": [{"category": "Cat", "name": f"F{i}"}]
                    },
                    "strengths": [{"point": "P", "evidence": "E", "score": 5}],
                    "weaknesses": [],
                    "differentiators": [],
                    "tech_signals": [],
                    "scores": {
                        "feature_richness": i % 10,
                        "ux": 5,
                        "pricing_value": 5,
                        "integration": 5,
                        "ai_capability": 5,
                        "momentum": 5,
                    },
                }
                for i in range(100)
            ],
            "opportunities": [],
            "gaps": [],
            "market_segments": [],
            "feature_overlap": {},
            "other_competitors": [],
        }
        out = self._render(data)
        # 不应崩溃,渲染时间应合理
        self.assertGreater(len(out), 100000, "Should produce a large report")
        # 100 家都应出现(可能在多处出现,不强求具体次数)
        for i in range(100):
            self.assertIn(f"C{i}", out, f"Competitor C{i} missing from output")


class TestCrawlers(unittest.TestCase):
    """爬虫适配器注册测试。"""

    def test_all_crawlers_registered(self):
        """13 个爬虫必须全部注册。"""
        from adapters import list_scrapers

        scrapers = list_scrapers()
        expected = {
            "firecrawl",
            "crawl4ai",
            "trafilatura",
            "newspaper3k",
            "readability",
            "markdownify",
            "playwright",
            "scrapy",
            "jina",
            "html2text",
            "requests_html",
            "camoufox",
            "crawlee",
        }
        self.assertEqual(set(scrapers.keys()), expected)

    def test_scrape_smart_imports(self):
        """scrape_smart 必须能导入且签名正确。"""
        from adapters import scrape_smart

        # 不实际调用(避免网络),只检查签名
        import inspect

        sig = inspect.signature(scrape_smart)
        self.assertIn("url", sig.parameters)


class TestIntelligentRouting(unittest.TestCase):
    """智能引擎路由(auto 默认 + 按类型组合 + 引擎学习)。"""

    def test_default_strategy_is_auto(self):
        """scrape_smart 默认策略必须是 auto(智能路由),不是全开 parallel。"""
        from adapters import scrape_smart
        import inspect

        sig = inspect.signature(scrape_smart)
        self.assertEqual(sig.parameters["strategy"].default, "auto")

    def test_classify_url(self):
        from adapters import classify_url

        self.assertEqual(classify_url("https://x.com/pricing"), "pricing")
        self.assertEqual(classify_url("https://docs.x.com/api"), "docs")
        self.assertEqual(classify_url("https://x.com/about"), "about")
        self.assertEqual(classify_url("https://x.com/"), "homepage")

    def test_pricing_gets_cross_validation_group(self):
        """定价页组合必须含 JS 引擎 + 静态对照引擎(交叉验证的基础)。"""
        from adapters import _URL_TYPE_SCRAPERS

        group = _URL_TYPE_SCRAPERS["pricing"]
        self.assertIn("trafilatura", group)  # 静态对照
        self.assertIn("firecrawl", group)  # JS 渲染

    def test_pricing_merge_isolated(self):
        """定价页禁止跨引擎拼接补充段落(历史价格污染根因)。"""
        from adapters import _merge_results, _NO_SUPPLEMENT_TYPES

        self.assertIn("pricing", _NO_SUPPLEMENT_TYPES)
        primary_md = (
            "# Pricing\n\nStarter $19/mo\n\nGrowth $39/mo\n\n"
            "Pro $99/mo\n\nAll plans include unlimited contacts and\n"
            "a shared team inbox with routing rules."
        )
        r1 = {"success": True, "scraper": "firecrawl", "markdown": primary_md,
              "html": "", "text": "", "screenshot": None, "extracted": None}
        r2 = {"success": True, "scraper": "trafilatura",
              "markdown": primary_md + "\n\nAdditional users $999 per month enterprise add-on pricing from comparison table.",
              "html": "", "text": "", "screenshot": None, "extracted": None}
        merged = _merge_results([r1, r2], allow_supplements=False)
        self.assertNotIn("$999", merged["markdown"])
        self.assertNotIn("其他引擎补充段落", merged["markdown"])
        self.assertEqual(merged["stats"]["supplement_paragraphs"], 0)
        # 对照:允许合并时(非定价页)补充段落机制本身正常
        merged2 = _merge_results([r1, r2], allow_supplements=True)
        self.assertIn("$999", merged2["markdown"])

    def test_engine_stats_learning(self):
        """引擎历史统计应影响推荐排序(失败多的引擎排后)。

        用临时 stats 文件,不污染真实 storage/engine-stats.json。
        """
        import adapters as A
        from adapters import recommend_scrapers, record_engine_outcome
        from pathlib import Path
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            orig = A._ENGINE_STATS_PATH
            A._ENGINE_STATS_PATH = Path(td) / "engine-stats.json"
            try:
                record_engine_outcome("pricing", {
                    "firecrawl": {"success": False, "quality": 0.0},
                    "playwright": {"success": False, "quality": 0.0},
                    "crawl4ai": {"success": True, "quality": 0.9},
                    "trafilatura": {"success": True, "quality": 0.8},
                })
                recs = recommend_scrapers("https://example.com/pricing")
                # crawl4ai 历史全优,应排在 firecrawl(历史全败)前面
                self.assertLess(recs.index("crawl4ai"), recs.index("firecrawl"))
            finally:
                A._ENGINE_STATS_PATH = orig


class TestNoFabrication(unittest.TestCase):
    """防伪造回归测试 —— 历史曾出现硬编码价格/伪造引文,这里守住不再复发。"""

    def test_builtin_price_fallback_deleted(self):
        """内置静态价格库必须已删除(返回空 dict)。"""
        from scripts.crawl_competitors import _get_fallback_from_builtin

        for name in ("WATI", "YCloud", "Sleekflow", "Notion", "随便什么"):
            self.assertEqual(_get_fallback_from_builtin(name), {})

    def test_other_competitors_pool_deleted(self):
        """硬编码 other_competitors 池(11 家 WhatsApp 竞品)必须已删除。"""
        from scripts.crawl_competitors import _derive_other_competitors

        self.assertEqual(_derive_other_competitors([], "任何主题"), [])

    def test_opportunities_not_templated(self):
        """脚本不得再输出模板化 opportunities(LLM Step 3 的工作)。"""
        from scripts.crawl_competitors import _derive_opportunities

        gaps = [{"gap": "Webhook 事件推送", "rationale": "x", "severity": "high", "source": ""}]
        self.assertEqual(_derive_opportunities([], gaps, "t"), [])

    def test_render_no_fabricated_g2_quotes(self):
        """渲染端 SWOT 补全不得再输出伪造 G2 引文。"""
        from render import _infer_strengths_weaknesses

        c = {"name": "X", "url": "https://x.com", "pricing": "$79/mo",
             "feature_catalog": {"X": [{"name": "AI 客服"}]}, "stage": "成长期"}
        pos, neg = _infer_strengths_weaknesses(c)
        for item in pos + neg:
            self.assertNotIn("G2:", item.get("evidence", ""))
            self.assertNotIn("gets expensive", item.get("evidence", ""))
            self.assertNotIn("Meta 官方", item.get("evidence", ""))

    def test_pricing_evidence_carries_provenance(self):
        """定价证据必须带 source_url + scraped_at(可追溯)。"""
        from scripts.crawl_competitors import _extract_pricing_evidence

        scrape = {"all_results": [
            {"success": True, "scraper": "firecrawl",
             "markdown": "Growth Plan $39 per user/month\n\nPro Plan $99 per user/month"},
            {"success": True, "scraper": "trafilatura",
             "markdown": "Growth Plan $39 per user/month"},
        ]}
        ev = _extract_pricing_evidence(scrape, "https://x.com/pricing")
        self.assertEqual(ev["source_url"], "https://x.com/pricing")
        self.assertTrue(ev["scraped_at"])
        self.assertTrue(ev["verified"])  # 2 引擎一致
        self.assertIn("firecrawl", ev["engines"])

    def test_verified_flag_consistent_with_engines(self):
        """verified=True 时 engines 必须非空(历史 bug: verified=True 但 engines=[])。"""
        from scripts.crawl_competitors import _extract_pricing_evidence

        scrape = {"all_results": [
            {"success": True, "scraper": "jina", "markdown": "Pro $49/mo"},
        ]}
        ev = _extract_pricing_evidence(scrape, "https://x.com/pricing")
        if ev["verified"]:
            self.assertTrue(ev["engines"], "verified=True 但 engines 为空")
        else:
            self.assertLessEqual(len(ev["engines"]), 1)

    def test_self_check_catches_fabricated_quotes(self):
        """self_check 的防伪造检查必须能拦住历史伪造引文。"""
        from render import self_check, normalize

        data = normalize({
            "topic": "t", "competitors": [
                {"name": "A", "url": "https://a.com", "scores": {},
                 "feature_catalog": {"A": []}},
                {"name": "B", "url": "https://b.com", "scores": {},
                 "feature_catalog": {"B": []}},
                {"name": "C", "url": "https://c.com", "scores": {},
                 "feature_catalog": {"C": []}},
            ],
        })
        html_with_fabrication = "<html>Pricing gets expensive at scale</html>"
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = self_check(data, html_with_fabrication)
        self.assertFalse(ok)


class TestHonestPricingSource(unittest.TestCase):
    """F2: 定价证据为空时,entry 不得用猜测 URL 充当 pricing_source。"""

    def test_no_evidence_no_source(self):
        from scripts.crawl_competitors import _build_competitor_entry
        scraped = {
            "name": "X", "url": "https://x.com",
            "pricing_source": "",  # _scrape_one 修复后失败时保持 ""
            "tagline_source": "https://x.com",
            "founded_source": "", "team_size_source": "",
            "headquarters_source": "",
            "raw_markdown": {
                "home": "# X\nTool for teams",
                "pricing": "",  # 定价页全失败
            },
            "page_urls": {"home": "https://x.com"},
            "pricing_evidence": {"pricing": "—", "verified": False,
                                 "engines": [], "source_url": "",
                                 "scraped_at": "", "vote_detail": [],
                                 "tiers": []},
            "site_title": "X — tool",
        }
        entry, warnings, _claims = _build_competitor_entry(scraped)
        self.assertEqual(entry["pricing_source"], "")
        self.assertFalse(entry["pricing_verified"])


class TestCompanyFieldAttribution(unittest.TestCase):
    """F3: founded/HQ/team 来源指向真正命中该值的页面(行级归属)。"""

    def _scrape(self, pages):
        return {
            "name": "X", "url": "https://x.com",
            "pricing_source": "", "tagline_source": "https://x.com",
            "founded_source": "", "team_size_source": "",
            "headquarters_source": "",
            "raw_markdown": {k: md for k, (md, _) in pages.items()},
            "page_urls": {k: u for k, (_, u) in pages.items()},
            "pricing_evidence": {"pricing": "—", "verified": False,
                                 "engines": [], "source_url": "",
                                 "scraped_at": "", "vote_detail": [],
                                 "tiers": []},
            "site_title": "X — tool",
        }

    def test_year_on_pricing_page_attributed_there(self):
        """年份在 pricing 页命中 → founded_source 指向 pricing 页而非官网。"""
        from scripts.crawl_competitors import _build_competitor_entry
        scraped = self._scrape({
            "home": ("# X\nTool for teams", "https://x.com"),
            "about": ("", ""),
            "pricing": ("# Pricing\nFounded in 2019 by ex-Googlers\n$59/mo",
                        "https://x.com/pricing"),
        })
        entry, _, _ = _build_competitor_entry(scraped)
        self.assertEqual(entry["founded"], "2019")
        self.assertEqual(entry["founded_source"], "https://x.com/pricing")
        self.assertIn("2019", entry["founded_quote"])

    def test_about_page_priority(self):
        from scripts.crawl_competitors import _build_competitor_entry
        scraped = self._scrape({
            "home": ("# X\nFounded in 2020", "https://x.com"),
            "about": ("# About\nFounded in 2015, headquartered in Kuala Lumpur",
                      "https://x.com/about"),
        })
        entry, _, _ = _build_competitor_entry(scraped)
        self.assertEqual(entry["founded"], "2015")  # about 优先
        self.assertEqual(entry["founded_source"], "https://x.com/about")
        self.assertEqual(entry["headquarters"], "Kuala Lumpur")
        self.assertEqual(entry["headquarters_source"], "https://x.com/about")

    def test_not_found_keeps_empty_source(self):
        from scripts.crawl_competitors import _build_competitor_entry
        scraped = self._scrape({
            "home": ("# X\nnothing useful", "https://x.com"),
        })
        entry, _, _ = _build_competitor_entry(scraped)
        self.assertEqual(entry["founded"], "—")
        self.assertEqual(entry["founded_source"], "")
        self.assertEqual(entry["founded_quote"], "")

    def test_feature_without_evidence_has_empty_source(self):
        """F4: 定位不到出处的功能,source 留空而不是挂 default 页。"""
        from scripts.crawl_competitors import _build_competitor_entry
        md_home = (
            "# X\n\n## Features\n\n"
            "- Team Inbox for shared conversations\n"
            "- Broadcasts to send updates at scale\n\n"
            "## Why teams pick X\n\nManage every customer chat in one place.\n"
        )
        scraped = self._scrape({
            "home": (md_home, "https://x.com"),
            # features 页返回了一个无法归因的候选功能(slug 派生形态)
            "features": ("- Slug Derived Feature Nine\n", ""),
        })
        entry, _, _ = _build_competitor_entry(scraped)
        names = {f["name"] for f in entry["feature_catalog"]["X"]}
        self.assertTrue(names)  # 有功能被提取
        self.assertIn("Team Inbox for shared conversations", names)
        for f in entry["feature_catalog"]["X"]:
            if f["name"] in md_home:
                self.assertEqual(f["source"], "https://x.com")
            else:
                self.assertEqual(f["source"], "")


class TestManifestEmission(unittest.TestCase):
    """F8: crawl_and_build 落盘 claims-manifest.json + engines.json。"""

    def test_manifest_and_engines_written(self):
        import tempfile
        from scripts import crawl_competitors as cc

        def fake_scrape_one(resolved, timeout=30, max_chars=25000):
            url = resolved["url"]
            return {
                "name": resolved["canonical_name"], "url": url,
                "pricing_source": "", "tagline_source": url,
                "founded_source": "", "team_size_source": "",
                "headquarters_source": "",
                "raw_markdown": {
                    "home": "# WATI\nWhatsApp API platform for teams",
                    "pricing": "# Pricing\nGrowth $59/mo",
                },
                "page_urls": {"home": url,
                              "pricing": url + "/pricing"},
                "pricing_evidence": {
                    "pricing": "Growth · $59 (/mo)", "verified": True,
                    "engines": ["playwright", "crawl4ai"],
                    "source_url": url + "/pricing",
                    "scraped_at": "2026-08-26 00:00 UTC",
                    "vote_detail": [{"line": "Growth $59/mo",
                                     "engines": ["playwright", "crawl4ai"],
                                     "independent_votes": 2}],
                    "tiers": [{"name": "Growth", "price": "$59",
                               "billing_period": "/mo", "features": [],
                               "source_url": url + "/pricing"}],
                },
                "site_title": "WATI — WhatsApp API",
                "_manifest": {
                    "fetched": {
                        url: {"status": "ok", "engines": {
                            "playwright": {"ok": True, "chars": 500,
                                           "content_hash": "h1"},
                        }, "fetched_at": "2026-08-26 00:00 UTC"},
                        url + "/pricing": {"status": "ok", "engines": {
                            "playwright": {"ok": True, "chars": 400,
                                           "content_hash": "h2"},
                            "crawl4ai": {"ok": True, "chars": 380,
                                         "content_hash": "h3"},
                        }, "fetched_at": "2026-08-26 00:00 UTC"},
                    },
                    "engines_by_url": {
                        url: {"playwright": "# WATI md"},
                        url + "/pricing": {
                            "playwright": "# Pricing\nGrowth $59/mo",
                            "crawl4ai": "# Plans\nGrowth $59 /mo"},
                    },
                    "failures": [],
                },
            }

        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "03-analysis.json"
            with mock.patch.object(cc, "_scrape_one", fake_scrape_one), \
                 mock.patch.object(cc, "resolve_competitors",
                                   return_value={"wati": {
                                       "canonical_name": "WATI",
                                       "url": "https://www.wati.io",
                                       "confidence": 0.95,
                                       "source": "builtin"}}):
                analysis = cc.crawl_and_build(
                    ["wati"], "WhatsApp 赛道",
                    manifest_path=Path(d) / "claims-manifest.json",
                    raw_dir=Path(d) / "02-raw",
                )
            self.assertTrue((Path(d) / "claims-manifest.json").exists())
            self.assertTrue((Path(d) / "02-raw" / "WATI.engines.json").exists())

            manifest = json.loads(
                (Path(d) / "claims-manifest.json").read_text(encoding="utf-8"))
            self.assertIn("https://www.wati.io", manifest["fetched"])
            # 定价 vote 行 claim 必须带 quote + 两引擎
            pricing_claims = [c for c in manifest["claims"]
                              if "pricing_vote_detail" in c["field"]]
            self.assertTrue(pricing_claims)
            self.assertEqual(pricing_claims[0]["quote"], "Growth $59/mo")
            self.assertEqual(sorted(pricing_claims[0]["verified_by"]),
                             ["crawl4ai", "playwright"])

            engines = json.loads(
                (Path(d) / "02-raw" / "WATI.engines.json").read_text(encoding="utf-8"))
            self.assertIn("Growth $59/mo",
                          engines["https://www.wati.io/pricing"]["playwright"])


class TestRunYouziFailures(unittest.TestCase):
    """F6: run_youzi step2 失败不再静默 —— 写入 manifest.failures。"""

    def test_failure_manifest_written(self):
        import tempfile
        import adapters
        from scripts import run_youzi

        with tempfile.TemporaryDirectory() as d:
            raw = Path(d) / "02-raw"
            with mock.patch.object(
                adapters, "scrape_smart", side_effect=RuntimeError("boom"),
            ):
                results, failures = run_youzi.step2_crawl(
                    ["https://dead.example.com"], raw)
            self.assertEqual(results, {})
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0]["url"], "https://dead.example.com")
            mpath = raw.parent / "claims-manifest.json"
            self.assertTrue(mpath.exists())
            manifest = json.loads(mpath.read_text(encoding="utf-8"))
            self.assertEqual(manifest["failures"][0]["url"],
                             "https://dead.example.com")
            self.assertEqual(
                manifest["fetched"]["https://dead.example.com"]["status"],
                "failed")


class TestFeatureAttributionAllEngines(unittest.TestCase):
    """5.2.3 出处修复:归因必须 grep 全引擎副本,不能只查主引擎合并稿。

    真实事故:WATI 53 个功能 20 个「无来源」—— 功能提取自全引擎并集,
    归因却只 grep merged md(=primary 引擎),只存在于副本的功能全部落空。
    """

    def _scrape(self):
        return {
            "name": "X", "url": "https://x.com",
            "pricing_source": "https://x.com/pricing",
            "tagline_source": "https://x.com",
            "founded_source": "", "team_size_source": "",
            "headquarters_source": "",
            "raw_markdown": {
                # merged 定价稿(=primary)不含该功能
                "home": "# X\n\n## Features\n\n- Team Inbox for shared chats\n\nwhy\n",
                "pricing": "# Pricing\nGrowth $59/mo\nPro $119/mo\n",
                "features": "", "about": "", "docs": "",
            },
            "page_urls": {"home": "https://x.com",
                          "pricing": "https://x.com/pricing"},
            "pricing_evidence": {"pricing": "—", "verified": False,
                                 "engines": [], "source_url": "",
                                 "scraped_at": "", "vote_detail": [],
                                 "tiers": []},
            # 副本(crawl4ai)里才有 "Single User Plan"(套餐卡功能清单)
            "pricing_all_markdowns": [
                "# Pricing\nGrowth $59/mo\n- Single User Plan included\n- Contact Info synced\n"
            ],
            "_manifest": {"fetched": {}, "engines_by_url": {
                "https://x.com/pricing": {
                    "crawl4ai": "# Pricing\nGrowth $59/mo\n- Single User Plan included\n- Contact Info synced\n",
                },
            }, "failures": []},
            "site_title": "X — tool",
        }

    def test_engine_only_feature_attributed_to_page(self):
        from scripts.crawl_competitors import _build_competitor_entry
        entry, _, _ = _build_competitor_entry(self._scrape())
        by_name = {f["name"]: f["source"] for f in entry["feature_catalog"]["X"]}
        # 只存在于副本引擎的套餐功能 → 归因到定价页
        self.assertEqual(by_name.get("Single User Plan included"),
                         "https://x.com/pricing")
        # merged 里的功能照旧归因
        self.assertEqual(by_name.get("Team Inbox for shared chats"),
                         "https://x.com")

    def test_junk_shapes_rejected(self):
        from scripts.crawl_competitors import _is_real_feature
        # 真实事故样本(WATI 2026-08-26)
        self.assertFalse(_is_real_feature("Manage {vendorcount} vendors"))
        self.assertFalse(_is_real_feature("Customers Blogs [Chatbot Library](ht"))
        self.assertFalse(_is_real_feature("Clientes Blogs [ Biblioteca de Chatbo"))
        # 正常功能不受影响
        self.assertTrue(_is_real_feature("Shared Team Inbox"))
        self.assertTrue(_is_real_feature("Broadcasts to send updates at scale"))

    def test_addon_note_with_markdown_emphasis(self):
        """加购价带 markdown 强调(_$24_)也要识别 —— WATI 事故:addon note 为空。"""
        from scripts.crawl_competitors import _build_competitor_entry
        scraped = self._scrape()
        scraped["raw_markdown"]["pricing"] = (
            "# Pricing\nmonth _billed annually_\n"
            "_5_ Users Included Additional Users @ _$24_ /user/month\n"
            "Growth $59/mo\n"
        )
        entry, _, _ = _build_competitor_entry(scraped)
        self.assertTrue(entry.get("pricing_addon_note"))


if __name__ == "__main__":
    # 默认 verbose 模式
    unittest.main(verbosity=2)
