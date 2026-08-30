#!/usr/bin/env python3
"""2026-08-29 审计修复的回归测试。

每条对应实测事故(见审计报告):
  - 单行巨型 CSS 稀释行级质量分 → primary 误选 → 截断丢正文(YCloud)
  - 导航发现只吃合并视图 → jina 看到的链接被丢 → 4 类页面丢失(YCloud)
  - 价格正则三套口径(₹ 漏检/US$59→US$5 截断)
  - home_as_pricing 覆盖台账 homepage kind → audit 假 gap
  - ≤14 天定价缓存回退(V2 重构丢失的能力)
"""

import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters import _md_quality, _merge_results, truncate_md  # noqa: E402
from pricing_tokens import (  # noqa: E402
    PRICE_TOKEN_RX,
    price_vote_key,
)
from scripts import fetch as fetch_mod  # noqa: E402


# ═══════════ P0-1 质量度量:单行巨型 CSS 不再稀释 ═══════════


def _css_junk_markdown(css_len=150000, body_lines=300):
    """YCloud 事故形态:一行巨型 CSS + 后续真实正文行。"""
    css = (
        ".css-179bgld{height:36px;background-color:#1A1E22;@media(min-width:991px)"
        + "a" * css_len
    )
    body = "\n".join(
        f"# Feature {i}\nReal product content line {i} with details."
        for i in range(body_lines)
    )
    return css + "\n" + body


def test_md_quality_single_line_giant_css_scores_low():
    """行级度量历史 bug:151KB 单行 CSS 在 380 行里只占 1 行 → junk_ratio
    0.3% → q=0.62 虚高。字符加权后同一样本必须显著低分。"""
    junk = _css_junk_markdown()
    clean = "# WATI\n\nReal pricing content with $39 and details.\n" * 50
    q_junk, q_clean = _md_quality(junk), _md_quality(clean)
    assert q_junk < 0.35, f"CSS 垃圾 markdown 质量分应 <0.35,实测 {q_junk:.3f}"
    assert q_clean > q_junk, "干净正文质量分应高于 CSS 垃圾"


def test_merge_primary_rejects_css_junk_engine():
    """CSS 垃圾引擎(即便引擎排名高)不得当选 primary(YCloud 实测:
    playwright q=0.62 假分压过 trafilatura q=0.635 真实定价表)。"""
    results = [
        {
            "success": True,
            "scraper": "playwright",
            "markdown": _css_junk_markdown(css_len=80000, body_lines=50),
        },
        {
            "success": True,
            "scraper": "trafilatura",
            "markdown": "# Pricing\n\nGrowth $39/mo\n\nPro $89/mo\n\nEnterprise Custom\n"
            * 10,
        },
    ]
    merged = _merge_results(results)
    assert merged["stats"]["primary_scraper"] == "trafilatura"


def test_truncate_md_keeps_head_and_tail():
    md = "HEAD" + "x" * 60000 + "TAIL_PRICING_TABLE"
    out = truncate_md(md, max_chars=50000, tail_chars=10000)
    assert out.startswith("HEAD")
    assert "TAIL_PRICING_TABLE" in out, "尾部定价表必须保留(历史:头部截断全丢)"
    assert len(out) < len(md)


# ═══════════ P0-2 多引擎导航发现 ═══════════


def test_discover_from_results_union_across_engines():
    """YCloud 事故:primary(playwright) 0 链接 → 发现空;jina 有链接。
    发现必须对每个成功引擎跑,取并集。"""
    all_results = [
        {"success": True, "scraper": "playwright", "markdown": "no links here at all"},
        {
            "success": True,
            "scraper": "jina",
            "markdown": (
                "[Pricing](https://www.ycloud.com/pricing) "
                "[Blog](https://www.ycloud.com/blog) "
                "[About Us](https://www.ycloud.com/about-us)"
            ),
        },
    ]
    found = fetch_mod.discover_from_results(all_results, "https://www.ycloud.com")
    assert found.get("pricing") == "https://www.ycloud.com/pricing"
    assert found.get("blog") == "https://www.ycloud.com/blog"
    assert found.get("about") == "https://www.ycloud.com/about-us"


def test_discover_urls_accepts_docs_subdomain():
    """同站判定放宽到可注册域:docs.wati.io 应能从 www.wati.io 首页发现。"""
    md = "[Read the Docs](https://docs.wati.io/docs/getting-started)"
    found = fetch_mod.discover_urls(md, "https://www.wati.io")
    assert found.get("docs") == "https://docs.wati.io/docs/getting-started"


def test_discover_urls_pagination_depth_tolerated():
    """分页段不计深:/customers/page/1 是栏目页,不因 depth=3 被拒。"""
    md = "[Customer Stories](https://x.com/customers/page/1)"
    found = fetch_mod.discover_urls(md, "https://x.com")
    assert found.get("testimonials") == "https://x.com/customers/page/1"


# ═══════════ P0-3 价格 token 统一 ═══════════


@pytest.mark.parametrize(
    "text,expect_hit",
    [
        ("$59", True),
        ("US$59", True),  # 历史 bug:sufficiency 截断成 US$5
        ("₹999", True),  # 历史 bug:fetch 投票正则漏检
        ("Rs. 999", True),
        ("€39", True),
        ("39 €", True),  # 欧陆后缀
        ("USD 39", True),
        ("$1,068", True),
        ("$0.012", True),  # 按量计费
        ("¥299", True),
        ("299元", True),
        ("Hours 39", False),  # 词边界:Hours 里的 rs 不是 Rs
        ("version 1.2", False),
    ],
)
def test_price_token_coverage(text, expect_hit):
    m = PRICE_TOKEN_RX.search(text)
    assert bool(m) is expect_hit, (
        f"{text!r} 期望 {'命中' if expect_hit else '不命中'},实测 {m.group(0) if m else None!r}"
    )


def test_price_vote_key_unifies_currency_writings():
    """跨引擎写法差异不得拆散交叉验证票。"""
    assert price_vote_key("$39") == price_vote_key("US$39") == price_vote_key("39 USD")
    assert price_vote_key("₹999") == price_vote_key("Rs. 999")
    assert price_vote_key("€39") == price_vote_key("39 €")
    assert price_vote_key("$39") != price_vote_key("$89")


def test_vote_price_lines_rupee_cross_validates():
    """印度系竞品(AiSensy/Interakt):₹ 定价两个引擎看到 → 交叉验证通过。
    历史实现投票正则漏 ₹ → 判不充分 → 升级梯空转。"""
    results = [
        {
            "scraper": "playwright",
            "success": True,
            "markdown": "Basic ₹999/mo Advanced ₹2,499/mo",
        },
        {
            "scraper": "trafilatura",
            "success": True,
            "markdown": "Basic: Rs. 999/month Advanced: Rs. 2,499",
        },
    ]
    assert fetch_mod._price_cross_validated(results) is True


# ═══════════ P0-4 台账 kinds 多值 ═══════════


def _fake_resolve(name):
    return {
        "name": "Q",
        "canonical_name": "Q",
        "url": "https://qzv.io",
        "pricing_url": "https://qzv.io/pricing",
        "features_url": None,
        "docs_url": None,
    }


def test_home_as_pricing_keeps_homepage_kind(tmp_path, monkeypatch):
    """home_as_pricing 触发时 fetched[base] 的 kinds 必须同时含
    homepage+pricing(历史 bug:homepage 被覆盖 → audit 假 gap)。"""
    fake_home = {
        "success": True,
        "scraper": "pw+tra",
        "markdown": "Growth $39/mo and Pro $89/mo for teams",
        "all_results": [
            {
                "scraper": "playwright",
                "success": True,
                "markdown": "Growth $39/mo Pro $89/mo",
            },
            {
                "scraper": "trafilatura",
                "success": True,
                "markdown": "Growth $39/mo Pro $89/mo",
            },
        ],
        "stats": {"successful": 2},
    }
    fake_fail = {
        "success": False,
        "scraper": "none",
        "markdown": "",
        "error": "all failed",
        "all_results": [
            {"scraper": "playwright", "success": False, "markdown": "", "error": "x"}
        ],
        "stats": {"successful": 0},
    }

    def fake_scrape(url, **kw):
        return fake_home if url == "https://qzv.io" else fake_fail

    monkeypatch.setattr(fetch_mod, "scrape_smart", fake_scrape)
    monkeypatch.setattr(fetch_mod, "resolve_competitor", _fake_resolve)
    from scripts import deep_link

    monkeypatch.setattr(
        deep_link, "locate_pricing_page", lambda domain, timeout=30: None
    )
    # 禁用缓存回退干扰(空缓存亦可:全灭+缓存空 → 走不到)
    monkeypatch.setattr(fetch_mod, "_cache_get", lambda domain: None)
    monkeypatch.setattr(fetch_mod, "_cache_put", lambda *a, **kw: None)
    monkeypatch.setattr(fetch_mod, "_robots_allowed", lambda url: True)

    r = fetch_mod.fetch_competitor("Q", tmp_path, budget_s=10)
    m = json.loads((tmp_path / "claims-manifest.json").read_text())
    ent = m["fetched"]["https://qzv.io"]
    assert set(ent["kinds"]) == {"homepage", "pricing"}, (
        f"kinds 应多值,实测 {ent['kinds']}"
    )
    assert r["pages"]["pricing"]["url"] == "https://qzv.io"
    assert r["pages"]["pricing"]["note"] == "定价来自首页非独立定价页"

    # audit 视角:homepage 不得误判缺失
    from audit import audit_page_coverage

    cov = audit_page_coverage({"name": "Q", "url": "https://qzv.io"}, m)
    assert "homepage" not in cov["missing_kinds"], (
        f"homepage 已抓到,缺失列表不应包含: {cov}"
    )


# ═══════════ P2-1 定价缓存回退 ═══════════


def test_pricing_cache_fallback_on_total_failure(tmp_path, monkeypatch):
    """定价页全灭 + 深链失败 + 首页无价 → ≤14 天缓存回退,G3 可复现
    (缓存带每引擎原文,内容哈希 ≥2)。"""
    fake_home = {
        "success": True,
        "scraper": "pw",
        "markdown": "welcome to quux no prices here",
        "all_results": [
            {
                "scraper": "playwright",
                "success": True,
                "markdown": "welcome to quux no prices",
            }
        ],
        "stats": {"successful": 1},
    }

    def fake_scrape(url, **kw):
        if url == "https://quux.io":
            return fake_home
        return {
            "success": False,
            "scraper": "none",
            "markdown": "",
            "error": "blocked",
            "all_results": [
                {
                    "scraper": "playwright",
                    "success": False,
                    "markdown": "",
                    "error": "blocked",
                }
            ],
            "stats": {"successful": 0},
        }

    fresh_cache = {
        "quux.io": {
            "url": "https://quux.io/pricing",
            "engines": {
                "playwright": "Growth $39/mo",
                "trafilatura": "Growth: $39 per month",
            },
            "fetched_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        }
    }
    monkeypatch.setattr(fetch_mod, "scrape_smart", fake_scrape)
    monkeypatch.setattr(
        fetch_mod,
        "resolve_competitor",
        lambda n: {
            "name": "Quux",
            "canonical_name": "Quux",
            "url": "https://quux.io",
            "pricing_url": "https://quux.io/pricing",
            "features_url": None,
            "docs_url": None,
        },
    )
    monkeypatch.setattr(fetch_mod, "_cache_load", lambda: fresh_cache)
    monkeypatch.setattr(fetch_mod, "_cache_put", lambda *a, **kw: None)
    monkeypatch.setattr(fetch_mod, "_robots_allowed", lambda url: True)
    from scripts import deep_link

    monkeypatch.setattr(
        deep_link, "locate_pricing_page", lambda domain, timeout=30: None
    )

    r = fetch_mod.fetch_competitor("Quux", tmp_path, budget_s=10)
    p = r["pages"]["pricing"]
    assert p["url"] == "https://quux.io/pricing"
    assert p.get("from_cache") is True
    assert "缓存回退" in p.get("note", "")

    # 台账:缓存引擎原文进 engines.json(G2 quote 可 grep)
    eng = json.loads((tmp_path / "02-raw" / "Quux.engines.json").read_text())
    md_by_eng = eng["https://quux.io/pricing"]
    assert "Growth $39/mo" in md_by_eng.get("playwright", "")
    assert set(md_by_eng) == {"playwright", "trafilatura"}

    # 交叉验证:缓存的两引擎原文能投出 ≥2 票
    assert fetch_mod._price_cross_validated(
        [
            {
                "scraper": "playwright",
                "success": True,
                "markdown": md_by_eng["playwright"],
            },
            {
                "scraper": "trafilatura",
                "success": True,
                "markdown": md_by_eng["trafilatura"],
            },
        ]
    )


def test_pricing_cache_expired_not_used(tmp_path, monkeypatch):
    """>14 天的缓存不回退(诚实 insufficient)。"""
    fake_home = {
        "success": True,
        "scraper": "pw",
        "markdown": "welcome",
        "all_results": [
            {"scraper": "playwright", "success": True, "markdown": "welcome"}
        ],
        "stats": {"successful": 1},
    }

    def fake_scrape(url, **kw):
        if url == "https://quux.io":
            return fake_home
        return {
            "success": False,
            "scraper": "none",
            "markdown": "",
            "error": "blocked",
            "all_results": [],
            "stats": {"successful": 0},
        }

    stale = {
        "quux.io": {
            "url": "https://quux.io/pricing",
            "engines": {"playwright": "$39", "trafilatura": "$39"},
            "fetched_at": "2025-01-01 00:00 UTC",
        }
    }
    monkeypatch.setattr(fetch_mod, "scrape_smart", fake_scrape)
    monkeypatch.setattr(
        fetch_mod,
        "resolve_competitor",
        lambda n: {
            "name": "Quux",
            "canonical_name": "Quux",
            "url": "https://quux.io",
            "pricing_url": "https://quux.io/pricing",
            "features_url": None,
            "docs_url": None,
        },
    )
    monkeypatch.setattr(fetch_mod, "_cache_load", lambda: stale)
    monkeypatch.setattr(fetch_mod, "_cache_put", lambda *a, **kw: None)
    monkeypatch.setattr(fetch_mod, "_robots_allowed", lambda url: True)
    from scripts import deep_link

    monkeypatch.setattr(
        deep_link, "locate_pricing_page", lambda domain, timeout=30: None
    )

    r = fetch_mod.fetch_competitor("Quux", tmp_path, budget_s=10)
    assert r["pages"]["pricing"].get("from_cache") is not True
    assert r["pages"]["pricing"]["sufficient"] is False


def test_pricing_cache_written_on_cross_validation(tmp_path, monkeypatch):
    """定价交叉验证成功 → 回写缓存(带每引擎原文)。"""
    good_pricing = {
        "success": True,
        "scraper": "pw+tra",
        "markdown": "Growth $39/mo Pro $89/mo",
        "all_results": [
            {
                "scraper": "playwright",
                "success": True,
                "markdown": "Growth $39/mo Pro $89/mo",
            },
            {
                "scraper": "trafilatura",
                "success": True,
                "markdown": "Growth: $39/mo Pro: $89/mo",
            },
        ],
        "stats": {"successful": 2},
    }
    fake_home = {
        "success": True,
        "scraper": "pw",
        "markdown": "welcome",
        "all_results": [
            {"scraper": "playwright", "success": True, "markdown": "welcome"}
        ],
        "stats": {"successful": 1},
    }

    def fake_scrape(url, **kw):
        if url.endswith("/pricing"):
            return good_pricing
        if url == "https://quux.io":
            return fake_home
        return {
            "success": False,
            "scraper": "none",
            "markdown": "",
            "error": "x",
            "all_results": [],
            "stats": {"successful": 0},
        }

    puts = []
    monkeypatch.setattr(fetch_mod, "scrape_smart", fake_scrape)
    monkeypatch.setattr(
        fetch_mod,
        "resolve_competitor",
        lambda n: {
            "name": "Quux",
            "canonical_name": "Quux",
            "url": "https://quux.io",
            "pricing_url": "https://quux.io/pricing",
            "features_url": None,
            "docs_url": None,
        },
    )
    monkeypatch.setattr(fetch_mod, "_cache_load", lambda: {})
    monkeypatch.setattr(
        fetch_mod,
        "_cache_put",
        lambda domain, url, engines_md: puts.append((domain, url, engines_md)),
    )
    monkeypatch.setattr(fetch_mod, "_robots_allowed", lambda url: True)

    r = fetch_mod.fetch_competitor("Quux", tmp_path, budget_s=10)
    assert r["pages"]["pricing"]["sufficient"] is True
    assert puts, "交叉验证成功后应回写缓存"
    domain, url, engines_md = puts[0]
    assert domain == "quux.io"
    assert set(engines_md) == {"playwright", "trafilatura"}


# ═══════════ P1 预算与并行 ═══════════


def test_budget_deadline_skips_pages_honestly(tmp_path, monkeypatch):
    """预算耗尽 → 未开始的页面诚实记 budget failure,不再抓取。"""
    calls = {"n": 0}

    def fake_scrape(url, **kw):
        calls["n"] += 1
        return {
            "success": True,
            "scraper": "pw",
            "markdown": "content " * 60,
            "all_results": [
                {"scraper": "playwright", "success": True, "markdown": "content " * 60}
            ],
            "stats": {"successful": 1},
        }

    monkeypatch.setattr(fetch_mod, "scrape_smart", fake_scrape)
    monkeypatch.setattr(
        fetch_mod,
        "resolve_competitor",
        lambda n: {
            "name": "Q",
            "canonical_name": "Q",
            "url": "https://qzv.io",
            "pricing_url": "https://qzv.io/pricing",
            "features_url": "https://qzv.io/features",
            "docs_url": "https://qzv.io/docs",
        },
    )
    monkeypatch.setattr(fetch_mod, "_robots_allowed", lambda url: True)
    monkeypatch.setattr(fetch_mod, "_cache_put", lambda *a, **kw: None)

    # 预算 0:首页抓完后,其余页面全部跳过
    r = fetch_mod.fetch_competitor("Q", tmp_path, budget_s=0.0)
    budget_failures = [f for f in r["failures"] if "budget" in (f.get("error") or "")]
    assert budget_failures, "预算耗尽的页面应记录 budget failure"
    assert calls["n"] == 1, "首页之后不应再发起抓取"


def test_page_level_parallelism(tmp_path, monkeypatch):
    """多类目标页面并行抓取:总墙钟 ≈ 错峰+最慢单页,而非各页之和。"""
    import threading

    lock = threading.Lock()
    active = {"now": 0, "peak": 0}
    PAGE_S = 1.0  # 单页耗时(模拟真实网络页)

    def fake_scrape(url, **kw):
        with lock:
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
        time.sleep(PAGE_S)
        with lock:
            active["now"] -= 1
        md = "content " * 60
        engines = [{"scraper": "playwright", "success": True, "markdown": md}]
        if url.endswith("/pricing"):
            # 双引擎同价:交叉验证立即通过,不走升级梯(该路径另有测试)
            engines.append(
                {
                    "scraper": "trafilatura",
                    "success": True,
                    "markdown": "Growth $39/mo Pro $89/mo",
                }
            )
            engines[0]["markdown"] = "Growth $39/mo Pro $89/mo"
        return {
            "success": True,
            "scraper": "pw+tra" if url.endswith("/pricing") else "pw",
            "markdown": engines[0]["markdown"],
            "all_results": engines,
            "stats": {"successful": len(engines)},
        }

    monkeypatch.setattr(fetch_mod, "scrape_smart", fake_scrape)
    monkeypatch.setattr(
        fetch_mod,
        "resolve_competitor",
        lambda n: {
            "name": "Q",
            "canonical_name": "Q",
            "url": "https://qzv.io",
            "pricing_url": "https://qzv.io/pricing",
            "features_url": "https://qzv.io/features",
            "docs_url": "https://qzv.io/docs",
        },
    )
    monkeypatch.setattr(fetch_mod, "_robots_allowed", lambda url: True)
    monkeypatch.setattr(fetch_mod, "_cache_put", lambda *a, **kw: None)

    t0 = time.monotonic()
    r = fetch_mod.fetch_competitor("Q", tmp_path, budget_s=60)
    elapsed = time.monotonic() - t0
    n_pages = len(r["pages"])
    assert n_pages >= 3, f"应有 ≥3 类页面(猜测路径),实测 {list(r['pages'])}"
    # 串行 = (1 首页 + n 页) × 1.0s;并行 + 0.8s 错峰应整体快于串行。
    # (小页数下错峰会吃掉部分收益;并发的硬证明是 peak ≥ 2)
    serial = (n_pages + 1) * PAGE_S
    assert elapsed < serial, f"并行下墙钟 {elapsed:.2f}s 应小于串行 {serial:.2f}s"
    assert active["peak"] >= 2, "应观察到页面级并发"


# ═══════════ P2-3 robots ═══════════


def test_robots_disallow_blocks_and_records(tmp_path, monkeypatch):
    """robots.txt disallow 的页面诚实跳过并记失败,不硬闯。"""
    monkeypatch.setattr(fetch_mod, "_robots_disallows", lambda u: ["/pricing"])
    fake_home = {
        "success": True,
        "scraper": "pw",
        "markdown": "welcome",
        "all_results": [
            {"scraper": "playwright", "success": True, "markdown": "welcome"}
        ],
        "stats": {"successful": 1},
    }
    monkeypatch.setattr(fetch_mod, "scrape_smart", lambda url, **kw: fake_home)
    monkeypatch.setattr(
        fetch_mod,
        "resolve_competitor",
        lambda n: {
            "name": "Q",
            "canonical_name": "Q",
            "url": "https://qzv.io",
            "pricing_url": "https://qzv.io/pricing",
            "features_url": None,
            "docs_url": None,
        },
    )
    r = fetch_mod.fetch_competitor("Q", tmp_path, budget_s=10)
    assert "pricing" not in r["pages"], "disallow 的定价页不应被抓取"
    assert any("robots" in (f.get("error") or "") for f in r["failures"])


def test_robots_root_disallow_blocks_competitor(tmp_path, monkeypatch):
    """整站 disallow → 该竞品诚实全灭。"""
    monkeypatch.setattr(fetch_mod, "_robots_disallows", lambda u: ["/"])
    monkeypatch.setattr(
        fetch_mod,
        "resolve_competitor",
        lambda n: {
            "name": "Q",
            "canonical_name": "Q",
            "url": "https://qzv.io",
            "pricing_url": None,
            "features_url": None,
            "docs_url": None,
        },
    )
    r = fetch_mod.fetch_competitor("Q", tmp_path, budget_s=10)
    assert r["pages"] == {}
    assert any(f.get("kind") == "robots" for f in r["failures"])


# ═══════════ P2-2 engine-stats 线程安全 ═══════════


def test_engine_stats_thread_safe_increment():
    """并发 record 不丢更新(历史:last-write-wins 丢计数)。"""
    import adapters

    before = (
        adapters._load_engine_stats()
        .get("pw-test", {})
        .get("thread-check", {})
        .get("n", 0)
    )
    import concurrent.futures as cf

    def hit(i):
        adapters.record_engine_outcome(
            "thread-check", {"pw-test": {"success": True, "quality": 0.5}}
        )

    with cf.ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(hit, range(32)))
    after = adapters._load_engine_stats()["pw-test"]["thread-check"]["n"]
    assert after - before == 32, f"并发计数应精确 +32,实测 +{after - before}"
    # 清理测试桶
    with adapters._STATS_LOCK:
        stats = adapters._load_engine_stats()
        stats.get("pw-test", {}).pop("thread-check", None)
        adapters._save_engine_stats(stats)


def test_engine_stats_legacy_buckets_pruned(tmp_path, monkeypatch):
    """legacy 桶(无 last 时间戳 = 2026-08-29 质量分修正前的失真数据)
    载入时自动清除,不再污染排序。"""
    import adapters

    legacy_file = tmp_path / "engine-stats.json"
    legacy_file.write_text(
        json.dumps(
            {
                "firecrawl": {"homepage": {"n": 410, "ok": 57, "q_sum": 53.3}},
                "trafilatura": {
                    "docs": {"n": 5, "ok": 5, "q_sum": 3.0, "last": time.time()}
                },
            }
        )
    )
    monkeypatch.setattr(adapters, "_ENGINE_STATS_PATH", legacy_file)
    monkeypatch.setattr(adapters, "_STATS_MEM", None)
    stats = adapters._load_engine_stats()
    assert "firecrawl" not in stats, "legacy 桶应被清除"
    assert "trafilatura" in stats and "docs" in stats["trafilatura"]


def test_discover_urls_ignores_images_and_cdn_assets():
    """linear.app 事故:同站放宽到可注册域后,webassets CDN 的 PNG 截图
    (alt 文本含 features)被当成 features 页抓取。"""
    md = (
        "![features screenshot](https://webassets.linear.app/images/abc/production/f792.png) "
        "[Features](https://linear.app/features) "
        "[logo](https://linear.app/static/logo.svg)"
    )
    found = fetch_mod.discover_urls(md, "https://linear.app")
    assert found.get("features") == "https://linear.app/features", f"实测 {found}"
