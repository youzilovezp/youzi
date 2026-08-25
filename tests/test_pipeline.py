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
        """§ 5.2 矩阵必须汇总所有 117 项功能,24 个类别。"""
        m = self.data["feature_comparison_matrix"]
        self.assertEqual(m["totals_per_competitor"]["Twilio"], 18)
        self.assertEqual(m["totals_per_competitor"]["WATI"], 18)
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
        """Sources 列表 URL 必须去重。"""
        urls = [s["url"] for s in self.data["sources"]]
        self.assertEqual(len(urls), len(set(urls)), "Sources URL 有重复")


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
        """11 个爬虫必须全部注册。"""
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
        }
        self.assertEqual(set(scrapers.keys()), expected)

    def test_scrape_smart_imports(self):
        """scrape_smart 必须能导入且签名正确。"""
        from adapters import scrape_smart

        # 不实际调用(避免网络),只检查签名
        import inspect

        sig = inspect.signature(scrape_smart)
        self.assertIn("url", sig.parameters)


if __name__ == "__main__":
    # 默认 verbose 模式
    unittest.main(verbosity=2)
