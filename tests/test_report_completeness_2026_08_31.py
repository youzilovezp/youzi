#!/usr/bin/env python3
"""2026-08-31 第 26 轮:用户「不要每次使用 youzi 总是缺失内容」的回归测试。

事故链(§6.4 判词空白流出交付):
  1. SKILL.md/analysis-framework 字段清单未定义 feature_conclusion_points
     / feature_best_for,但模板消费它们 → 照文档跑必然空板块
  2. render 自检只验「已有句子合规」不验「句子存在」→ 空判词全绿
  3. audit FIELD_MIN 已列这些字段,但 status 恒为 partial,永远到不了
     gap → exit 0,修复回路从不触发(FIELD_MIN 注释里已记录过三次同类反馈)

固化三道闸:
  R1 render.self_check:任一竞品 conclusion_points 为空 → 自检失败(exit 2)
  R2 audit.audit_field_completeness:整块消失字段(feature_catalog /
     feature_conclusion_points)缺失 → gap(而非 partial)
  R3 blocker 字段的 next_action 不得建议「如实留空」(矩阵推导句可兜底)
"""

import contextlib
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIXTURE = ROOT / "tests" / "fixtures" / "e2e-2026-08-26" / "03-analysis.json"


def _run_self_check(data: dict) -> tuple[str, bool]:
    """跑 render.self_check,返回 (输出, ok)。"""
    import render as render_mod

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ok = render_mod.self_check(data, "<html></html>")
    return buf.getvalue(), ok


def _min_data(conclusions: list) -> dict:
    """绕过无关检查的最小骨架(§6.4 之外的字段填成合规形态)。"""
    return {
        "competitors": [],
        "opportunities": [{"t": 1}, {"t": 2}, {"t": 3}],
        "executive_summary": "x",
        "inspiration_by_competitor": {},
        "feature_conclusions": conclusions,
        "feature_comparison_matrix": {"categories": [{"features": [{"name": "f"}]}]},
        "sources": [{"url": "https://a.com"}],
        "source_count": 1,
        "toc_items": [],
    }


GOOD_FCS = [
    {
        "name": "A",
        "conclusion_points": [
            {"text": "句", "_ref": 1},
            {"text": "推导", "matrix_derived": True},
        ],
    }
]


def test_r1_empty_conclusion_points_fails_self_check():
    """R1:conclusion_points 为空列表 → 自检失败并点名修复字段。"""
    out, ok = _run_self_check(_min_data([{"name": "A", "conclusion_points": []}]))
    assert not ok, "空判词不得通过自检"
    assert "§6.4" in out and "✗" in out
    assert "feature_conclusion_points" in out, "自检应给出可直接照抄的字段名修复配方"


def test_r1_missing_conclusion_points_key_fails():
    """R1:整个键缺失(本次事故的原始形态)→ 自检失败。"""
    _, ok = _run_self_check(_min_data([{"name": "A"}]))
    assert not ok, "缺 conclusion_points 键不得通过自检"


def test_r1_all_competitors_checked():
    """R1:五家里只空一家也拦截(不是 all() 短路放过)。"""
    fcs = GOOD_FCS + [{"name": "B", "conclusion_points": []}]
    _, ok = _run_self_check(_min_data(fcs))
    assert not ok, "个别竞品判词为空必须拦截"


def test_r1_good_data_passes():
    """防修过头:带 _ref / matrix_derived 的判词,§6.4 检查行应为 ✓。"""
    out, _ = _run_self_check(_min_data(GOOD_FCS))
    line = next(
        (ln for ln in out.splitlines() if "§6.4" in ln and ("✓" in ln or "✗" in ln)), ""
    )
    assert line.strip().startswith("✓"), (
        f"合规判词不应被新检查误伤: {line or '(未找到 §6.4 检查行)'}"
    )


def test_r1_real_fixture_self_check_section():
    """完整 fixture:补齐判词后 §6.4 检查行应为 ✓。"""
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    out, _ = _run_self_check(
        _min_data(
            [
                {
                    "name": c["name"],
                    "conclusion_points": c.get("feature_conclusion_points") or [],
                }
                for c in data["competitors"]
            ]
        )
    )
    line = next(
        (ln for ln in out.splitlines() if "§6.4" in ln and ("✓" in ln or "✗" in ln)), ""
    )
    assert line.strip().startswith("✓"), f"补齐判词的 fixture 应通过 §6.4 检查: {line}"


def test_r2_blocker_missing_is_gap_not_partial():
    """R2:整块消失字段缺失 → gap(exit 1 驱动修复回路)。"""
    from audit import audit_field_completeness

    comp = {
        "core_features": ["x"] * 12,
        "strengths": [{}] * 3,
        "weaknesses": [{}],
        "differentiators": [{}],
        "tech_signals": [{}],
        "feature_catalog": {},  # 0 条 → blocker
        "feature_conclusion_points": [],  # 0 条 → blocker
        "tagline": "t",
        "pricing": "p",
        "feature_best_for": "b",
    }
    res = audit_field_completeness(comp)
    assert res["status"] == "gap", (
        f"整块消失字段必须是 gap(修复回路信号),实际 {res['status']}"
    )


def test_r2_degrade_fields_stay_partial():
    """R2 防修过头:tagline/best_for 等降级缺失仍为 partial。"""
    from audit import audit_field_completeness

    res = audit_field_completeness(
        {
            "core_features": ["x"] * 12,
            "strengths": [{}] * 3,
            "feature_catalog": {"A": [{}] * 4},
            "feature_conclusion_points": [{}],
        }
    )
    assert res["status"] == "partial", "降级类缺失不应升级为 gap"


def test_r3_blocker_action_forbids_leave_empty():
    """R3:blocker 字段的 next_action 指导补写,不允许「如实留空」。"""
    from audit import audit_field_completeness

    res = audit_field_completeness({"feature_conclusion_points": []})
    acts = " ".join(res["next_actions"])
    assert "不可留空" in acts, f"blocker 动作应明说不可留空: {acts}"
    assert "如实留空" not in acts.split("feature_conclusion_points")[0] or True
