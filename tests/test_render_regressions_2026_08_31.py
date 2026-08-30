#!/usr/bin/env python3
"""2026-08-31 用户巡检反馈的渲染回归测试(第 24 轮)。

三处质量事故的固化:
  R1 tech_signals 写成 {signal,quote} 等别名键(name 空)→ §4.2 出现
     「无信号名 · N 家 · 乱计数」伪聚类卡 + §4.3 只显示裸引文
  R2 market_segments 写成 {name, competitors}(fixture/框架示例形态)
     → §5.0 渲染成「? · — | 0 家玩家」
  R3 §5.2 发布密度卡的 source 懒惰取官网根 URL → 点击找不到任何动态
     (不可溯源)
"""

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "tests" / "fixtures" / "e2e-2026-08-26" / "03-analysis.json"


def _render(data: dict, tmp: Path) -> str:
    src = tmp / "analysis.json"
    src.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    out = tmp / "report.html"
    r = subprocess.run(
        [
            sys.executable,
            str(ROOT / "render.py"),
            "--input",
            str(src),
            "--output",
            str(out),
            "--no-check",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stderr[-500:]
    return out.read_text(encoding="utf-8")


def test_market_segments_name_competitors_form_renders_players(tmp_path):
    """{name, competitors} 形态必须渲染出玩家,而不是「0 家玩家」。"""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    names = [c["name"] for c in data["competitors"]]
    data["market_segments"] = [
        {"name": "测试赛道A", "competitors": names[:1]},
        {"name": "测试赛道B", "competitors": names[1:2]},
    ]
    h = _render(data, tmp_path)
    assert "测试赛道A" in h and "1 家玩家" in h, (
        "{name, competitors} 形态的 players 必须被归一化消费"
    )


def test_tech_signal_without_name_no_garbage_cluster(tmp_path):
    """name 缺失的 tech_signal 不得进 §4.2 聚类(空名伪簇)。"""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for c in data["competitors"]:
        c["tech_signals"] = [
            {"signal": "错误键名形态", "source": c.get("url", ""), "quote": "x"}
            for _ in range(2)
        ]
    h = _render(data, tmp_path)
    # 聚类区不应出现空信号名的卡片(⚙ 后直接跟数字/「家」)
    import re

    garbage = re.findall(r"⚙\s*<[^>]*>\s*\d+ 家", h)
    assert not garbage, f"空名信号仍产出伪聚类卡: {garbage[:3]}"


def test_tech_signal_with_name_appears_in_cluster(tmp_path):
    """正向:name 键形态的信号名必须出现在 §4.2(防修过头全跳过)。"""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    for c in data["competitors"][:1]:
        c["tech_signals"] = [{"name": "REST 回归测试信号", "source": c.get("url", "")}]
    h = _render(data, tmp_path)
    assert "REST 回归测试信号" in h


def test_growth_density_card_links_to_momentum_source(tmp_path):
    """§5.2 密度卡 source 应指向动态条目的实际来源,而非官网根。"""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    blog_url = "https://example.com/blog/changelog"
    for c in data["competitors"]:
        c["product_momentum"] = [
            {"title": "回归测试动态", "when": "2026-01-01", "source": blog_url}
        ]
    h = _render(data, tmp_path)
    i = h.find("5.2 发布密度对比")
    seg = h[i : i + 3000] if i >= 0 else ""
    assert blog_url in seg, "密度卡应链接到 momentum 条目的 source"
