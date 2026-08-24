#!/usr/bin/env python3
"""pre-commit smoke test · /youzi 工程

用 examples/online-collab-demo.json 跑 render.py 全流程（保留自检），
确认 templates/report.html 在最近的改动后仍能正确渲染（0 未解析标签 + 7 section 齐全）。

退出码 = render.py 退出码（0 = 自检全通过；非 0 = 模板解析失败 / 输入不合法 / 自检有 ✗）。

维护者用法：
    python3 scripts/render_smoke.py            # 单跑
    pre-commit run render-smoke --all-files    # 通过 pre-commit
"""

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SMOKE_OUT = Path("/tmp/pre-commit-render-smoke.html")

cmd = [
    "python3",
    "render.py",
    "--input",
    "examples/online-collab-demo.json",
    "--output",
    str(SMOKE_OUT),
    # 注意：不传 --no-check，让自检真的跑起来
]

r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
sys.stdout.write(r.stdout)
sys.stderr.write(r.stderr)
if r.returncode != 0:
    sys.stderr.write(
        "\n[render-smoke] render.py 失败 —— 模板/输入/自检有回归，禁止合入。\n"
    )
    sys.exit(r.returncode)
