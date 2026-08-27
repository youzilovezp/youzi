#!/usr/bin/env python3
"""tests/test_verify.py · verify.py 证据验证器测试。"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from verify import (  # noqa: E402
    Report,
    build_engine_index,
    load_manifest,
    norm_ws,
    verify_analysis,
)


def _write(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


MINI_MANIFEST = {
    "run": {"topic": "t", "started_at": "2026-08-26 00:00 UTC"},
    "fetched": {},
    "claims": [],
    "failures": [],
}

MINI_ANALYSIS = {
    "topic": "t",
    "executive_summary": "x",
    "competitors": [],
}


class TestSkeleton(unittest.TestCase):
    """骨架:加载 / 归一化 / 引擎索引 / 退出码。"""

    def test_norm_ws(self):
        self.assertEqual(norm_ws("  a\tb\n\nc  "), "a b c")
        self.assertEqual(norm_ws(None), "")

    def test_load_manifest_ok(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            p = _write(Path(d) / "m.json", MINI_MANIFEST)
            self.assertEqual(load_manifest(p)["run"]["topic"], "t")

    def test_load_manifest_corrupt_exit1(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.json"
            p.write_text("{broken", encoding="utf-8")
            with self.assertRaises(SystemExit) as cm:
                load_manifest(p)
            self.assertEqual(cm.exception.code, 1)

    def test_missing_input_exit1(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit) as cm:
                verify_analysis(
                    Path(d) / "nope.json",
                    Path(d) / "m.json",
                    Path(d),
                )
            self.assertEqual(cm.exception.code, 1)

    def test_empty_bundle_passes(self):
        """空证据包 + 空分析 = 三无报告,零 violation 通过。"""
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            m = _write(Path(d) / "m.json", MINI_MANIFEST)
            a = _write(Path(d) / "a.json", MINI_ANALYSIS)
            rep = verify_analysis(a, m, Path(d))
            self.assertTrue(rep["passed"])
            self.assertEqual(rep["exit_code"], 0)

    def test_report_collect(self):
        r = Report()
        r.hard("G1", "competitors[0].pricing", "https://x", "detail", "hint")
        r.warn("G6", "competitors[0].tagline", "detail2")
        self.assertFalse(r.ok)
        d = r.to_dict(2)
        self.assertEqual(d["summary"]["hard_failed"], 1)
        self.assertEqual(d["summary"]["warnings"], 1)
        self.assertEqual(d["violations"][0]["gate"], "G1")
        self.assertEqual(d["violations"][0]["hint"], "hint")

    def test_engine_index_merges_files(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            raw = Path(d) / "02-raw"
            _write(
                raw / "wati.engines.json",
                {
                    "https://wati.io/pricing": {
                        "playwright": "pw md",
                        "trafilatura": "tr md",
                    },
                },
            )
            _write(
                raw / "respond.engines.json",
                {
                    "https://respond.io/pricing": {"firecrawl": "fc md"},
                    "https://wati.io/pricing": {
                        "firecrawl": "fc2 md"
                    },  # 跨文件同 URL 合并
                },
            )
            idx = build_engine_index(raw)
            self.assertEqual(idx["https://wati.io/pricing"]["playwright"], "pw md")
            self.assertEqual(idx["https://wati.io/pricing"]["firecrawl"], "fc2 md")
            self.assertEqual(idx["https://respond.io/pricing"]["firecrawl"], "fc md")


def _bundle(tmp: Path, manifest: dict, analysis: dict, engines: dict = None):
    """构造最小证据包文件并跑验证。"""
    m = _write(tmp / "m.json", manifest)
    a = _write(tmp / "a.json", analysis)
    raw = tmp / "02-raw"
    if engines is not None:
        _write(raw / "x.engines.json", engines)
    return verify_analysis(a, m, raw)


def _ok_manifest(urls_ok=(), urls_failed=()):
    return {
        "run": {"topic": "t"},
        "fetched": {
            **{u: {"status": "ok", "engines": {}} for u in urls_ok},
            **{u: {"status": "failed", "engines": {}} for u in urls_failed},
        },
        "claims": [],
        "failures": [],
    }


def _one_comp_analysis(**fields):
    comp = {
        "name": "WATI",
        "url": "https://www.wati.io",
        "tagline": "x",
        "tagline_source": "",
        "founded": "—",
        "founded_source": "",
        "headquarters": "—",
        "headquarters_source": "",
        "team_size": "—",
        "team_size_source": "",
        "pricing": "—",
        "pricing_source": "",
        "pricing_verified": False,
        "pricing_tiers": [],
        "strengths": [],
        "weaknesses": [],
        "gtm_evidence": [],
        "moat_evidence": [],
        "tech_signals": [],
    }
    comp.update(fields)
    return {"topic": "t", "executive_summary": "x", "competitors": [comp]}


def _violations_by_gate(rep: dict, gate: str):
    return [v for v in rep["violations"] if v["gate"] == gate]


def _pricing_comp(
    verified=True,
    engines=("playwright", "jina"),
    hashes=None,
    scraped_at="2026-08-26 00:00 UTC",
    source="https://www.wati.io/pricing",
    tiers=1,
):
    return _one_comp_analysis(
        pricing="Growth · $59 (/mo)",
        pricing_verified=verified,
        pricing_source=source,
        pricing_scraped_at=scraped_at,
        pricing_engines=list(engines),
        pricing_tiers=[
            {
                "name": "Growth",
                "price": "$59",
                "billing_period": "/mo",
                "features": [],
                "source_url": source,
            }
        ]
        * tiers,
    )


def _manifest_with_hashes(url, engine_hashes: dict, status="ok"):
    return {
        "run": {},
        "claims": [],
        "failures": [],
        "fetched": {
            url: {
                "status": status,
                "engines": {
                    e: {"ok": True, "chars": 100, "content_hash": h}
                    for e, h in engine_hashes.items()
                },
            }
        },
    }


class TestG1SourceTraceability(unittest.TestCase):
    """G1: 分析引用的每个 source_url 必须在本轮成功抓取集合里。"""

    def test_url_not_fetched_is_hard_fail(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            rep = _bundle(
                Path(d),
                _ok_manifest(urls_ok=["https://www.wati.io"]),
                _one_comp_analysis(pricing_source="https://www.wati.io/pricing"),
            )
            v = _violations_by_gate(rep, "G1")
            self.assertEqual(len(v), 1)
            self.assertIn("pricing_source", v[0]["field"])

    def test_url_fetch_failed_is_hard_fail(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            rep = _bundle(
                Path(d),
                _ok_manifest(urls_failed=["https://www.wati.io/pricing"]),
                _one_comp_analysis(
                    pricing_tiers=[
                        {
                            "name": "Growth",
                            "price": "$59",
                            "billing_period": "/mo",
                            "features": [],
                            "source_url": "https://www.wati.io/pricing",
                        }
                    ]
                ),
            )
            self.assertEqual(len(_violations_by_gate(rep, "G1")), 1)

    def test_fetched_ok_passes(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            rep = _bundle(
                Path(d),
                _ok_manifest(urls_ok=["https://www.wati.io/pricing"]),
                _one_comp_analysis(pricing_source="https://www.wati.io/pricing"),
            )
            self.assertTrue(rep["passed"])


class TestG2QuoteGrep(unittest.TestCase):
    """G2: quote 必须在 source_url 的引擎原文中归一化命中。"""

    def test_quote_hit_passes(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            rep = _bundle(
                Path(d),
                _ok_manifest(urls_ok=["https://www.wati.io/about-us"]),
                _one_comp_analysis(
                    strengths=[
                        {
                            "point": "p",
                            "evidence": '官网原文: "Trusted by 8000+ teams"',
                            "score": 0,
                            "source": "https://www.wati.io/about-us",
                        }
                    ]
                ),
                engines={
                    "https://www.wati.io/about-us": {
                        "playwright": "Some nav\nTrusted by 8000+  teams\nfooter",
                    }
                },
            )
            self.assertTrue(rep["passed"])

    def test_quote_miss_is_hard_fail(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            rep = _bundle(
                Path(d),
                _ok_manifest(urls_ok=["https://www.wati.io"]),
                _one_comp_analysis(
                    gtm_evidence=[
                        {
                            "name": "n",
                            "quote": "not in page at all",
                            "source": "https://www.wati.io",
                        }
                    ]
                ),
                engines={"https://www.wati.io": {"playwright": "completely different"}},
            )
            v = _violations_by_gate(rep, "G2")
            self.assertEqual(len(v), 1)
            self.assertIn("gtm_evidence", v[0]["field"])

    def test_no_engines_recorded_is_fail_not_crash(self):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            rep = _bundle(
                Path(d),
                _ok_manifest(urls_ok=["https://www.wati.io"]),
                _one_comp_analysis(
                    moat_evidence=[
                        {
                            "name": "n",
                            "quote": "q",
                            "source": "https://www.wati.io",
                        }
                    ]
                ),
                engines={},
            )
            self.assertEqual(len(_violations_by_gate(rep, "G2")), 1)

    def test_cache_fallback_vote_line_skipped(self):
        """缓存回退的 vote 行是上一轮证据,本轮原文不可能包含 → 不 grep。

        真实事故:WATI 定价被反爬 starved 回退缓存,G2 拿缓存 vote 行
        grep 本轮引擎原文必失败 —— 时移证据的新鲜度由 G3 TTL 保证。
        """
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            rep = _bundle(
                Path(d),
                _ok_manifest(urls_ok=["https://www.wati.io/pricing"]),
                _one_comp_analysis(
                    pricing_source="https://www.wati.io/pricing",
                    pricing_from_cache=True,
                    pricing_vote_detail=[
                        {"line": "Growth $59/mo", "engines": ["playwright"]}
                    ],
                ),
                engines={
                    "https://www.wati.io/pricing": {"playwright": "no such line here"}
                },
            )
            self.assertEqual(len(_violations_by_gate(rep, "G2")), 0)


class TestG3PricingIntegrity(unittest.TestCase):
    """G3: verified ⇒ ≥2 内容独立引擎 + 时间戳新鲜 + tiers 非空。"""

    def _run(self, analysis, manifest, engines=None):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            return _bundle(Path(d), manifest, analysis, engines or {})

    def test_independent_engines_pass(self):
        rep = self._run(
            _pricing_comp(engines=("playwright", "jina")),
            _manifest_with_hashes(
                "https://www.wati.io/pricing",
                {"playwright": "aaaa", "jina": "bbbb"},
            ),
        )
        self.assertTrue(rep["passed"])

    def test_same_hash_two_engines_fail(self):
        """两引擎拿到同一反爬变体(内容哈希相同)≠ 交叉验证。"""
        rep = self._run(
            _pricing_comp(engines=("playwright", "jina")),
            _manifest_with_hashes(
                "https://www.wati.io/pricing",
                {"playwright": "same", "jina": "same"},
            ),
        )
        self.assertEqual(len(_violations_by_gate(rep, "G3")), 1)

    def test_single_engine_fail(self):
        rep = self._run(
            _pricing_comp(engines=("playwright",)),
            _manifest_with_hashes(
                "https://www.wati.io/pricing", {"playwright": "aaaa"}
            ),
        )
        self.assertEqual(len(_violations_by_gate(rep, "G3")), 1)

    def test_stale_timestamp_fail(self):
        rep = self._run(
            _pricing_comp(scraped_at="2026-01-01 00:00 UTC"),
            _manifest_with_hashes(
                "https://www.wati.io/pricing",
                {"playwright": "aaaa", "jina": "bbbb"},
            ),
        )
        self.assertEqual(len(_violations_by_gate(rep, "G3")), 1)

    def test_verified_with_empty_tiers_fail(self):
        rep = self._run(
            _pricing_comp(tiers=0),
            _manifest_with_hashes(
                "https://www.wati.io/pricing",
                {"playwright": "aaaa", "jina": "bbbb"},
            ),
        )
        self.assertEqual(len(_violations_by_gate(rep, "G3")), 1)

    def test_unverified_pricing_not_checked(self):
        """未验证定价(⚠ 徽章)不受 G3 约束 —— 诚实降级可交付。"""
        rep = self._run(
            # source="": 未验证定价不断言来源,否则会先触发 G1
            _pricing_comp(
                verified=False, engines=(), scraped_at="", tiers=0, source=""
            ),
            _ok_manifest(),
        )
        self.assertTrue(rep["passed"])


class TestG4MissingHonesty(unittest.TestCase):
    """G4: 失败有记录;缺失字段不断言来源。"""

    def _run(self, manifest, analysis, engines=None):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            return _bundle(Path(d), manifest, analysis, engines or {})

    def test_failed_fetch_without_failure_record(self):
        m = _ok_manifest(urls_failed=["https://www.wati.io/pricing"])
        rep = self._run(m, _one_comp_analysis())  # failures 列表为空 = 静默吞掉
        self.assertEqual(len(_violations_by_gate(rep, "G4")), 1)

    def test_failed_fetch_with_failure_record_passes(self):
        m = _ok_manifest(urls_failed=["https://www.wati.io/pricing"])
        m["failures"] = [
            {
                "competitor": "WATI",
                "url": "https://www.wati.io/pricing",
                "kind": "pricing",
                "error": "404",
            }
        ]
        rep = self._run(m, _one_comp_analysis())
        self.assertEqual(len(_violations_by_gate(rep, "G4")), 0)

    def test_missing_value_with_source_asserted(self):
        """founded="—" 却断言 founded_source → 读者点开找不到任何东西。"""
        rep = self._run(
            _ok_manifest(urls_ok=["https://www.wati.io/about"]),
            _one_comp_analysis(
                founded="—",
                founded_source="https://www.wati.io/about",
                headquarters="—",
                headquarters_source="",
                team_size="—",
                team_size_source="",
            ),
        )
        v = _violations_by_gate(rep, "G4")
        self.assertEqual(len(v), 1)
        self.assertIn("founded_source", v[0]["field"])

    def test_value_with_source_passes(self):
        rep = self._run(
            _ok_manifest(urls_ok=["https://www.wati.io/about"]),
            _one_comp_analysis(
                founded="2019",
                founded_source="https://www.wati.io/about",
            ),
        )
        self.assertEqual(len(_violations_by_gate(rep, "G4")), 0)


class TestG5Antifabrication(unittest.TestCase):
    """G5: 已知伪造引文黑名单 / repr 泄漏 / 占位符。"""

    def _run(self, analysis):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            return _bundle(Path(d), _ok_manifest(), analysis)

    def test_blacklisted_strength_quote(self):
        rep = self._run(
            _one_comp_analysis(
                strengths=[
                    {
                        "point": "p",
                        "evidence": '官网原文: "Pricing gets expensive at scale"',
                        "score": 0,
                        "source": "",
                    }
                ]
            )
        )
        self.assertEqual(len(_violations_by_gate(rep, "G5")), 1)

    def test_repr_leak_in_field(self):
        rep = self._run(_one_comp_analysis(tagline="['Best', 'tool']"))
        self.assertEqual(len(_violations_by_gate(rep, "G5")), 1)

    def test_placeholder_in_opportunities(self):
        a = _one_comp_analysis()
        a["opportunities"] = [{"title": "待补充", "inspiration": ""}]
        rep = self._run(a)
        self.assertEqual(len(_violations_by_gate(rep, "G5")), 1)

    def test_clean_analysis_passes(self):
        rep = self._run(_one_comp_analysis())
        self.assertTrue(rep["passed"])


class TestG6UrlHygiene(unittest.TestCase):
    """G6: URL 格式(硬) + 跨竞品域名(警告)。"""

    def _run(self, analysis, manifest=None, engines=None):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            return _bundle(Path(d), manifest or _ok_manifest(), analysis, engines)

    def test_malformed_url_hard_fail(self):
        rep = self._run(
            _one_comp_analysis(
                pricing_source="notaurl",
                pricing="x",
            )
        )
        self.assertEqual(len(_violations_by_gate(rep, "G6")), 1)

    def test_cross_competitor_domain_warning_only(self):
        rep = self._run(
            _one_comp_analysis(
                founded="2019",
                founded_source="https://competitor-x.com/about",
            ),
            manifest=_ok_manifest(urls_ok=["https://competitor-x.com/about"]),
        )
        self.assertEqual(len(_violations_by_gate(rep, "G6")), 0)  # 仅警告
        w = [x for x in rep["warnings"] if x["gate"] == "G6"]
        self.assertEqual(len(w), 1)

    def test_subdomain_of_own_site_no_warning(self):
        m = _ok_manifest(urls_ok=["https://docs.wati.io/api"])
        rep = self._run(
            _one_comp_analysis(
                tech_signals=[
                    {"name": "REST API", "source": "https://docs.wati.io/api"}
                ],
            ),
            manifest=m,
        )
        self.assertEqual(len([x for x in rep["warnings"] if x["gate"] == "G6"]), 0)


class TestG7SourceAuthority(unittest.TestCase):
    """G7: 功能/技术/差异化类证据不得锚定定价页/域名根(溯源权威性)。"""

    def _run(self, analysis, manifest=None):
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            return _bundle(Path(d), manifest or _ok_manifest(), analysis)

    def test_tech_signal_pricing_anchor_hard_fails(self):
        # 锚 pricing → hard fail;但 quote 本身是定价陈述(货币+数字)时豁免
        rep = self._run(
            _one_comp_analysis(
                tech_signals=[
                    {
                        "name": "API 用量分层",
                        "source": "https://www.wati.io/pricing",
                        "quote": "200k API calls",
                    },
                    {
                        "name": "Pro 档价格",
                        "source": "https://www.wati.io/pricing",
                        "quote": "Pro plan costs $99/mo",
                    },
                ]
            )
        )
        v = _violations_by_gate(rep, "G7")
        self.assertEqual(len(v), 1)
        self.assertIn("tech_signals[0]", v[0]["field"])
        self.assertIn("pricing", v[0]["source_url"])

    def test_tech_signal_docs_subpage_passes(self):
        # 锚 docs 具体子页 + 域名根(homepage)对照:子页过,域名根挂
        rep = self._run(
            _one_comp_analysis(
                tech_signals=[
                    {
                        "name": "REST API",
                        "source": "https://docs.wati.io/reference/introduction",
                        "quote": "OpenAPI",
                    },
                    {"name": "弱锚", "source": "https://docs.wati.io", "quote": ""},
                ]
            )
        )
        v = _violations_by_gate(rep, "G7")
        self.assertEqual(len(v), 1)
        self.assertIn("tech_signals[1]", v[0]["field"])
        self.assertIn("域名根", v[0]["detail"])

    def test_strength_feature_semantic_pricing_anchor_fails(self):
        # R2-C:strengths 功能语义(无价格语境词)锚 pricing → hard fail
        rep = self._run(
            _one_comp_analysis(
                strengths=[
                    {
                        "point": "Webhook & API 与角色权限",
                        "evidence": '官网原文: "Webhook & API calls"',
                        "score": 3,
                        "source": "https://www.wati.io/pricing",
                    },
                    {
                        "point": "多坐席协同收件箱",
                        "evidence": "—",
                        "source": "https://www.wati.io",
                    },
                ]
            )
        )
        v = _violations_by_gate(rep, "G7")
        self.assertEqual(len(v), 2)
        self.assertIn("strengths[0]", v[0]["field"])
        self.assertIn("定价页路径", v[0]["detail"])
        self.assertIn("strengths[1]", v[1]["field"])
        self.assertIn("域名根", v[1]["detail"])

    def test_strength_pricing_semantic_pricing_anchor_passes(self):
        # R2-C:定价陈述(无免费档/货币数字/定价语境)锚 pricing = 合理保留
        rep = self._run(
            _one_comp_analysis(
                strengths=[
                    {
                        "point": "定价全场最低($12/seat),只为分配 seat 者付费",
                        "evidence": '官网原文: "$12/seat"',
                        "score": 4,
                        "source": "https://www.wati.io/pricing",
                    },
                    {
                        "point": "无免费档,起步价贵",
                        "evidence": '官网原文: "Pro plan costs $99/mo"',
                        "score": 2,
                        "source": "https://www.wati.io/pricing",
                    },
                ],
                weaknesses=[
                    {
                        "point": "定价页默认年付视图,月付价需手动切换",
                        "evidence": '官网原文: "Monthly Annual Save 20%"',
                        "source": "https://www.wati.io/pricing",
                    }
                ],
            )
        )
        self.assertEqual(_violations_by_gate(rep, "G7"), [])


class TestNetworkGates(unittest.TestCase):
    """N1/N2: 全部 mock,不发真实网络请求。"""

    _P = "https://www.wati.io/pricing"

    def _run_network(self, fetch_ret):
        import tempfile
        from unittest import mock
        import network_gates

        with tempfile.TemporaryDirectory() as d:
            m = _ok_manifest(urls_ok=["https://www.wati.io", self._P])
            a = _write(
                Path(d) / "a.json",
                _one_comp_analysis(
                    pricing_source=self._P,
                    pricing="x",
                ),
            )
            mm = _write(Path(d) / "m.json", m)
            with mock.patch.object(network_gates, "fetch_url", return_value=fetch_ret):
                return verify_analysis(a, mm, Path(d), network=True)

    def test_n1_dead_url_hard_fail(self):
        rep = self._run_network(
            {"ok": False, "http_status": 404, "final_url": "", "error": "HTTP 404"}
        )
        v = [x for x in rep["violations"] if x["gate"] == "N1"]
        self.assertEqual(len(v), 1)

    def test_n1_live_url_passes(self):
        rep = self._run_network(
            {"ok": True, "http_status": 200, "final_url": self._P, "error": ""}
        )
        self.assertTrue(rep["passed"])

    def test_n1_cross_domain_redirect_warning(self):
        rep = self._run_network(
            {
                "ok": True,
                "http_status": 200,
                "final_url": "https://other-cdn.net/pricing",
                "error": "",
            }
        )
        self.assertEqual(len([w for w in rep["warnings"] if w["gate"] == "N1"]), 1)

    def test_sample_limits_urls(self):
        """--sample 只抽查前 N 个 URL(mock 计数)。"""
        from unittest import mock
        import network_gates

        calls = []

        def counting(url, **kw):
            calls.append(url)
            return {"ok": True, "http_status": 200, "final_url": url, "error": ""}

        comp = _one_comp_analysis(pricing_source="https://a.com/p")["competitors"][0]
        with mock.patch.object(network_gates, "fetch_url", side_effect=counting):
            r = network_gates.Report()
            network_gates.run_all({"competitors": [comp]}, {"fetched": {}}, r, sample=1)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
