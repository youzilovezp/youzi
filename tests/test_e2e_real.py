#!/usr/bin/env python3
"""tests/test_e2e_real.py · 真实网络 e2e 验收(发布前手动跑)。

跑完整管线:爬取(WATI/respond.io/YCloud,内置表最熟的三家)→ 证据包
→ 渲染 → verify 离线门禁 + 网络门禁。全部硬门禁绿灯 = 生产级验收通过。

运行:
    python3 -m pytest tests/test_e2e_real.py -m network -v

产物固定落在 /tmp/youzi-e2e-acceptance/(便于冻结为离线 fixture)。
"""

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

E2E_DIR = Path("/tmp/youzi-e2e-acceptance")


@pytest.mark.network
class TestE2EReal(unittest.TestCase):
    """真实数据验收:全管线 + verify --network。耗时 5-15 分钟。"""

    def test_full_pipeline_real_competitors(self):
        from verify import verify_analysis

        if E2E_DIR.exists():
            shutil.rmtree(E2E_DIR)
        E2E_DIR.mkdir(parents=True)
        out = E2E_DIR / "03-analysis.json"

        # Step 2+3: 爬取 + 证据包
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "crawl_competitors.py"),
             "--competitors", "wati,respond.io,ycloud",
             "--topic", "WhatsApp BSP 赛道(生产验收)",
             "--output", str(out)],
            cwd=ROOT, capture_output=True, text=True, timeout=1800,
        )
        self.assertEqual(
            r.returncode, 0, f"crawl failed:\n{r.stdout}\n{r.stderr}")

        analysis = json.loads(out.read_text(encoding="utf-8"))
        self.assertTrue(
            (out.parent / "claims-manifest.json").exists(),
            "证据包未落盘")
        self.assertTrue(
            (out.parent / "02-raw").exists(), "02-raw 未落盘")
        self.assertGreaterEqual(len(analysis["competitors"]), 2,
                                "真实爬取至少 2 家成功")

        # Step 4: 渲染
        html = out.parent / "report.html"
        r2 = subprocess.run(
            [sys.executable, str(ROOT / "render.py"),
             "--input", str(out), "--output", str(html)],
            cwd=ROOT, capture_output=True, text=True, timeout=300,
        )
        self.assertIn(r2.returncode, (0, 2), r2.stdout + r2.stderr)

        # Step 5: verify 离线门禁(必须全绿)
        rep = verify_analysis(
            out, out.parent / "claims-manifest.json", out.parent / "02-raw",
            report_path=out.parent / "verify-report.json",
        )
        self.assertTrue(
            rep["passed"],
            "离线硬门禁失败:\n" + json.dumps(
                rep["violations"], ensure_ascii=False, indent=1))

        # 网络门禁:N1 可达性(真实回访被引用 URL)
        rep_net = verify_analysis(
            out, out.parent / "claims-manifest.json", out.parent / "02-raw",
            network=True, sample=10,
        )
        self.assertFalse(
            [v for v in rep_net["violations"] if v["gate"] == "N1"],
            "N1 死链:\n" + json.dumps(
                [v for v in rep_net["violations"] if v["gate"] == "N1"],
                ensure_ascii=False, indent=1))
