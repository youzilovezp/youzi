#!/usr/bin/env python3
"""tests/test_e2e_offline.py · 冻结的真实运行回归。

用 e2e 验收当天的真实产物(analysis + manifest + engines.json)离线重放
verify 全部门禁 —— 新 bad shape 修复后不得让已验收数据退化。
"""

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import gates  # noqa: E402
from verify import verify_analysis  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "e2e-2026-08-26"
# 2026-08-27 闭环改造(ladder/深链/定价归一)后的单竞品 WATI 真实运行
FIXTURE_LOOP = ROOT / "tests" / "fixtures" / "e2e-loop-2026-08-27"


@unittest.skipUnless(
    (FIXTURE / "claims-manifest.json").exists(), "fixture 未冻结(先跑 -m network 验收)"
)
class TestFrozenE2E(unittest.TestCase):
    def test_frozen_real_run_passes_all_gates(self):
        # G3 的 TTL 新鲜度是时间相对的 —— 冻结数据 14 天后必然"过期"。
        # 回放时钉住时钟:TTL 判定逻辑由 test_verify 的固定日期用例覆盖。
        with mock.patch.object(gates, "_ts_age_days", return_value=0.0):
            rep = verify_analysis(
                FIXTURE / "03-analysis.json",
                FIXTURE / "claims-manifest.json",
                FIXTURE / "02-raw",
            )
        self.assertTrue(rep["passed"], f"冻结数据回放失败:\n{rep['violations']}")
        self.assertGreaterEqual(
            rep["summary"]["claims_checked"], 5, "真实运行至少检查 5 条 claim"
        )


@unittest.skipUnless(
    (FIXTURE_LOOP / "claims-manifest.json").exists(),
    "loop fixture 未冻结(先跑闭环验收)",
)
class TestFrozenLoopE2E(unittest.TestCase):
    """闭环改造(§1-§4)后的真实运行回放:门禁 + 新能力断言。"""

    def test_frozen_loop_run_passes_gates(self):
        with mock.patch.object(gates, "_ts_age_days", return_value=0.0):
            rep = verify_analysis(
                FIXTURE_LOOP / "03-analysis.json",
                FIXTURE_LOOP / "claims-manifest.json",
                FIXTURE_LOOP / "02-raw",
            )
        self.assertTrue(rep["passed"], f"闭环数据回放失败:\n{rep['violations']}")

    def test_deep_link_tech_signal_anchored_to_subpage(self):
        # §3:tech_signals 必须锚定 docs 具体子页(非栏目首页),带 quote
        import json

        d = json.loads((FIXTURE_LOOP / "03-analysis.json").read_text())
        sigs = d["competitors"][0].get("tech_signals") or []
        deep = [
            s
            for s in sigs
            if s.get("quote") and "/reference/" in (s.get("source") or "")
        ]
        self.assertTrue(deep, f"应有深链锚定的技术信号: {sigs}")

    def test_pricing_cache_fallback_verified(self):
        # §2:反爬变体轮次 → 已验证缓存回退,verified 保持 True
        import json

        d = json.loads((FIXTURE_LOOP / "03-analysis.json").read_text())
        c = d["competitors"][0]
        self.assertTrue(c.get("pricing_verified"), "缓存回退后应保持 verified")
        self.assertTrue(c.get("pricing_from_cache"), "应标记 from_cache")
