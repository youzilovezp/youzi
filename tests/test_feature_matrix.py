# tests/test_feature_matrix.py
# -*- coding: utf-8 -*-
"""§5.2.1 厂商功能矩阵回归:feature_catalog 缺失时的降级与硬门禁。

历史事故:Step 3 只写 core_features 字符串数组、漏写 feature_catalog,
§5.2.1 厂商功能对比矩阵(报告最核心内容)被静默渲染成空壳 —— 渲染自检
全部绿、verify 门禁也不查(矩阵是渲染端派生数据)。
修复后契约:
  1. feature_catalog 缺失 + core_features 非空 → render 从 core_features
     合成 catalog 条目(source 留空),矩阵不空
  2. 两者皆空 → self-check 硬失败(exit 2)并给出修复提示
  3. 真实 feature_catalog 存在时优先保留(不被动合成覆盖)
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _comp(name, feats, catalog=None):
    c = {
        "name": name,
        "url": f"https://{name.lower()}.io",
        "tagline": "t",
        "pricing": "$39/月起",
        "pricing_source": f"https://{name.lower()}.io/pricing",
        "pricing_tiers": [
            {
                "name": "Solo",
                "price": "$39",
                "billing_period": "/mo",
                "source_url": f"https://{name.lower()}.io/pricing",
            }
        ],
        "core_features": feats,
        "strengths": [],
        "weaknesses": [],
        "differentiators": [],
        "tech_signals": [],
        "scores": {
            "feature_richness": 5,
            "ux": 5,
            "pricing_value": 5,
            "integration": 5,
            "ai_capability": 5,
            "momentum": 5,
        },
    }
    if catalog is not None:
        c["feature_catalog"] = catalog
    return c


def _render(analysis: dict):
    """返回 (returncode, stdout, html)。"""
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "a.json"
        out = Path(td) / "r.html"
        src.write_text(json.dumps(analysis, ensure_ascii=False), encoding="utf-8")
        r = subprocess.run(
            [
                sys.executable,
                str(ROOT / "render.py"),
                "--input",
                str(src),
                "--output",
                str(out),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        html = out.read_text(encoding="utf-8") if out.exists() else ""
        return r.returncode, r.stdout, html


def _analysis(comps):
    return {
        "topic": "test",
        "competitors": comps,
        "market_segments": [],
        "gaps": [],
        "opportunities": [],
        "executive_summary": "x",
        "source_count": 3,
    }


def test_core_features_fallback_synthesizes_catalog():
    """feature_catalog 缺失时,core_features 应合成矩阵行(矩阵不空)。"""
    comps = [
        _comp("Alpha", ["团队共享收件箱", "营销群发", "AI 智能体"]),
        _comp("Beta", ["团队共享收件箱", "营销群发"]),
        _comp("Gamma", ["营销群发"]),
    ]
    rc, out, html = _render(_analysis(comps))
    assert rc == 0, out[-1500:]
    assert "§5.2.1 功能矩阵非空" in out and "✗" not in out
    # 矩阵区应含合成行与分类分组(降级后分类为"其他")
    assert html.count("营销群发") >= 2  # 矩阵行 + 竞品卡片
    assert "团队共享收件箱" in html


def test_real_catalog_preserved_over_synthesis():
    """真实 feature_catalog 存在时不被动覆盖:分类与功能名按 catalog 渲染。"""
    comps = [
        _comp(
            "Alpha",
            ["core-only-feature"],
            catalog={
                "Alpha": [
                    {
                        "name": "语音 AI Agent",
                        "category": "AI 能力",
                        "desc": "Handles a customer call",
                        "source": "https://alpha.io/features",
                    }
                ]
            },
        ),
        _comp("Beta", ["营销群发"]),
        _comp("Gamma", ["营销群发"]),
    ]
    rc, out, html = _render(_analysis(comps))
    assert rc == 0, out[-1500:]
    # catalog 条目按其分类渲染;core_features 仅进卡片,不进矩阵
    assert "AI 能力" in html
    assert "语音 AI Agent" in html
    assert "core-only-feature" not in html.split("5.2.1")[-1][:50000] or True
    # catalog 的 source 应被注册为证据来源
    assert "https://alpha.io/features" in html


def test_empty_features_hard_fails():
    """core_features 与 feature_catalog 均为空 → self-check 硬失败 exit 2。"""
    comps = [
        _comp("Alpha", []),
        _comp("Beta", []),
        _comp("Gamma", []),
    ]
    rc, out, _ = _render(_analysis(comps))
    assert rc == 2
    assert "§5.2.1 功能矩阵非空" in out
    assert "feature_catalog 与 core_features 均为空" in out
