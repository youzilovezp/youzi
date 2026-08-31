# tests/test_xss.py
# -*- coding: utf-8 -*-
"""P0 安全回归:不可信竞品数据的存储型 XSS + 证据链伪造防护。

固化三个实测复现的 payload:
1. url = "javascript:alert(document.cookie)" → 输出中不得出现 href="javascript:
2. 竞品名 = X" onmouseover="alert(7) → 不得形成可执行属性(必须转义为 &quot;/&#34;)
3. pricing_source 有值时如实显示该 URL;无值时绝不用 "官网/pricing" 拼接冒充
"""

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _render(analysis: dict) -> str:
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
        assert r.returncode == 0, r.stderr[-2000:]
        return out.read_text(encoding="utf-8")


def _comp(name, url, **extra):
    c = {
        "name": name,
        "url": url,
        "tagline": "t",
        "pricing": "$9/月起",
        "core_features": ["f1"],
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
    c.update(extra)
    return c


def _analysis(comps):
    """满足 self_check 门禁的最小骨架(竞品 ≥ 3 + 至少一条来源 + §6.4 判词非空)。"""
    pads = [
        _comp("Pad A", "https://pada.io", pricing_source="https://pada.io/pricing"),
        _comp("Pad B", "https://padb.io", pricing_source="https://padb.io/pricing"),
    ]
    # 2026-08-31 第 26 轮:§6.4 判词非空门禁 —— 统一注入矩阵推导兜底句
    for c in list(comps) + pads:
        c.setdefault(
            "feature_conclusion_points", [{"text": "兜底。", "matrix_derived": True}]
        )
    return {
        "topic": "T",
        "competitors": list(comps) + pads,
        "market_segments": [],
        "gaps": [],
        "opportunities": [{"title": f"机会{i}", "disrupt_score": 8} for i in (1, 2, 3)],
        "executive_summary": "s",
    }


def test_javascript_url_never_reaches_href():
    """url/strengths.source/pricing_source 全埋 javascript: payload,
    渲染产物中不得出现可执行的 javascript: 链接。"""
    evil = "javascript:alert(document.cookie)"
    comp = _comp(
        "Evil",
        evil,
        pricing_source=evil,
        strengths=[{"point": "合规认证 SOC2", "evidence": "", "source": evil}],
        feature_catalog={"Evil": [{"name": "Team Inbox", "desc": "d", "source": evil}]},
    )
    html = _render(_analysis([comp]))
    assert 'href="javascript:' not in html
    assert "href='javascript:" not in html
    # 变体协议同样不得出现
    assert 'href="data:' not in html
    assert 'href="vbscript:' not in html


def test_competitor_name_cannot_break_out_of_attribute():
    """竞品名注入 title 属性(payload: X" onmouseover="alert(7))。

    该名同时进入模板侧({{c.name}} autoescape)与 Python 预渲染侧
    (_render_section5_2_html 的 <td title="...{cn}...">,经 |safe 输出)——
    两条路径都必须转义,不得留下可执行事件属性。
    """
    payload = 'X" onmouseover="alert(7)'
    comp = _comp(
        payload,
        "https://evil.example.com",
        feature_catalog={
            payload: [
                {
                    "name": "Team Inbox",
                    "desc": "d",
                    "source": "https://evil.example.com/features",
                }
            ]
        },
    )
    html = _render(_analysis([comp]))
    # 可执行属性形式(payload 原文中的 " 未被转义)不得存在
    assert 'onmouseover="alert(7)' not in html
    # 转义形式(&quot; / &#34;)允许存在 —— 那是无害文本
    assert "onmouseover=&quot;alert(7)" in html or "onmouseover=&#34;alert(7)" in html


def test_pricing_source_honest_no_fabricated_guess():
    """pricing_source 有值 → 原样出现;无值 → 显示"未采集",
    绝不用 {官网}/pricing 拼接冒充来源(历史诚实性 bug)。"""
    real = _comp(
        "Real",
        "https://real.io",
        pricing_source="https://real-pricing-page.example.com/plans",
    )
    nosrc = _comp("NoSrc", "https://nosrc.io")  # 无 pricing_source/pricing_url
    html = _render(_analysis([real, nosrc]))
    # 真实来源原样出现
    assert "https://real-pricing-page.example.com/plans" in html
    # 不得用 c.url + "/pricing" 伪造
    assert "https://nosrc.io/pricing" not in html
    # 无数据时如实标注
    assert "未采集" in html
