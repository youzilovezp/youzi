# tests/test_fetch.py
# -*- coding: utf-8 -*-
"""fetch.py 取证层:多页采集 + 定价交叉验证升级梯 + 台账落盘。"""

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import fetch  # noqa: E402


def _eng(name, md, ok=True):
    return {
        "success": ok,
        "scraper": name,
        "markdown": md if ok else "",
        "html": "",
        "text": "",
        "screenshot": None,
        "extracted": None,
        "error": None if ok else "boom",
    }


def _fake_resolve(name):
    """resolver 打桩:真实 resolver 会把 wati.io 映射到内置 WATI
    (www.wati.io / canonical 'WATI'),测试断言按域名直通语义写。"""
    return {
        "name": name,
        "canonical_name": name,
        "url": "https://wati.io",
        "pricing_url": "https://wati.io/pricing",
        "features_url": "https://wati.io/features",
        "docs_url": "https://wati.io/docs",
        "source": "builtin",
        "confidence": 0.95,
    }


def test_vote_price_lines_cross_engine():
    rs = [
        _eng("playwright", "# Pro\n$39/user/month billed monthly\n"),
        _eng("trafilatura", "Pro — $39/user/mo\nGrowth — $19/mo\n"),
        _eng("jina", "Welcome to our site"),  # 无价格
    ]
    votes = fetch.vote_price_lines(rs)
    top = max(votes, key=lambda v: v["independent_votes"])
    assert "39" in top["token"]
    assert set(top["engines"]) == {"playwright", "trafilatura"}
    assert top["independent_votes"] == 2


def test_vote_price_lines_empty():
    assert fetch.vote_price_lines([_eng("jina", "no price here")]) == []


def test_fetch_competitor_writes_ledger_and_raw(tmp_path, monkeypatch):
    calls = []

    def fake_scrape_smart(url, enabled_scrapers=None, **kw):
        calls.append((url, enabled_scrapers))
        if url == "https://wati.io":
            return {
                "success": True,
                "scraper": "playwright",
                "markdown": "[Pricing](https://wati.io/pricing) [Docs](https://wati.io/docs)",
                "all_results": [_eng("playwright", "home md")],
                "stats": {"successful": 1},
            }
        if url.endswith("/pricing"):
            return {
                "success": True,
                "scraper": "playwright+trafilatura",
                "markdown": "Growth $39/mo",
                "all_results": [
                    _eng("playwright", "Growth $39/mo"),
                    _eng("trafilatura", "Growth $39/mo"),
                ],
                "stats": {"successful": 2},
            }
        return {
            "success": True,
            "scraper": "trafilatura",
            "markdown": "docs content " * 50,
            "all_results": [_eng("trafilatura", "docs content " * 50)],
            "stats": {"successful": 1},
        }

    monkeypatch.setattr(fetch, "scrape_smart", fake_scrape_smart)
    monkeypatch.setattr(fetch, "resolve_competitor", _fake_resolve)

    result = fetch.fetch_competitor("wati.io", out_dir=tmp_path)

    # 台账 + 引擎原文 + raw md
    manifest = json.loads((tmp_path / "claims-manifest.json").read_text())
    assert manifest["fetched"]["https://wati.io/pricing"]["status"] == "ok"
    engines = json.loads((tmp_path / "02-raw" / "wati_io.engines.json").read_text())
    assert "https://wati.io/pricing" in engines
    assert set(engines["https://wati.io/pricing"]) == {"playwright", "trafilatura"}
    raw_md = (tmp_path / "02-raw" / "wati_io.md").read_text()
    assert "# Kind: pricing" in raw_md and "# Source: https://wati.io/pricing" in raw_md
    # 定价页双引擎一致 → sufficient
    assert result["pages"]["pricing"]["sufficient"] is True
    # 接口契约:每个 pages 条目含 problems 键(list)
    for k, p in result["pages"].items():
        assert "problems" in p, f"{k} 缺 problems 键"
        assert isinstance(p["problems"], list)


def test_fetch_pricing_insufficient_triggers_ladder(tmp_path, monkeypatch):
    """单引擎无价格 → 升级梯换引擎重爬 → 达标。"""
    state = {"n": 0}

    def fake_scrape_smart(url, enabled_scrapers=None, **kw):
        if not url.endswith("/pricing"):
            return {
                "success": True,
                "scraper": "playwright",
                "markdown": "home",
                "all_results": [_eng("playwright", "home")],
                "stats": {"successful": 1},
            }
        state["n"] += 1
        if state["n"] == 1:  # 首棒:单引擎,无价格行
            return {
                "success": True,
                "scraper": "playwright",
                "markdown": "Plans coming soon",
                "all_results": [_eng("playwright", "Plans coming soon")],
                "stats": {"successful": 1},
            }
        # 升级棒:带来第二个引擎 + 价格行
        return {
            "success": True,
            "scraper": "+".join(enabled_scrapers or []),
            "markdown": "Growth $25/mo",
            "all_results": [
                _eng("playwright", "Plans coming soon"),
                _eng("trafilatura", "Growth $25/mo"),
                _eng("jina", "Growth $25/mo"),
            ],
            "stats": {"successful": 3},
        }

    monkeypatch.setattr(fetch, "scrape_smart", fake_scrape_smart)
    monkeypatch.setattr(fetch, "resolve_competitor", _fake_resolve)
    monkeypatch.setenv("FIRECRAWL_API_KEY", "")  # 保证梯子里无 firecrawl
    import os

    os.environ.pop("FIRECRAWL_API_KEY", None)

    result = fetch.fetch_competitor("wati.io", out_dir=tmp_path)
    assert state["n"] == 2, "定价不充分时应触发升级棒重爬"
    assert result["pages"]["pricing"]["sufficient"] is True


def test_fetch_budget_exhausted_honest(tmp_path, monkeypatch):
    """预算耗尽 → 不再重爬,标 insufficient(诚实)。"""

    def fake_scrape_smart(url, enabled_scrapers=None, **kw):
        if url.endswith("/pricing"):
            return {
                "success": True,
                "scraper": "playwright",
                "markdown": "no price",
                "all_results": [_eng("playwright", "no price")],
                "stats": {"successful": 1},
            }
        return {
            "success": True,
            "scraper": "playwright",
            "markdown": "home",
            "all_results": [_eng("playwright", "home")],
            "stats": {"successful": 1},
        }

    monkeypatch.setattr(fetch, "scrape_smart", fake_scrape_smart)
    monkeypatch.setattr(fetch, "resolve_competitor", _fake_resolve)
    import os

    os.environ.pop("FIRECRAWL_API_KEY", None)
    # 预算设为 0:首棒爬完即超预算(真实 monotonic 已耗时 >0) → 不重爬
    result = fetch.fetch_competitor("wati.io", out_dir=tmp_path, budget_s=0)
    assert result["pages"]["pricing"]["sufficient"] is False
    # insufficient 必须有 problems 说明原因(诚实)
    assert result["pages"]["pricing"]["problems"]
