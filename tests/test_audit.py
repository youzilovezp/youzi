# tests/test_audit.py
# -*- coding: utf-8 -*-
"""audit.py 自我审计器回归:定价深度状态机 + not-published 终态 + 反哺 lessons。

覆盖的核心行为:
  1. Step 2.5 形态(无 analysis):≥2 引擎有价格 token = ok,1 引擎 = partial
  2. not-published 终态:0 token × 全引擎 + 有替代探测 → 终态情报而非 gap
  3. 0 token 且无任何探测 → gap(采集失败)
  4. 月付/年付配对:仅年付 = partial 且 next_action 提示找 toggle
  5. lessons 沉淀:not-published 写入经验,再次运行 runs_seen 递增
"""

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from audit import run_audit, _upsert_lesson  # noqa: E402


def _mk(tmp: Path, manifest_comps, raw_pages, analysis=None):
    """构造最小 manifest + engines.json + 可选 analysis。

    manifest_comps: [{domain, pages: [(url, kind)]}]
    raw_pages: [{url, engines: {eng: markdown}}]
    """
    manifest = {"fetched": {}}
    for comp in manifest_comps:
        for url, kind in comp["pages"]:
            manifest["fetched"][url] = {"status": "ok", "kind": kind, "engines": {}}
    mp = tmp / "claims-manifest.json"
    mp.write_text(json.dumps(manifest), encoding="utf-8")
    raw = tmp / "02-raw"
    raw.mkdir()
    by_file = {}
    for pg in raw_pages:
        by_file.setdefault(pg.get("comp", "C"), {})[pg["url"]] = pg["engines"]
    for cname, pages in by_file.items():
        (raw / f"{cname}.engines.json").write_text(json.dumps(pages), encoding="utf-8")
    return manifest, raw, analysis


def test_step25_multi_engine_tokens_ok():
    """≥2 引擎看到价格 token → pricing_depth ok(Step 2.5 形态)。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        manifest, raw, _ = _mk(
            tmp,
            [
                {
                    "domain": "a.io",
                    "pages": [
                        ("https://a.io", "homepage"),
                        ("https://a.io/pricing", "pricing"),
                        ("https://a.io/features", "features"),
                        ("https://docs.a.io/intro", "docs"),
                        ("https://a.io/customers", "testimonials"),
                    ],
                }
            ],
            [
                {
                    "url": "https://a.io/pricing",
                    "comp": "A",
                    "engines": {
                        "jina": "Pro $39/mo",
                        "trafilatura": "Pro is $39 per month",
                    },
                }
            ],
        )
        r = run_audit(manifest, raw, None)
        pd = list(r["competitors"].values())[0]["pricing_depth"]
        assert pd["status"] == "ok"
        assert pd["priced_tiers"] == 0  # Step 2.5 无套餐结构
        assert r["summary"]["unresolved_gaps"] == []


def test_step25_single_engine_partial():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        manifest, raw, _ = _mk(
            tmp,
            [
                {
                    "domain": "a.io",
                    "pages": [
                        ("https://a.io", "homepage"),
                        ("https://a.io/pricing", "pricing"),
                    ],
                }
            ],
            [
                {
                    "url": "https://a.io/pricing",
                    "comp": "A",
                    "engines": {"jina": "Pro $39/mo"},
                }
            ],
        )
        r = run_audit(manifest, raw, None)
        pd = list(r["competitors"].values())[0]["pricing_depth"]
        assert pd["status"] == "partial"
        assert any("交叉验证" in a for a in pd["next_actions"])


def test_not_published_terminal_state():
    """0 价格 token + 已探测替代路径 → not-published 终态,lessons 沉淀。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        manifest, raw, _ = _mk(
            tmp,
            [
                {
                    "domain": "b.io",
                    "pages": [
                        ("https://b.io", "homepage"),
                        ("https://b.io/pricing", "pricing"),
                    ],
                }
            ],
            [
                {
                    "url": "https://b.io/pricing",
                    "comp": "B",
                    "engines": {"jina": "专业版 企业版", "trafilatura": "定制版"},
                },
                # 第三方替代证据源(探测过的证明)
                {
                    "url": "https://help.b.io/price",
                    "comp": "B",
                    "engines": {"jina": "以官方计费为准"},
                },
            ],
        )
        # help 页也要进 manifest(证明探测过)
        manifest["fetched"]["https://help.b.io/price"] = {
            "status": "ok",
            "kind": "docs",
            "engines": {},
        }
        (tmp / "claims-manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )
        r = run_audit(manifest, raw, None)
        pd = list(r["competitors"].values())[0]["pricing_depth"]
        assert pd["status"] == "not-published"
        assert "b.io" in r["lessons_new"]
        assert r["summary"]["not_published"]


def test_zero_tokens_no_probe_is_gap():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        manifest, raw, _ = _mk(
            tmp,
            [{"domain": "c.io", "pages": [("https://c.io", "homepage")]}],
            [{"url": "https://c.io", "comp": "C", "engines": {"jina": "hello"}}],
        )
        r = run_audit(manifest, raw, None)
        pd = list(r["competitors"].values())[0]["pricing_depth"]
        assert pd["status"] == "gap"
        assert r["summary"]["unresolved_gaps"]


def test_monthly_annual_pairing():
    """仅年付价 = partial + 找 toggle 的 next_action;双周期 = ok。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        manifest, raw, _ = _mk(
            tmp,
            [
                {
                    "domain": "d.io",
                    "pages": [
                        ("https://d.io", "homepage"),
                        ("https://d.io/pricing", "pricing"),
                    ],
                }
            ],
            [
                {
                    "url": "https://d.io/pricing",
                    "comp": "D",
                    "engines": {
                        "jina": "$39 billed annually $468/yr",
                        "trafilatura": "$39 billed annually",
                    },
                }
            ],
        )
        analysis = {
            "competitors": [
                {
                    "name": "D",
                    "url": "https://d.io",
                    "pricing_tiers": [
                        {
                            "name": "Pro",
                            "price": "$39",
                            "billing_period": "billed",
                            "source_url": "https://d.io/pricing",
                        },
                    ],
                }
            ]
        }
        r = run_audit(manifest, raw, analysis)
        pd = r["competitors"]["D"]["pricing_depth"]
        assert pd["monthly"] is False and pd["annual"] is True
        assert pd["status"] == "partial"
        assert any("toggle" in a for a in pd["next_actions"])
        # 补齐月付后 → ok
        analysis["competitors"][0]["pricing_tiers"].insert(
            0,
            {
                "name": "Pro",
                "price": "$49",
                "billing_period": "/mo",
                "source_url": "https://d.io/pricing",
            },
        )
        r2 = run_audit(manifest, raw, analysis)
        assert r2["competitors"]["D"]["pricing_depth"]["status"] == "ok"


def test_price_vote_comma_normalization():
    """'$1,068' 与原文 '$1068' 数字归一后算同一价格。"""
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        manifest, raw, _ = _mk(
            tmp,
            [
                {
                    "domain": "e.io",
                    "pages": [
                        ("https://e.io", "homepage"),
                        ("https://e.io/pricing", "pricing"),
                    ],
                }
            ],
            [
                {
                    "url": "https://e.io/pricing",
                    "comp": "E",
                    "engines": {
                        "jina": "Billed $1068 /yr",
                        "trafilatura": "Billed $1068 yearly",
                    },
                }
            ],
        )
        analysis = {
            "competitors": [
                {
                    "name": "E",
                    "url": "https://e.io",
                    "pricing_tiers": [
                        {
                            "name": "Pro",
                            "price": "$1,068",
                            "billing_period": "/yr",
                            "source_url": "https://e.io/pricing",
                        }
                    ],
                }
            ]
        }
        r = run_audit(manifest, raw, analysis)
        votes = r["competitors"]["E"]["price_votes"]["votes"]
        assert votes and votes[0]["agree"] is True
        assert set(votes[0]["engines_seen"]) == {"jina", "trafilatura"}


def test_lesson_upsert_accumulates():
    """同一域名同一 issue 再出现 → runs_seen 递增、evidence 刷新。"""
    lessons = {}
    _upsert_lesson(
        lessons, "x.io", "pricing_not_published", "confirmed", ["e1"], ["alt1"], "hint1"
    )
    _upsert_lesson(
        lessons, "x.io", "pricing_not_published", "confirmed", ["e2"], [], "hint2"
    )
    les = lessons["x.io"]["lessons"][0]
    assert les["runs_seen"] == 2
    assert les["evidence"] == ["e2"]
    assert les["alt_sources"] == ["alt1"]  # 空列表不覆盖
    assert les["hint"] == "hint2"
