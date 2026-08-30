#!/usr/bin/env python3
"""2026-08-30 审计修复的回归测试(含门禁对抗用例)。

每条对应实测事故(见 docs/audit/2026-08-30-crawler-core-audit.md):
  P0-1 单页异常炸整批(gather 无 return_exceptions + ex.map 传播)
  P0-2 robots 失败缓存键错位 + 裸 urllib 无 certifi(合规门静默失效)
  P0-3 firecrawl 纯头部截断丢页尾定价表
  P0-5 deep_link 单引擎 → 定价四级回退第三级永远无法交叉验证
  P0-7 G2 pricing_from_cache 的 continue 放错循环层级 → 伪造引文免检
  P1   PRICE_TOKEN_RX 漏检 $.012 类无前导零小数价
  P1   audit 价格计数跨全页 → docs 页 shell 示例虚增 engines_with_price
  P1   定价页 primary 按"洁净度"当选 → 零价格正文丢合并视图(tidio 实测)
"""

import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters import _md_quality, _merge_results  # noqa: E402
from adapters.firecrawl_scraper import _truncate  # noqa: E402
from gates import g2_quote_grep  # noqa: E402
from pricing_tokens import PRICE_TOKEN_RX, price_vote_key  # noqa: E402
from scripts import fetch as fetch_mod  # noqa: E402
from verify import Report  # noqa: E402


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
    return {
        "name": name,
        "canonical_name": name,
        "url": "https://wati.io",
        "pricing_url": "https://wati.io/pricing",
        "features_url": "https://wati.io/features",
        "docs_url": None,
        "source": "builtin",
        "confidence": 0.95,
    }


# conftest 的 autouse fixture 会把 _robots_disallows 桩成"允许";
# 模块导入时留一份真实实现供行为测试还原
_REAL_ROBOTS_DISALLOWS = fetch_mod._robots_disallows


# ═══════════ P0-7 G2 门禁旁路:缓存回退竞品的伪造引文必须仍被拦截 ═══════════


def _g2_analysis(from_cache: bool) -> dict:
    return {
        "competitors": [
            {
                "name": "X",
                "pricing_source": "https://x.io/pricing",
                "pricing_from_cache": from_cache,
                "strengths": [
                    {
                        "point": "p",
                        "evidence": '官网原文: "THIS QUOTE IS FABRICATED NOT IN ANY ENGINE"',
                        "source": "https://x.io/pricing",
                    }
                ],
                "pricing_vote_detail": [{"line": "$59/mo", "raw_line": "$59/mo"}],
            }
        ]
    }


_G2_ENGINE_INDEX = {"https://x.io/pricing": {"playwright": "real content here"}}
_G2_MANIFEST = {"fetched": {"https://x.io/pricing": {"status": "ok", "engines": {}}}}


def test_g2_cache_fallback_does_not_bypass_quote_checks():
    """对抗用例:pricing_from_cache=True 时,strengths 伪造引文必须仍被
    hard 拦截(历史:continue 放错层级 → 该竞品全部 G2 检查被丢弃,0 命中)。
    同时 vote 行回查仍按设计跳过(缓存证据本轮原文天然不含)。"""
    rep = Report()
    g2_quote_grep(_g2_analysis(True), _G2_MANIFEST, _G2_ENGINE_INDEX, rep)
    fields = [v["field"] for v in rep.violations]
    assert any("strengths[0].evidence" in f for f in fields), (
        "缓存回退竞品的伪造 strengths 引文被免检 —— G2 旁路回归"
    )
    assert not any("pricing_vote_detail" in f for f in fields), (
        "vote 行在 pricing_from_cache=True 时应跳过(缓存证据语义)"
    )


def test_g2_vote_lines_checked_when_fresh():
    """非缓存竞品:vote 行照常回查(raw_line 不在引擎原文 → hard)。"""
    rep = Report()
    g2_quote_grep(_g2_analysis(False), _G2_MANIFEST, _G2_ENGINE_INDEX, rep)
    fields = [v["field"] for v in rep.violations]
    assert any("pricing_vote_detail" in f for f in fields)


# ═══════════ P0-8(实测新发现)中段价格保窗截断 ═══════════


def test_truncate_keeps_midpage_price_windows():
    """tidio 实测形态:111K 页面,5 个价格全在 65K-95K 中段 —— 头(40K)
    尾(10K)截断后全丢(合并视图/证据库/jina 引擎级三处同时失价,
    交叉验证随机失败)。keep_rx 保窗必须救回。"""
    from adapters import truncate_md
    from pricing_tokens import PRICE_TOKEN_RX

    head = "nav junk " * 7300  # ~65K
    mid = "Starter $24.17/mo " * 20 + "faq " * 5000 + "Pro $49.17/mo " * 20
    tail = "footer " * 2200
    md = head + mid + tail
    assert len(md) > 100000

    # 历史行为(无保窗):中段价格全丢 —— 本用例的存在意义
    legacy = truncate_md(md, 50000)
    assert "$24.17" not in legacy and "$49.17" not in legacy

    kept = truncate_md(md, 50000, keep_rx=PRICE_TOKEN_RX)
    assert "$24.17" in kept and "$49.17" in kept, "中段价格窗口必须保留"
    assert len(kept) <= 54000, "保窗截断仍须守住总预算"
    assert kept.startswith("nav junk"), "头部上下文保留"
    assert "footer" in kept[-12000:], "尾部保留"


def test_truncate_keep_rx_no_matches_falls_back():
    from adapters import truncate_md
    from pricing_tokens import PRICE_TOKEN_RX

    md = "plain " * 20000  # 120K 无价格
    out = truncate_md(md, 50000, keep_rx=PRICE_TOKEN_RX)
    assert "plain" in out[:100] and "plain" in out[-100:]
    assert len(out) <= 52000


# ═══════════ P0-3 firecrawl 头尾截断 ═══════════


def test_firecrawl_truncate_keeps_tail_pricing_table():
    md = "CSS junk " * 8000 + "||TAIL_PRICING_TABLE|| $59/mo $119/mo"
    out = _truncate(md, 50000)
    assert "TAIL_PRICING_TABLE" in out, (
        "firecrawl 截断必须保尾(历史:纯头部截断丢页尾套餐表)"
    )
    assert "[... 中间内容已截断 ...]" in out or len(out) <= 50000


# ═══════════ P0-2 robots 失败缓存 + SSL context ═══════════


def test_robots_failure_cached_at_origin(monkeypatch):
    """拉取失败必须缓存到 origin 键(历史:缓存到完整 URL → 永不命中,
    每 URL 重拉 5s 超时)。conftest 的 autouse fixture 会把 _robots_disallows
    桩成允许 —— 本测试显式还原真实实现再验证其行为。"""
    calls = {"n": 0}

    def fake_urlopen(req, *a, **kw):
        calls["n"] += 1
        raise OSError("simulated network failure")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(fetch_mod, "_ROBOTS_CACHE", {})
    monkeypatch.setattr(fetch_mod, "_robots_disallows", _REAL_ROBOTS_DISALLOWS)
    d1 = fetch_mod._robots_disallows("https://example.com/some/page")
    d2 = fetch_mod._robots_disallows("https://example.com/other/page")
    assert d1 == [] and d2 == []
    assert calls["n"] == 1, "同 origin 失败第二次必须命中缓存,不重拉"
    assert "https://example.com" in fetch_mod._ROBOTS_CACHE


def test_robots_uses_certifi_ssl_context():
    """python.org 构建裸 urllib SSL 必失败 → 合规门静默失效(本机实测)。
    certifi 可用时模块必须携带 SSL context。"""
    try:
        import certifi  # noqa: F401
    except ImportError:
        return  # 无 certifi 环境:回退系统默认是既有行为
    assert fetch_mod._SSL_CTX is not None


# ═══════════ P0-1 单页异常隔离 ═══════════


def test_single_page_crash_isolated(tmp_path, monkeypatch):
    """单页 _fetch_page 内部异常:不得炸掉整竞品(历史:gather 无
    return_exceptions,一个异常取消全部兄弟任务且整体上抛)。"""
    monkeypatch.setattr(fetch_mod, "resolve_competitor", _fake_resolve)
    monkeypatch.setattr(
        fetch_mod,
        "scrape_smart",
        lambda url, **kw: {
            "success": True,
            "scraper": "trafilatura",
            "markdown": "content " * 60,
            "all_results": [_eng("trafilatura", "content " * 60)],
            "stats": {"successful": 1},
        },
    )

    real_fetch_page = fetch_mod._fetch_page

    def boom(kind, url, **kw):
        if kind == "pricing":
            raise RuntimeError("simulated page bug")
        return real_fetch_page(kind, url, **kw)

    monkeypatch.setattr(fetch_mod, "_fetch_page", boom)
    result = fetch_mod.fetch_competitor("wati.io", out_dir=tmp_path, budget_s=30)
    # 崩溃页降级为诚实失败;其余页面照常
    assert "pricing" in result["pages"]
    assert result["pages"]["pricing"]["sufficient"] is False
    assert any("crashed" in f["error"] for f in result["failures"]), (
        "崩溃页必须有 failure 留痕"
    )
    assert any(k and v.get("sufficient") for k, v in result["pages"].items()), (
        "非崩溃页面不应被单页异常拖死"
    )
    manifest = json.loads((tmp_path / "claims-manifest.json").read_text())
    assert manifest["fetched"], "台账必须照常落盘"


# ═══════════ P0-5 deep_link 各引擎原文 → 四级回退恢复交叉验证 ═══════════


def test_deep_link_fallback_cross_validates(tmp_path, monkeypatch):
    """定价页全灭 → deep_link 定位 + 各引擎原文透传 → ≥2 独立引擎见同价
    → sufficient(历史:单引擎合成条目 → 该级回退必然 insufficient)。"""
    monkeypatch.setattr(fetch_mod, "resolve_competitor", _fake_resolve)

    def fake_scrape(url, **kw):
        if url == "https://wati.io":
            return {
                "success": True,
                "scraper": "trafilatura",
                "markdown": "homepage no nav links",
                "all_results": [_eng("trafilatura", "homepage no nav links")],
                "stats": {"successful": 1},
            }
        return {
            "success": False,
            "scraper": "none",
            "markdown": "",
            "all_results": [],
            "stats": {},
        }

    monkeypatch.setattr(fetch_mod, "scrape_smart", fake_scrape)

    from scripts import deep_link

    pricing_md = "# Pricing\n\nGrowth $59/mo\nPro $119/mo\n"
    monkeypatch.setattr(
        deep_link,
        "locate_pricing_page",
        lambda domain, timeout=30, budget_s=None: {
            "url": "https://wati.io/en/pricing",
            "markdown": pricing_md,
            "engine": "trafilatura",
            "fetched_at": "2026-08-30 00:00 UTC",
            "all_results": [
                {"success": True, "scraper": "trafilatura", "markdown": pricing_md},
                {"success": True, "scraper": "jina", "markdown": pricing_md},
            ],
        },
    )

    result = fetch_mod.fetch_competitor("wati.io", out_dir=tmp_path, budget_s=30)
    page = result["pages"]["pricing"]
    assert page["sufficient"] is True, (
        f"deep_link 双引擎同价必须充分(历史:单引擎必 insufficient): {page}"
    )
    assert set(page["engines"]) >= {"trafilatura", "jina"}
    manifest = json.loads((tmp_path / "claims-manifest.json").read_text())
    eng_md = manifest["fetched"]["https://wati.io/en/pricing"]["engines"]
    assert {"trafilatura", "jina"} <= set(eng_md), "各引擎原文必须进台账"


# ═══════════ P1 价格正则:无前导零小数价 ═══════════


def test_price_regex_subdollar_no_leading_zero():
    assert PRICE_TOKEN_RX.search("Pay $.012 per conversation") is not None
    assert PRICE_TOKEN_RX.search("starts at $.99") is not None
    m = PRICE_TOKEN_RX.search("Pay $.012/conversation")
    assert m and price_vote_key(m.group(0)) == "$|012"


def test_price_regex_positive_shapes_unchanged():
    for text in ["$59", "US$39", "₹999", "Rs. 999", "39 €", "59 USD", "$1,068"]:
        assert PRICE_TOKEN_RX.search(text), f"{text} 必须仍命中"


# ═══════════ P1 audit 价格计数:定价页优先,docs shell 噪声不虚增 ═══════════


def test_audit_price_counting_prefers_pricing_pages():
    from audit import _price_tokens_per_engine

    comp = {"url": "https://wati.io"}
    raw_index = {
        "https://wati.io/pricing": {"trafilatura": "Growth $39/mo"},
        "https://wati.io/docs": {"jina": "run install.sh && echo $1 $2 $3"},
    }
    manifest = {
        "fetched": {
            "https://wati.io/pricing": {"kind": "pricing", "kinds": ["pricing"]},
            "https://wati.io/docs": {"kind": "docs", "kinds": ["docs"]},
        }
    }
    counts = _price_tokens_per_engine(comp, raw_index, manifest)
    assert counts.get("jina") in (None, 0), "docs 页 shell 变量不得计入"
    assert counts.get("trafilatura") == 1


def test_audit_price_counting_falls_back_without_pricing_page():
    """无定价语义页时退回全页统计(定价藏在 docs 的按量计费竞品仍可发现)。"""
    from audit import _price_tokens_per_engine

    comp = {"url": "https://wati.io"}
    raw_index = {"https://wati.io/docs/api": {"trafilatura": "$0.005 per message"}}
    manifest = {"fetched": {"https://wati.io/docs/api": {"kind": "docs"}}}
    counts = _price_tokens_per_engine(comp, raw_index, manifest)
    assert counts.get("trafilatura") == 1


# ═══════════ P1 定价页 primary:内容完整性优先于洁净度 ═══════════


def test_merge_primary_pricing_prefers_price_bearing_engine():
    """tidio 实测形态:playwright(引擎排名高)拿到零价格高洁净正文,
    trafilatura(排名低)拿到含价正文 —— 定价页必须选后者。

    历史行为:第一维 q>=0.5 都过线 → 引擎排名决定 → 零价格的 playwright
    当选,合并视图丢全部价格。"""
    clean_no_price = "\n".join(
        f"# Section {i}\n\n- Tidio makes customer service easy and fast"
        for i in range(30)
    )
    with_price = "Growth $29/mo\nPro $59/mo\n" * 10
    # 前提自检:无价正文洁净度更高(否则本用例不构成对抗)
    assert _md_quality(clean_no_price) > _md_quality(with_price)
    results = [
        _eng("playwright", clean_no_price),
        _eng("trafilatura", with_price),
    ]
    merged = _merge_results(results, url_type="pricing")
    assert merged["stats"]["primary_scraper"] == "trafilatura"


def test_merge_primary_non_pricing_unchanged():
    """非定价页不引入价格维度(回归保护:历史 CSS 垃圾拒绝行为不变)。"""
    junk = ".css-1{height:36px;background:#1A1E22;@media(min-width:991px)" + "a" * 80000
    clean = "# Pricing\n\nGrowth $39/mo details\n" * 20
    merged = _merge_results(
        [_eng("playwright", junk), _eng("trafilatura", clean)], url_type="docs"
    )
    assert merged["stats"]["primary_scraper"] == "trafilatura"


# ═══════════ P0-4 run_youzi 竞品级:并行 + 单竞品崩溃隔离 ═══════════


# ═══════════ P1(第 3 轮)from_cache 落台账 ═══════════


def test_from_cache_written_to_manifest(tmp_path, monkeypatch):
    """定价四级回退命中缓存 → manifest.fetched 条目必须带 from_cache=True
    (历史:标记只存在内存返回值,台账无此字段 → G2 的 pricing_from_cache
    豁免分支实际不可达,Step 3 LLM 也无从得知该竞品走了缓存)。"""
    import time as _t

    monkeypatch.setattr(fetch_mod, "resolve_competitor", _fake_resolve)

    def fake_scrape(url, **kw):
        if url == "https://wati.io":
            return {
                "success": True,
                "scraper": "trafilatura",
                "markdown": "homepage",
                "all_results": [_eng("trafilatura", "homepage")],
                "stats": {"successful": 1},
            }
        return {
            "success": False,
            "scraper": "none",
            "markdown": "",
            "all_results": [],
            "stats": {},
        }

    monkeypatch.setattr(fetch_mod, "scrape_smart", fake_scrape)
    # 封闭 deep_link 通道(否则真实搜索成功会走第三级回退,轮不到缓存)
    from scripts import deep_link as dl

    monkeypatch.setattr(dl, "locate_pricing_page", lambda domain, timeout=30: None)
    # 预种 ≤14 天已验证缓存(双引擎同价,交叉验证可复现)
    cache = {
        "wati.io": {
            "url": "https://wati.io/pricing",
            "engines": {
                "playwright": "Growth $59/mo\nPro $119/mo\n",
                "jina": "Growth $59/mo\nPro $119/mo\n",
            },
            "fetched_at": _t.strftime("%Y-%m-%d %H:%M UTC", _t.gmtime()),
        }
    }
    (tmp_path / "pricing-cache.json").write_text(json.dumps(cache))

    result = fetch_mod.fetch_competitor("wati.io", out_dir=tmp_path, budget_s=30)
    assert result["pages"]["pricing"]["sufficient"] is True
    assert result["pages"]["pricing"].get("from_cache") is True
    manifest = json.loads((tmp_path / "claims-manifest.json").read_text())
    ent = manifest["fetched"]["https://wati.io/pricing"]
    assert ent.get("from_cache") is True, "台账条目必须带 from_cache(G2 豁免依据)"
    assert {"playwright", "jina"} <= set(ent["engines"])


# ═══════════ P0-6(第 3 轮)resolver websearch 兜底 ═══════════


def test_resolver_websearch_finds_official_site(monkeypatch):
    """非内置纯产品名 → websearch 发现官网(域名含产品 token 才采纳)。"""
    from adapters.competitor_resolver import resolve_competitor
    from scripts import deep_link

    def fake_search(q, n=6):
        return [
            {"title": "Cursor - The AI Code Editor", "url": "https://cursor.com"},
            {"title": "Cursor reviews", "url": "https://g2.com/products/cursor"},
        ]

    monkeypatch.setattr(deep_link, "search_web", fake_search)
    r = resolve_competitor("cursor")
    assert r is not None and r["source"] == "websearch"
    assert r["url"].startswith("https://cursor.com")
    assert 0.5 < r["confidence"] < 0.95


def test_resolver_websearch_rejects_mismatched_domain(monkeypatch):
    """搜索结果域名不含产品 token(张冠李戴)→ 不采纳,诚实 None。"""
    from adapters.competitor_resolver import resolve_competitor
    from scripts import deep_link

    monkeypatch.setattr(
        deep_link,
        "search_web",
        lambda q, n=6: [{"title": "Best AI tools", "url": "https://techcrunch.com/x"}],
    )
    assert resolve_competitor("cursorrr") is None


def test_run_youzi_step2_crash_isolated(tmp_path, monkeypatch):
    """单竞品 fetch_competitor 崩溃:step2 不上抛,降级为 crash failure。"""
    from scripts import fetch as fm
    from scripts import run_youzi

    def fake_fetch(comp, out_dir, budget_s=None, topic=""):
        if comp == "bad":
            raise RuntimeError("simulated competitor crash")
        return {
            "name": comp,
            "url": f"https://{comp}.io",
            "pages": {
                "homepage": {
                    "url": f"https://{comp}.io",
                    "engines": ["jina"],
                    "sufficient": True,
                    "problems": [],
                }
            },
            "failures": [],
        }

    monkeypatch.setattr(fm, "fetch_competitor", fake_fetch)
    results = run_youzi.step2_fetch(["good", "bad"], tmp_path)
    assert len(results) == 2, "崩溃竞品不得吞掉正常竞品"
    crashed = [r for r in results if r.get("name") == "bad"]
    assert crashed and crashed[0]["failures"][0]["kind"] == "crash"


# ═══════════ 第 4 轮:audit kind/kinds 多值兼容 ═══════════


def test_audit_genuine_pricing_url_reads_kinds():
    """home_as_pricing 场景:首页条目 kinds=[homepage, pricing] ——
    _has_genuine_pricing_url 必须认出 pricing 语义(历史:读单值 kind
    → 漏判 → 误走「不是真实定价页」分支)。"""
    from audit import _has_genuine_pricing_url

    comp = {"url": "https://x.io"}
    manifest = {
        "fetched": {
            "https://x.io": {
                "kind": "homepage",
                "kinds": ["homepage", "pricing"],  # home_as_pricing 形态
            }
        }
    }
    assert _has_genuine_pricing_url(comp, manifest) is True
    # 域名根定价页(无路径)也算真实
    manifest2 = {"fetched": {"https://x.io": {"kind": "pricing", "kinds": ["pricing"]}}}
    assert _has_genuine_pricing_url(comp, manifest2) is True
    # 旧格式(只有单值 kind)兼容
    manifest3 = {"fetched": {"https://x.io/plans": {"kind": "pricing"}}}
    assert _has_genuine_pricing_url(comp, manifest3) is True


def test_scrape_with_fallback_removed():
    """死代码清理回归:旧版串行 fallback 不应再存在(零调用方已删)。"""
    import adapters

    assert not hasattr(adapters, "scrape_with_fallback")
    assert "scrape_with_fallback" not in adapters.__all__


# ═══════════ 第 15 轮:Step 3 证据助手 ═══════════


def test_evidence_safe_quotes_are_grep_verified(tmp_path):
    """safe_quotes 返回的每条引文必须通过 G2 同款回查(产品化上次手工摩擦)。"""
    import json as _json

    from gates import _quote_grep
    from scripts import evidence

    out = tmp_path
    (out / "02-raw").mkdir(parents=True)
    url = "https://x.io/features"
    (out / "claims-manifest.json").write_text(
        _json.dumps(
            {
                "fetched": {
                    url: {"status": "ok", "kind": "features", "kinds": ["features"]}
                }
            }
        )
    )
    md = "title: X\n---\n# Features\nBuild agents that qualify leads fast.\n[Link](https://x.io)\navoid 中文Englishespañol Português switcher line content\n"
    (out / "02-raw" / "X.engines.json").write_text(
        _json.dumps({url: {"trafilatura": md}})
    )
    _, idx = evidence.load_evidence(out)
    qs = evidence.safe_quotes(url, idx, n=3)
    assert qs, "应至少产出 1 条安全引文"
    for q in qs:
        assert _quote_grep(q, url, idx), f"引文未过 G2 回查: {q}"
        assert "title:" not in q and "http" not in q, "front-matter/链接行必须被过滤"


def test_evidence_quote_search_returns_verbatim(tmp_path):
    import json as _json

    from gates import _quote_grep
    from scripts import evidence

    out = tmp_path
    (out / "02-raw").mkdir(parents=True)
    url = "https://x.io/docs"
    (out / "claims-manifest.json").write_text(
        _json.dumps({"fetched": {url: {"status": "ok", "kind": "docs"}}})
    )
    md = "# API\nThe API is organized around REST principles.\nOther unrelated line here.\n"
    (out / "02-raw" / "X.engines.json").write_text(
        _json.dumps({url: {"trafilatura": md}})
    )
    hits = evidence.quote_search(out, url, "REST").strip().strip("`").split("\n")
    assert any(
        "REST" in h
        and _quote_grep(
            h,
            url,
            {"x": {url: {"trafilatura": md}}}.get("x") or {url: {"trafilatura": md}},
        )
        for h in hits
    )


# ═══════════ 第 20 轮:audit 字段完整性覆盖(用户三次投诉的字段) ═══════════


def test_audit_field_completeness_catches_render_block_gaps():
    """第 20 轮教训:feature_catalog/feature_conclusion_points/feature_best_for
    缺失 = 渲染板块整块消失,但 FIELD_MIN 旧表全不查 —— 完整性靠用户
    肉眼发现。现在必须检查器兜住。"""
    from audit import audit_field_completeness

    incomplete = {
        "tagline": "x",
        "pricing": "y",
        "core_features": ["a"] * 12,
        "strengths": ["s"] * 3,
        "weaknesses": ["w"],
        "differentiators": ["d"],
        "tech_signals": ["t"],
        # 缺:feature_catalog / feature_conclusion_points / feature_best_for
    }
    r = audit_field_completeness(incomplete)
    joined = " ".join(r["missing"])
    assert "feature_catalog" in joined, "矩阵整列空必须检出"
    assert "feature_conclusion_points" in joined, "§2.4 整块消失必须检出"
    assert "feature_best_for" in joined, "竞品卡标签缺失必须检出"
    assert r["status"] == "partial"
    # momentum 为聚合板块:只给 next_action,不算 missing
    assert any("product_momentum" in a for a in r["next_actions"])


def test_audit_field_completeness_passes_full():
    from audit import audit_field_completeness

    full = {
        "tagline": "x",
        "pricing": "y",
        "feature_best_for": "z",
        "core_features": ["a"] * 12,
        "strengths": ["s"] * 3,
        "weaknesses": ["w"],
        "differentiators": ["d"],
        "tech_signals": ["t"],
        "feature_catalog": {"C": [{"name": "n", "category": "c", "source": ""}] * 4},
        "feature_conclusion_points": [{"text": "t", "source": "https://x.io/f"}],
    }
    r = audit_field_completeness(full)
    assert r["status"] == "ok", r["missing"]
