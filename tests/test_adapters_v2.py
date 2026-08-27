# tests/test_adapters_v2.py
# -*- coding: utf-8 -*-
"""V2 引擎白名单:注册表只含 5 个存活引擎,firecrawl 需 key。"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import adapters  # noqa: E402


LIVE = {"playwright", "trafilatura", "newspaper3k", "jina", "firecrawl"}


def test_registry_only_live_engines():
    reg = adapters._build_adapter_registry()
    assert set(reg.keys()) == LIVE


def test_dead_engine_files_deleted():
    for dead in [
        "crawl4ai",
        "crawlee",
        "camoufox",
        "scrapy",
        "readability",
        "markdownify",
        "html2text",
        "requests_html",
    ]:
        assert not (ROOT / "adapters" / f"{dead}_scraper.py").exists(), dead


def test_classify_url_semantics_intact():
    assert adapters.classify_url("https://wati.io/pricing/") == "pricing"
    assert adapters.classify_url("https://docs.sleekflow.io/api") == "docs"
    assert adapters.classify_url("https://wati.io") == "homepage"


def test_firecrawl_needs_key(monkeypatch):
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    from adapters import firecrawl_scraper

    assert firecrawl_scraper.is_available() is False
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")
    assert firecrawl_scraper.is_available() is True


def test_recommend_scrapers_firecrawl_priority(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "test-key")
    engs = adapters.recommend_scrapers("https://x.com/pricing")
    assert engs[0] == "firecrawl"
    assert set(engs) <= LIVE


def test_url_type_scrapers_whitelisted():
    for engs in adapters._URL_TYPE_SCRAPERS.values():
        assert set(engs) <= LIVE, engs


def test_ladder_whitelisted_and_excludes_used(monkeypatch):
    from scripts import sufficiency

    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    for ut in sufficiency._ENGINE_LADDER_EXTRA:
        ladder = sufficiency.ladder_engines(ut, already_used=[])
        assert set(ladder) <= LIVE, (ut, ladder)
        assert sufficiency.ladder_engines(ut, already_used=ladder) == []


def test_ladder_firecrawl_first_with_key(monkeypatch):
    from scripts import sufficiency

    monkeypatch.setenv("FIRECRAWL_API_KEY", "k")
    assert sufficiency.ladder_engines("pricing", already_used=[])[0] == "firecrawl"
