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


if __name__ == "__main__":
    unittest.main()
