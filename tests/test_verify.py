#!/usr/bin/env python3
"""tests/test_verify.py · verify.py 证据验证器测试。"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from verify import (  # noqa: E402
    Report, build_engine_index, load_analysis, load_manifest, norm_ws,
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
                    Path(d) / "nope.json", Path(d) / "m.json", Path(d), 
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
            _write(raw / "wati.engines.json", {
                "https://wati.io/pricing": {"playwright": "pw md", "trafilatura": "tr md"},
            })
            _write(raw / "respond.engines.json", {
                "https://respond.io/pricing": {"firecrawl": "fc md"},
                "https://wati.io/pricing": {"firecrawl": "fc2 md"},  # 跨文件同 URL 合并
            })
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
        "name": "WATI", "url": "https://www.wati.io",
        "tagline": "x", "tagline_source": "",
        "founded": "—", "founded_source": "",
        "headquarters": "—", "headquarters_source": "",
        "team_size": "—", "team_size_source": "",
        "pricing": "—", "pricing_source": "", "pricing_verified": False,
        "pricing_tiers": [], "strengths": [], "weaknesses": [],
        "gtm_evidence": [], "moat_evidence": [], "tech_signals": [],
    }
    comp.update(fields)
    return {"topic": "t", "executive_summary": "x", "competitors": [comp]}


def _violations_by_gate(rep: dict, gate: str):
    return [v for v in rep["violations"] if v["gate"] == gate]


def _pricing_comp(verified=True, engines=("playwright", "crawl4ai"),
                  hashes=None, scraped_at="2026-08-26 00:00 UTC",
                  source="https://www.wati.io/pricing", tiers=1):
    return _one_comp_analysis(
        pricing="Growth · $59 (/mo)", pricing_verified=verified,
        pricing_source=source, pricing_scraped_at=scraped_at,
        pricing_engines=list(engines),
        pricing_tiers=[{
            "name": "Growth", "price": "$59", "billing_period": "/mo",
            "features": [], "source_url": source,
        }] * tiers,
    )


def _manifest_with_hashes(url, engine_hashes: dict, status="ok"):
    return {
        "run": {}, "claims": [], "failures": [],
        "fetched": {url: {
            "status": status,
            "engines": {
                e: {"ok": True, "chars": 100, "content_hash": h}
                for e, h in engine_hashes.items()
            },
        }},
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
                    pricing_tiers=[{
                        "name": "Growth", "price": "$59",
                        "billing_period": "/mo", "features": [],
                        "source_url": "https://www.wati.io/pricing",
                    }]
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
                _ok_manifest(urls_ok=["https://www.wati.io"]),
                _one_comp_analysis(strengths=[{
                    "point": "p", "evidence": '官网原文: "Trusted by 8000+ teams"',
                    "score": 0, "source": "https://www.wati.io",
                }]),
                engines={"https://www.wati.io": {
                    "playwright": "Some nav\nTrusted by 8000+  teams\nfooter",
                }},
            )
            self.assertTrue(rep["passed"])

    def test_quote_miss_is_hard_fail(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rep = _bundle(
                Path(d),
                _ok_manifest(urls_ok=["https://www.wati.io"]),
                _one_comp_analysis(gtm_evidence=[{
                    "name": "n", "quote": "not in page at all", "source": "https://www.wati.io",
                }]),
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
                _one_comp_analysis(moat_evidence=[{
                    "name": "n", "quote": "q", "source": "https://www.wati.io",
                }]),
                engines={},
            )
            self.assertEqual(len(_violations_by_gate(rep, "G2")), 1)


class TestG3PricingIntegrity(unittest.TestCase):
    """G3: verified ⇒ ≥2 内容独立引擎 + 时间戳新鲜 + tiers 非空。"""

    def _run(self, analysis, manifest, engines=None):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            return _bundle(Path(d), manifest, analysis, engines or {})

    def test_independent_engines_pass(self):
        rep = self._run(
            _pricing_comp(engines=("playwright", "crawl4ai")),
            _manifest_with_hashes("https://www.wati.io/pricing", {
                "playwright": "aaaa", "crawl4ai": "bbbb"}),
        )
        self.assertTrue(rep["passed"])

    def test_same_hash_two_engines_fail(self):
        """两引擎拿到同一反爬变体(内容哈希相同)≠ 交叉验证。"""
        rep = self._run(
            _pricing_comp(engines=("playwright", "crawl4ai")),
            _manifest_with_hashes("https://www.wati.io/pricing", {
                "playwright": "same", "crawl4ai": "same"}),
        )
        self.assertEqual(len(_violations_by_gate(rep, "G3")), 1)

    def test_single_engine_fail(self):
        rep = self._run(
            _pricing_comp(engines=("playwright",)),
            _manifest_with_hashes("https://www.wati.io/pricing", {
                "playwright": "aaaa"}),
        )
        self.assertEqual(len(_violations_by_gate(rep, "G3")), 1)

    def test_stale_timestamp_fail(self):
        rep = self._run(
            _pricing_comp(scraped_at="2026-01-01 00:00 UTC"),
            _manifest_with_hashes("https://www.wati.io/pricing", {
                "playwright": "aaaa", "crawl4ai": "bbbb"}),
        )
        self.assertEqual(len(_violations_by_gate(rep, "G3")), 1)

    def test_verified_with_empty_tiers_fail(self):
        rep = self._run(
            _pricing_comp(tiers=0),
            _manifest_with_hashes("https://www.wati.io/pricing", {
                "playwright": "aaaa", "crawl4ai": "bbbb"}),
        )
        self.assertEqual(len(_violations_by_gate(rep, "G3")), 1)

    def test_unverified_pricing_not_checked(self):
        """未验证定价(⚠ 徽章)不受 G3 约束 —— 诚实降级可交付。"""
        rep = self._run(
            # source="": 未验证定价不断言来源,否则会先触发 G1
            _pricing_comp(verified=False, engines=(), scraped_at="",
                          tiers=0, source=""),
            _ok_manifest(),
        )
        self.assertTrue(rep["passed"])


if __name__ == "__main__":
    unittest.main()
