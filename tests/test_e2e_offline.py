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


@unittest.skipUnless((FIXTURE / "claims-manifest.json").exists(),
                     "fixture 未冻结(先跑 -m network 验收)")
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
        self.assertTrue(
            rep["passed"],
            f"冻结数据回放失败:\n{rep['violations']}")
        self.assertGreaterEqual(rep["summary"]["claims_checked"], 5,
                                "真实运行至少检查 5 条 claim")
