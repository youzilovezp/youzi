# youzi V2 精简重构实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 引擎砍到 4+1（playwright/trafilatura/newspaper3k/jina + 有 key 时的 firecrawl），删除 165KB 启发式提取单体，语义提取 100% 回归 LLM，报告定价区重设计 —— 准 > 全 > 美。

**Architecture:** 三层职责：脚本层只取证（fetch.py：爬取+落盘+台账），LLM 层唯一负责语义提取（Step 3），门禁层审判（verify G1-G6 不变）。spec 见 `docs/superpowers/specs/2026-08-27-youzi-v2-lean-refactor-design.md`。

**Tech Stack:** Python 3 + requests/playwright/trafilatura/newspaper3k/Jinja2 渲染（全部已装）。测试 pytest（pytest.ini 已配）。

## Global Constraints

- 仓库根：`/Users/zhangpeng/workspace/liaohe/youzi/youzi`（下文相对路径均基于此）
- 测试命令：`python3 -m pytest tests/ -x -q`（除 Task 8 实爬外全部离线可跑）
- 证据铁律不可违反：字段必须带 {值, source_url, quote}；抓不到标「未验证」，绝不伪造
- 台账格式 = 现有 claims-manifest.json（gates.py G1/G3 已消费该格式，不发明新格式）
- 引擎存活白名单：playwright, trafilatura, newspaper3k, jina, firecrawl(需 `FIRECRAWL_API_KEY`)
- 提交信息风格：`feat:/refactor:/test:/docs: 中文描述`；docs/superpowers 被 .gitignore 忽略，提交用 `git add -f`
- 删除文件前先确认无活引用（grep 验证步骤内置在各任务里）

---

### Task 1: adapters 瘦身 — 删 8 死引擎 + firecrawl key 门控

**Files:**
- Delete: `adapters/crawl4ai_scraper.py`, `adapters/crawlee_scraper.py`, `adapters/camoufox_scraper.py`, `adapters/scrapy_scraper.py`, `adapters/readability_scraper.py`, `adapters/markdownify_scraper.py`, `adapters/html2text_scraper.py`, `adapters/requests_html_scraper.py`
- Modify: `adapters/__init__.py`
- Modify: `adapters/firecrawl_scraper.py:15-27`（is_available）
- Test: `tests/test_adapters_v2.py`（新建）

**Interfaces:**
- Produces: 注册表仅含 5 引擎；`scrape_smart/classify_url/recommend_scrapers` 签名不变；`recommend_scrapers` 在 firecrawl 可用时将其插到组合首位

- [ ] **Step 1: 写失败测试**

```python
# tests/test_adapters_v2.py
# -*- coding: utf-8 -*-
"""V2 引擎白名单:注册表只含 5 个存活引擎,firecrawl 需 key。"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import adapters


LIVE = {"playwright", "trafilatura", "newspaper3k", "jina", "firecrawl"}


def test_registry_only_live_engines():
    reg = adapters._build_adapter_registry()
    assert set(reg.keys()) == LIVE


def test_dead_engine_files_deleted():
    for dead in ["crawl4ai", "crawlee", "camoufox", "scrapy",
                 "readability", "markdownify", "html2text", "requests_html"]:
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_adapters_v2.py -x -q`
Expected: FAIL（注册表仍是 13 引擎、死引擎文件还在）

- [ ] **Step 3: 删除 8 个死引擎文件**

```bash
git rm adapters/crawl4ai_scraper.py adapters/crawlee_scraper.py \
  adapters/camoufox_scraper.py adapters/scrapy_scraper.py \
  adapters/readability_scraper.py adapters/markdownify_scraper.py \
  adapters/html2text_scraper.py adapters/requests_html_scraper.py
```

- [ ] **Step 4: 改 firecrawl_scraper.is_available（仅 key 门控）**

```python
def is_available() -> bool:
    """V2:仅 FIRECRAWL_API_KEY 存在时启用(无 key 的 CLI 通道曾长期 402 欠费,
    engine-stats ok=0.12,纯噪声)。"""
    return bool(os.environ.get("FIRECRAWL_API_KEY"))
```

（删除原函数里 CLI 探测分支；`os` 已 import。）

- [ ] **Step 5: 重写 `adapters/__init__.py` 的引擎面**

修改点（其余函数——`_md_quality`/`_merge_results`/`scrape_smart` 等——全部不动）：

1. 模块 docstring 的爬虫清单改为 5 引擎白名单描述。
2. `_URL_TYPE_SCRAPERS` 替换为：

```python
_URL_TYPE_SCRAPERS = {
    # V2 白名单(2026-08-27 重构,依据 engine-stats n=700+):
    #   playwright = JS 页王者(pricing q=0.50 / homepage q=0.42)
    #   trafilatura = 静态正文王者(docs q=0.57)
    #   newspaper3k = 文章型(blog/customer q=0.67)
    #   jina = 第三方渲染交叉验证票
    #   firecrawl 由 recommend_scrapers 动态插首(需 FIRECRAWL_API_KEY)
    "pricing":     ["playwright", "trafilatura", "jina"],
    "docs":        ["trafilatura", "jina"],
    "dashboard":   ["playwright"],
    "about":       ["trafilatura", "jina"],
    "integration": ["trafilatura", "jina"],
    "customer":    ["newspaper3k", "trafilatura"],
    "blog":        ["newspaper3k", "trafilatura"],
    "feature":     ["playwright", "trafilatura", "jina"],
    "changelog":   ["trafilatura", "jina"],
    "testimonials": ["trafilatura", "newspaper3k"],
    "homepage":    ["playwright", "trafilatura", "jina"],
}
```

3. `_build_adapter_registry` 的 import 和字典缩减为 5 项：

```python
def _build_adapter_registry():
    from adapters import (
        firecrawl_scraper,
        trafilatura_scraper,
        newspaper3k_scraper,
        jina_scraper,
        playwright_scraper,
    )
    return {
        "firecrawl": (firecrawl_scraper, True, False, False),
        "trafilatura": (trafilatura_scraper, False, False, False),
        "newspaper3k": (newspaper3k_scraper, False, False, False),
        "jina": (jina_scraper, False, False, False),
        "playwright": (playwright_scraper, True, True, True),
    }
```

4. `_ENGINE_QUALITY` 替换为：

```python
_ENGINE_QUALITY = {
    "firecrawl": 0, "playwright": 1, "trafilatura": 2,
    "jina": 3, "newspaper3k": 4,
}
```

5. `recommend_scrapers` 在返回前插入 firecrawl 优先位（函数体末尾改为）：

```python
    scored.sort(reverse=True)
    ordered = [eng for _, _, eng in scored]
    # firecrawl 有 key 时插首位(商业 API 覆盖最强);无 key 不出现
    from adapters import firecrawl_scraper as _fc
    if _fc.is_available() and "firecrawl" not in ordered:
        ordered.insert(0, "firecrawl")
    return ordered
```

注意 `need_login` 分支保持 `["playwright"]`（camoufox 已删）。`scrape_with_fallback`（旧兼容函数）里对 crawl4ai 的引用整段删除，保留 firecrawl→playwright 两级。

- [ ] **Step 6: 清理 engine-stats 死引擎桶**

```bash
python3 - <<'EOF'
import json
from pathlib import Path
p = Path("storage/engine-stats.json")
d = json.loads(p.read_text(encoding="utf-8"))
live = {"playwright", "trafilatura", "newspaper3k", "jina", "firecrawl"}
d = {k: v for k, v in d.items() if k in live}
p.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
EOF
```

- [ ] **Step 7: 跑测试确认通过**

Run: `python3 -m pytest tests/test_adapters_v2.py -x -q`
Expected: 6 passed

- [ ] **Step 8: 提交**

```bash
git add -A && git commit -m "refactor: 引擎砍到4+1 — 删8死引擎, firecrawl仅key启用, 路由表白名单化"
```

---

### Task 2: sufficiency 升级梯收敛到白名单引擎

**Files:**
- Modify: `scripts/sufficiency.py:12-28`（`_ENGINE_LADDER_EXTRA`）
- Test: `tests/test_adapters_v2.py`（追加）

**Interfaces:**
- Produces: `ladder_engines(url_type, already_used)` 返回值 ⊆ 白名单，且 firecrawl 有 key 时排最前

- [ ] **Step 1: 追加失败测试**

```python
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
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_adapters_v2.py -k ladder -x -q`
Expected: FAIL（现梯子里有 crawl4ai/firecrawl(无 key)/readability）

- [ ] **Step 3: 替换 `_ENGINE_LADDER_EXTRA` 并改 `ladder_engines`**

```python
# ── V2 引擎升级梯(白名单内,排除已用) ──
_ENGINE_LADDER_EXTRA = {
    "pricing":     ["trafilatura", "jina", "newspaper3k"],
    "docs":        ["jina", "playwright", "newspaper3k"],
    "homepage":    ["jina", "newspaper3k"],
    "feature":     ["jina", "newspaper3k"],
    "about":       ["playwright", "newspaper3k"],
    "blog":        ["jina", "playwright"],
    "customer":    ["jina", "playwright"],
    "testimonials": ["jina", "playwright"],
    "changelog":   ["playwright", "newspaper3k"],
    "integration": ["playwright", "newspaper3k"],
    "dashboard":   [],
}


def ladder_engines(url_type: str, already_used: List[str]) -> List[str]:
    """返回升级梯下一棒引擎(白名单内、未用过的)。

    firecrawl 有 key 时排最前(商业 API 是最强增援)。
    注意:梯子含"首棒组合里的引擎"没关系 —— already_used 会把它们排除,
    首棒失败/低质的引擎换不同引擎重试才是目的。
    """
    pool = list(_ENGINE_LADDER_EXTRA.get(url_type, []))
    from adapters import firecrawl_scraper as _fc
    if _fc.is_available():
        pool = ["firecrawl"] + pool
    used = set(already_used or [])
    seen, out = set(), []
    for e in pool:
        if e not in used and e not in seen:
            seen.add(e)
            out.append(e)
    return out
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_adapters_v2.py -x -q`
Expected: 8 passed

- [ ] **Step 5: 提交**

```bash
git add scripts/sufficiency.py tests/test_adapters_v2.py && git commit -m "refactor: 升级梯收敛到白名单引擎 + firecrawl动态增援"
```

---

### Task 3: scripts/fetch.py — 新取证采集器

**Files:**
- Create: `scripts/fetch.py`
- Test: `tests/test_fetch.py`（新建）

**Interfaces:**
- Consumes: `adapters.resolve_competitor(name) -> {name, url, pricing_url, docs_url, features_url, ...}`（`adapters/competitor_resolver.py:193`）；`adapters.scrape_smart(url, enabled_scrapers=[...]) -> {success, markdown, all_results, stats, scraper}`；`scripts.sufficiency.{assess_page_content, assess_pricing, ladder_engines, COMPETITOR_BUDGET_SECONDS}`；`scripts.deep_link.locate_pricing_page(domain) -> {url, markdown} | None`
- Produces:
  - `fetch_competitor(name, out_dir, budget_s=300) -> dict`：`{"name", "url", "pages": {kind: {"url", "engines", "sufficient", "problems"}}, "failures": [...]}`
  - `main()` CLI：`python3 scripts/fetch.py --competitors "wati,respond.io" --out-dir OUT [--budget 300]`
  - 落盘：`OUT/02-raw/<name>.md`（每页一节，header 含 Kind/Source/Scrapers/Time）、`OUT/02-raw/<name>.engines.json`（`{url: {engine: markdown}}`，verify.py 消费）、`OUT/claims-manifest.json`（`{run, fetched, claims: [], failures}`，gates.py 消费）
  - `vote_price_lines(all_results) -> [{"token", "engines", "independent_votes"}]`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_fetch.py
# -*- coding: utf-8 -*-
"""fetch.py 取证层:多页采集 + 定价交叉验证升级梯 + 台账落盘。"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import fetch


def _eng(name, md, ok=True):
    return {"success": ok, "scraper": name, "markdown": md if ok else "",
            "html": "", "text": "", "screenshot": None, "extracted": None,
            "error": None if ok else "boom"}


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
            return {"success": True, "scraper": "playwright",
                    "markdown": "[Pricing](https://wati.io/pricing) [Docs](https://wati.io/docs)",
                    "all_results": [_eng("playwright", "home md")],
                    "stats": {"successful": 1}}
        if url.endswith("/pricing"):
            return {"success": True, "scraper": "playwright+trafilatura",
                    "markdown": "Growth $39/mo",
                    "all_results": [
                        _eng("playwright", "Growth $39/mo"),
                        _eng("trafilatura", "Growth $39/mo"),
                    ],
                    "stats": {"successful": 2}}
        return {"success": True, "scraper": "trafilatura",
                "markdown": "docs content " * 50,
                "all_results": [_eng("trafilatura", "docs content " * 50)],
                "stats": {"successful": 1}}

    monkeypatch.setattr(fetch, "scrape_smart", fake_scrape_smart)

    result = fetch.fetch_competitor("wati.io", out_dir=tmp_path)

    # 台账 + 引擎原文 + raw md
    manifest = json.loads((tmp_path / "claims-manifest.json").read_text())
    assert manifest["fetched"]["https://wati.io/pricing"]["status"] == "ok"
    engines = json.loads(
        (tmp_path / "02-raw" / "wati_io.engines.json").read_text())
    assert "https://wati.io/pricing" in engines
    assert set(engines["https://wati.io/pricing"]) == {"playwright", "trafilatura"}
    raw_md = (tmp_path / "02-raw" / "wati_io.md").read_text()
    assert "# Kind: pricing" in raw_md and "# Source: https://wati.io/pricing" in raw_md
    # 定价页双引擎一致 → sufficient
    assert result["pages"]["pricing"]["sufficient"] is True


def test_fetch_pricing_insufficient_triggers_ladder(tmp_path, monkeypatch):
    """单引擎无价格 → 升级梯换引擎重爬 → 达标。"""
    state = {"n": 0}

    def fake_scrape_smart(url, enabled_scrapers=None, **kw):
        if not url.endswith("/pricing"):
            return {"success": True, "scraper": "playwright",
                    "markdown": "home",
                    "all_results": [_eng("playwright", "home")],
                    "stats": {"successful": 1}}
        state["n"] += 1
        if state["n"] == 1:  # 首棒:单引擎,无价格行
            return {"success": True, "scraper": "playwright",
                    "markdown": "Plans coming soon",
                    "all_results": [_eng("playwright", "Plans coming soon")],
                    "stats": {"successful": 1}}
        # 升级棒:带来第二个引擎 + 价格行
        return {"success": True, "scraper": "+".join(enabled_scrapers or []),
                "markdown": "Growth $25/mo",
                "all_results": [
                    _eng("playwright", "Plans coming soon"),
                    _eng("trafilatura", "Growth $25/mo"),
                    _eng("jina", "Growth $25/mo"),
                ],
                "stats": {"successful": 3}}

    monkeypatch.setattr(fetch, "scrape_smart", fake_scrape_smart)
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
            return {"success": True, "scraper": "playwright",
                    "markdown": "no price",
                    "all_results": [_eng("playwright", "no price")],
                    "stats": {"successful": 1}}
        return {"success": True, "scraper": "playwright", "markdown": "home",
                "all_results": [_eng("playwright", "home")],
                "stats": {"successful": 1}}

    monkeypatch.setattr(fetch, "scrape_smart", fake_scrape_smart)
    import os
    os.environ.pop("FIRECRAWL_API_KEY", None)
    monkeypatch.setattr(fetch.time, "monotonic",
                        lambda: fetch.time.monotonic.__wrapped__
                        if hasattr(fetch.time.monotonic, "__wrapped__") else 0)
    # 直接把预算设为 0:首棒爬完即超预算 → 不重爬
    result = fetch.fetch_competitor("wati.io", out_dir=tmp_path, budget_s=0)
    assert result["pages"]["pricing"]["sufficient"] is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_fetch.py -x -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.fetch'`

- [ ] **Step 3: 实现 scripts/fetch.py**

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""youzi V2 · 取证层采集器 —— 只取证,不做语义提取。

职责(全部,没有更多):
  1. URL 发现   resolver 猜路径 + 首页导航链接发现兜底(404 语义)
  2. 爬取       scrape_smart 智能路由(4+1 白名单引擎)
  3. 落盘       02-raw/<name>.md + <name>.engines.json(每引擎原文独立)
  4. 台账       claims-manifest.json.fetched(url × engine × hash × 时间)

充分性闭环:定价页 ≥2 独立引擎看到相同价格才 sufficient;不达标沿
升级梯换未用引擎重爬;全灭时 deep_link 搜索发现官方定价页;
预算(默认 300s/竞品)耗尽 → 诚实标 insufficient。

语义提取(tiers/features/tagline/...)是 LLM Step 3 的工作,这里不做。
"""
import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters import scrape_smart  # noqa: E402
from adapters.competitor_resolver import resolve_competitor  # noqa: E402
from scripts import sufficiency  # noqa: E402

# 首页导航发现模式(自 crawl_competitors 移植,语义不变)
_DISCOVER_PATTERNS = {
    "pricing": re.compile(r"pricing|price|plans?|定价|价格|套餐", re.I),
    "features": re.compile(
        r"features?|functionalit|capabilities|platform|product|产品|功能", re.I),
    "about": re.compile(r"^about|about[-\s]us|company|our[-\s]story|team$|关于|公司", re.I),
    "docs": re.compile(r"^docs?$|documentation|developers?|api[-\s]docs", re.I),
    "testimonials": re.compile(
        r"testimonials?|customer[-\s]?stor(y|ies)|case[-\s]?stud|success[-\s]?stor"
        r"|customers$|reviews?|口碑|客户案例|用户评价", re.I),
    "blog": re.compile(r"^blog$|blogs?/|news|changelog|release[-\s]?notes?|updates?|博客|动态", re.I),
}
# 深链栏目限制:testimonials/blog 只收栏目页(路径 ≤2 段)
_PAGE_ORDER = ["pricing", "features", "docs", "about", "testimonials", "blog"]

_PRICE_TOKEN_RX = re.compile(
    r"(?<![.\d])[$€£¥]\s?\d[\d,]*(?:\.\d+)?(?:\s?/\s?(?:mo|month|yr|year|user/month|seat/mo))?", re.I)


def _content_hash(md: str) -> str:
    return hashlib.sha256(" ".join((md or "").split()).encode("utf-8")).hexdigest()[:16]


def discover_urls(home_md: str, base_url: str) -> Dict[str, str]:
    """首页 markdown 链接 → {kind: url}。只认同域 http(s),链接文本优先。"""
    found: Dict[str, str] = {}
    for m in re.finditer(r"\[([^\]\[]{2,25})\]\(([^)#\s]+)\)", home_md or ""):
        text, href = m.group(1).strip(), m.group(2).strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        full = urljoin(base_url.rstrip("/") + "/", href)
        if not full.startswith("http"):
            continue
        try:
            if (urlparse(full).netloc.replace("www.", "")
                    != urlparse(base_url).netloc.replace("www.", "")):
                continue
        except Exception:
            continue
        for kind, pat in _DISCOVER_PATTERNS.items():
            if kind in found:
                continue
            if pat.search(text) or pat.search(
                    full.replace(base_url.rstrip("/"), "", 1).split("?")[0]):
                url_clean = full.split("?")[0].split("#")[0]
                if kind in ("testimonials", "blog"):
                    depth = len([p for p in urlparse(url_clean).path.split("/") if p])
                    if depth > 2:
                        continue
                found[kind] = url_clean
    return found


def vote_price_lines(all_results: List[Dict]) -> List[Dict]:
    """跨引擎价格行投票(证据级,非语义提取):同一价格 token 被 ≥2 独立
    引擎看到 = 交叉验证票。LLM Step 3/G3 门禁消费。"""
    by_token: Dict[str, Dict] = {}
    for r in all_results or []:
        eng = r.get("scraper", "?")
        if not (r.get("success") and r.get("markdown")):
            continue
        seen_in_engine = set()
        for m in _PRICE_TOKEN_RX.finditer(r["markdown"]):
            tok = re.sub(r"\s+", "", m.group(0)).lower()
            if tok in seen_in_engine:
                continue
            seen_in_engine.add(tok)
            slot = by_token.setdefault(
                tok, {"token": m.group(0), "engines": [], "independent_votes": 0})
            if eng not in slot["engines"]:
                slot["engines"].append(eng)
                slot["independent_votes"] += 1
    return [v for v in by_token.values() if v["independent_votes"] >= 1]


def _merged_ok(result: Dict) -> bool:
    return bool(result.get("success") and result.get("markdown"))


def fetch_competitor(name: str, out_dir: Path, budget_s: float = None) -> Dict:
    """采集单个竞品全部证据页。预算内不充分则沿升级梯重爬定价页。"""
    budget_s = sufficiency.COMPETITOR_BUDGET_SECONDS if budget_s is None else budget_s
    t0 = time.monotonic()
    resolved = resolve_competitor(name)
    if not resolved:
        return {"name": name, "url": "", "pages": {}, "failures": [
            {"competitor": name, "url": "", "kind": "resolve", "error": "not_found"}]}
    base = resolved["url"]
    cname = re.sub(r"[^\w\-]", "_", (resolved.get("canonical_name") or name))

    raw_dir = out_dir / "02-raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    fetched: Dict[str, Dict] = {}
    failures: List[Dict] = []
    pages: Dict[str, Dict] = {}
    engines_doc: Dict[str, Dict[str, str]] = {}
    raw_sections: List[str] = []

    def _record(url: str, result: Dict, kind: str):
        engines_md, engines_meta = {}, {}
        for x in (result.get("all_results") or []):
            if x.get("scraper") and x.get("success") and x.get("markdown"):
                engines_md[x["scraper"]] = x["markdown"][:50000]
                engines_meta[x["scraper"]] = {
                    "ok": True, "chars": len(x["markdown"]),
                    "content_hash": _content_hash(x["markdown"])}
        engines_doc[url] = engines_md
        ok = _merged_ok(result)
        fetched[url] = {
            "status": "ok" if ok else "failed",
            "kind": kind,
            "engines": engines_meta,
            "fetched_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        }
        if not ok:
            failures.append({"competitor": cname, "url": url, "kind": kind,
                             "error": (result.get("error") or "all engines empty")[:200]})
        if engines_md:
            scrapers = ",".join(engines_md)
            raw_sections.append(
                f"# Kind: {kind}\n# Source: {url}\n# Scrapers: {scrapers}\n"
                f"# Time: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}\n\n"
                + (result.get("markdown") or ""))

    # ── 首页 ──
    home = scrape_smart(base)
    _record(base, home, "homepage")
    home_md = home.get("markdown") or ""

    # ── URL 发现:resolver 猜路径优先,导航发现补缺/纠错 ──
    targets: Dict[str, str] = {}
    guessed = {"pricing": resolved.get("pricing_url"),
               "features": resolved.get("features_url"),
               "docs": resolved.get("docs_url")}
    nav = discover_urls(home_md, base)
    for kind in _PAGE_ORDER:
        targets[kind] = nav.get(kind) or guessed.get(kind)
    targets = {k: v for k, v in targets.items() if v and v != base}

    # ── 逐页爬取 + 页面级充分性 + 定价交叉验证升级梯 ──
    for kind, url in targets.items():
        result = scrape_smart(url)
        used = [r.get("scraper") for r in (result.get("all_results") or [])
                if r.get("success")]
        if kind == "pricing":
            votes = vote_price_lines(result.get("all_results") or [])
            assess = sufficiency.assess_pricing([], vote_detail=votes)
            # 页面壳都不行 → 先按页面级处理
            while ((not _merged_ok(result)
                    or not sufficiency.assess_page_content(kind, result.get("markdown") or ""))
                   or not assess["sufficient"]):
                if time.monotonic() - t0 > budget_s:
                    break
                extra = sufficiency.ladder_engines("pricing", already_used=used)
                if not extra:
                    break
                retry = scrape_smart(url, enabled_scrapers=extra[:2])
                if not _merged_ok(retry):
                    failures.append({"competitor": cname, "url": url,
                                     "kind": kind, "error": "ladder retry failed"})
                    break
                merged_all = ((result.get("all_results") or [])
                              + (retry.get("all_results") or []))
                result = {"success": True,
                          "scraper": "+".join(dict.fromkeys(
                              (result.get("scraper") or "").split("+")
                              + (retry.get("scraper") or "").split("+"))),
                          "markdown": (result.get("markdown") or "")
                          + "\n\n" + (retry.get("markdown") or ""),
                          "all_results": merged_all,
                          "stats": {"successful": len(
                              [r for r in merged_all if r.get("success")])}}
                used += [r.get("scraper") for r in (retry.get("all_results") or [])
                         if r.get("success")]
                votes = vote_price_lines(merged_all)
                assess = sufficiency.assess_pricing([], vote_detail=votes)
                if assess["sufficient"]:
                    break
            # 全灭 → deep_link 搜索发现官方定价页
            if (not _merged_ok(result)
                    or not sufficiency.assess_page_content(kind, result.get("markdown") or "")):
                try:
                    from scripts import deep_link
                    alt = deep_link.locate_pricing_page(
                        urlparse(base).netloc.replace("www.", ""))
                    if alt and alt.get("url"):
                        _record(url, result, kind)  # 原失败留痕
                        url = alt["url"]
                        result = {"success": True, "scraper": "deep_link+search",
                                  "markdown": alt.get("markdown") or "",
                                  "all_results": [{"success": True,
                                                   "scraper": "deep_link",
                                                   "markdown": alt.get("markdown") or ""}],
                                  "stats": {"successful": 1}}
                except Exception:
                    pass
            pages[kind] = {"url": url,
                           "engines": [e for e in (used or []) if e],
                           "sufficient": bool(
                               _merged_ok(result)
                               and sufficiency.assess_page_content(
                                   kind, result.get("markdown") or "")
                               and sufficiency.assess_pricing(
                                   [], vote_detail=vote_price_lines(
                                       result.get("all_results") or []))["sufficient"]),
                           }
        else:
            pages[kind] = {"url": url,
                           "engines": used,
                           "sufficient": _merged_ok(result) and
                           sufficiency.assess_page_content(kind, result.get("markdown") or "")}
        _record(url, result, kind)

    # ── 落盘 ──
    (raw_dir / f"{cname}.engines.json").write_text(
        json.dumps(engines_doc, ensure_ascii=False, indent=1), encoding="utf-8")
    (raw_dir / f"{cname}.md").write_text(
        "\n\n---\n\n".join(raw_sections), encoding="utf-8")
    manifest_path = out_dir / "claims-manifest.json"
    manifest = {"run": {"topic": "",
                        "started_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                        "pipeline_version": "3.0"},
                "fetched": fetched, "claims": [], "failures": failures}
    if manifest_path.exists():
        try:
            old = json.loads(manifest_path.read_text(encoding="utf-8"))
            old.setdefault("fetched", {}).update(fetched)
            old.setdefault("failures", []).extend(failures)
            manifest = old
        except Exception:
            pass
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1),
                             encoding="utf-8")
    return {"name": resolved.get("name") or name, "url": base,
            "pages": pages, "failures": failures}


def main() -> int:
    ap = argparse.ArgumentParser(description="youzi V2 取证采集器(无语义提取)")
    ap.add_argument("--competitors", required=True,
                    help="逗号分隔的竞品名或域名,如 wati,respond.io")
    ap.add_argument("--out-dir", required=True, help="输出目录(OUT_DIR)")
    ap.add_argument("--budget", type=float, default=None,
                    help="每竞品墙钟预算秒数(默认 300)")
    args = ap.parse_args()
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    ok = True
    for n in [x.strip() for x in args.competitors.split(",") if x.strip()]:
        t0 = time.time()
        r = fetch_competitor(n, out_dir, budget_s=args.budget)
        insuff = [k for k, v in r["pages"].items() if not v["sufficient"]]
        print(f"[{'✓' if r['pages'] else '✗'} {r['name']}] {time.time()-t0:5.1f}s | "
              f"{len(r['pages'])} pages | 不充分: {','.join(insuff) or '无'} | "
              f"failures: {len(r['failures'])}")
        if not r["pages"]:
            ok = False
    print(f"\n台账: {out_dir / 'claims-manifest.json'}")
    print(f"原文: {out_dir / '02-raw'}/")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `python3 -m pytest tests/test_fetch.py -x -q`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add scripts/fetch.py tests/test_fetch.py && git commit -m "feat: fetch.py 取证层采集器 — 多页采集+定价交叉验证升级梯+台账"
```

---

### Task 4: 删除提取单体 + 切换调用方

**Files:**
- Delete: `scripts/crawl_competitors.py`, `scripts/crawl_summarize.py`, `scripts/build_whatsapp_demo.py`
- Modify: `tests/test_e2e_real.py:42-46`（subprocess 调 fetch.py）
- Test: 现有 `tests/test_e2e_offline.py`、`tests/test_verify.py` 必须仍绿

**Interfaces:**
- Consumes: Task 3 的 fetch.py CLI
- Produces: 仓库内无任何模块 import crawl_competitors/crawl_summarize

- [ ] **Step 1: 改 test_e2e_real.py 的 subprocess 调用**

```python
        r = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "fetch.py"),
             "--competitors", "wati,respond.io,ycloud",
             "--out-dir", str(E2E_DIR)],
            cwd=ROOT, capture_output=True, text=True, timeout=1800,
        )
```

（e2e_real 是网络测试，本任务不跑它，只保证引用正确。）

- [ ] **Step 2: 删除单体**

```bash
git rm scripts/crawl_competitors.py scripts/crawl_summarize.py scripts/build_whatsapp_demo.py
```

- [ ] **Step 3: grep 确认无活引用**

```bash
grep -rn "crawl_competitors\|crawl_summarize\|build_whatsapp_demo" \
  --include="*.py" scripts/ adapters/ tests/ render.py verify.py gates.py network_gates.py
```
Expected: 仅 tests/test_accuracy_loop.py、tests/test_pricing_extract.py、tests/test_pipeline.py 命中（Task 6 处理）；其余零命中。

- [ ] **Step 4: 跑离线测试确认不回归**

Run: `python3 -m pytest tests/test_e2e_offline.py tests/test_verify.py -x -q`
Expected: PASS（这两个文件不依赖被删模块）

- [ ] **Step 5: 提交**

```bash
git add -A && git commit -m "refactor: 删除165K提取单体+summarize+demo脚本 — 语义提取回归LLM"
```

---

### Task 5: 报告定价区重设计 + 证据链 + 视觉刷新

**Files:**
- Modify: `templates/report.html:1373-1530`（定价 CSS）、`templates/report.html:1929-1998`（4.1 定价卡片 Jinja 块）
- Test: `tests/test_report_v2.py`（新建）

**Interfaces:**
- Consumes: `commercial_strategies[name].plans = [{name, monthly, annual, monthly_billed, other_note, save_pct, annual_monthly_equiv, is_free, is_custom, custom_note}]`（render.py `_synthesize_plans_from_tiers` 产出，字段不变）
- Produces: 月付-only 数据 → 无年付列；Custom 档独立块；证据链 `<details>` 折叠

- [ ] **Step 1: 写失败测试**

```python
# tests/test_report_v2.py
# -*- coding: utf-8 -*-
"""V2 报告:定价区优雅降级(无空年付列)+ Custom 语义块。"""
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
            [sys.executable, str(ROOT / "render.py"),
             "--input", str(src), "--output", str(out)],
            capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stderr[-2000:]
        return out.read_text(encoding="utf-8")


def _minimal_comp(name, tiers, currency="USD"):
    return {
        "name": name, "url": f"https://{name.lower()}.io",
        "tagline": "t", "pricing": "$39/月起",
        "pricing_currency": currency,
        "pricing_tiers": tiers,
        "core_features": ["f1", "f2", "f3"],
        "strengths": [], "weaknesses": [], "differentiators": [],
        "tech_signals": [], "scores": {"feature_richness": 5, "ux": 5,
                                        "pricing_value": 5, "integration": 5,
                                        "ai_capability": 5, "momentum": 5},
    }


def _analysis(comps):
    return {"topic": "T", "competitors": comps,
            "market_segments": [], "gaps": [], "opportunities": [],
            "executive_summary": "s"}


def test_monthly_only_no_annual_column():
    tiers = [{"name": "Growth", "price": "$39", "billing_period": "/mo",
              "source_url": "https://x.io/pricing"},
             {"name": "Pro", "price": "$89", "billing_period": "/mo",
              "source_url": "https://x.io/pricing"}]
    html = _render(_analysis([_minimal_comp("Alpha", tiers)]))
    # 头行只有 套餐|月付 两列语义:pp-head 内不出现"年付"
    import re
    head = re.search(r'class="pp-row pp-head[^"]*">(.*?)</div>', html, re.S)
    assert head, "pp-head 必须存在"
    assert "年付" not in head.group(1), "无年付数据时不得渲染年付列"


def test_monthly_annual_shows_both_and_save():
    tiers = [{"name": "Pro", "price": "$39", "billing_period": "/mo",
              "source_url": "https://x.io/pricing"},
             {"name": "Pro", "price": "$390", "billing_period": "/yr",
              "source_url": "https://x.io/pricing"}]
    html = _render(_analysis([_minimal_comp("Beta", tiers)]))
    assert "年付" in html and "省 1" in html  # (1-390/468)*100 ≈ 17%


def test_custom_tier_semantic_block():
    tiers = [{"name": "Enterprise", "price": "Custom", "billing_period": "",
              "source_url": "https://x.io/pricing"}]
    html = _render(_analysis([_minimal_comp("Gamma", tiers)]))
    assert "pc-custom" in html and "联系销售" in html
    # custom 不出现在月/年数据行里
    assert 'class="pp-row"' not in html or "Custom" not in html.split("pc-custom")[0].split("pp-head")[-1]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `python3 -m pytest tests/test_report_v2.py -x -q`
Expected: FAIL（现模板 pp-head 恒渲染「年付」；custom 走 pp-span 行）

- [ ] **Step 3: 重写模板定价卡片块（`templates/report.html:1932-1997` 区域）**

```html
      {% for c in competitors %}
      {% set pcs = commercial_strategies[c.name] %}
      {% set priced = pcs.plans|rejectattr('is_custom')|list if pcs.plans else [] %}
      {% set show_annual = (priced|selectattr('annual')|list|length > 0) or (priced|selectattr('monthly_billed')|list|length > 0) %}
      <div class="pricing-card{% if not show_annual %} mo-only{% endif %}">
        <div class="pc-head">
          <strong>{{c.name}}</strong>
          <span class="pc-model">{{pcs.model|default('—')}}</span>
          <span class="pc-currency" title="币种以官网公示价为准">{{c.pricing_currency|default('USD')}}{% if c.pricing_unit %} · {{c.pricing_unit}}{% endif %}</span>
        </div>
        {% if pcs.plans %}
        <div class="pc-plans">
          <div class="pp-row pp-head"><span>套餐</span><span>月付</span>{% if show_annual %}<span>年付</span>{% endif %}</div>
          {% for p in pcs.plans %}
          {% if not p.is_custom %}
          <div class="pp-row">
            <span class="pp-name">{{p.name}}{% if p.is_free %}<em class="pp-tag free">免费</em>{% endif %}</span>
            {% if p.is_free %}
            <span class="pp-mo">$0</span>
            {% if show_annual %}<span class="pp-yr"><span class="pp-empty">—</span></span>{% endif %}
            {% else %}
            <span class="pp-mo">{% if p.monthly %}{{p.monthly}}{% elif p.monthly_billed and not show_annual %}{{p.monthly_billed}}<i class="pp-equiv">年付结算月价</i>{% else %}<span class="pp-empty">—</span>{% endif %}</span>
            {% if show_annual %}
            <span class="pp-yr">
              {%- if p.annual %}{{p.annual}}{% if p.save_pct %}<em class="pp-save">省 {{p.save_pct}}%</em>{% endif %}{% if p.annual_monthly_equiv %}<i class="pp-equiv">≈ {{p.annual_monthly_equiv}}/月</i>{% endif %}
              {%- elif p.monthly_billed %}{{p.monthly_billed}}<i class="pp-equiv">年付结算月价</i>
              {%- elif p.other_note %}<span class="pp-empty">{{p.other_note}}</span>
              {%- else %}<span class="pp-empty">—</span>{% endif %}
            </span>
            {% endif %}
            {% endif %}
          </div>
          {% endif %}
          {% endfor %}
        </div>
        {% for p in pcs.plans if p.is_custom %}
        <div class="pc-custom">
          <span class="pp-name">{{p.name}}<em class="pp-tag custom">定制</em></span>
          <span class="pp-custom-note">{{p.custom_note or '联系销售报价'}}</span>
        </div>
        {% endfor %}
        {% elif pcs.pricing_tiers %}
        <table style="width:100%; border-collapse:collapse; font-size:0.82rem;">
          <tbody>
            {% for t in pcs.pricing_tiers %}
            <tr style="border-bottom:1px solid var(--line);">
              <td style="padding:0.4rem 0.2rem; font-weight:600; white-space:nowrap;">{{t.name}}</td>
              <td style="padding:0.4rem 0.2rem; text-align:right; font-family:var(--font-mono); font-weight:700; white-space:nowrap;">{{t.price}}</td>
              <td style="padding:0.4rem 0.2rem; color:var(--fg-mute); font-size:0.75rem; white-space:nowrap;">{{t.billing_period}}</td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
        {% else %}
        <div style="color:var(--fg-mute); font-size:0.78rem; padding:0.5rem 0;">— 公开材料未提供具体价格</div>
        {% endif %}
        <div class="pc-foot">
          <details class="ev-chain">
            <summary>
              {% if c._refs.pricing %}<a href="#src-{{c._refs.pricing}}" class="ref" onclick="event.preventDefault()">来源 [{{c._refs.pricing}}]</a>{% else %}来源{% endif %}
              {% if c.pricing_verified %}
              <span class="pc-verified">✓ 已验证{% if c.pricing_engines %} ({{c.pricing_engines|length}} 引擎){% endif %}</span>
              {% elif c.pricing and c.pricing != '—' %}
              <span class="pc-unverified">⚠ 未验证 · 单引擎</span>
              {% endif %}
            </summary>
            <dl class="ev-detail">
              <dt>定价页</dt><dd>{% if pcs.pricing_source %}<a href="{{pcs.pricing_source}}" target="_blank" rel="noopener">{{pcs.pricing_source}}</a>{% elif c._pricing_url %}{{c._pricing_url}}{% else %}{{c.url}}/pricing{% endif %}</dd>
              <dt>抓取时间</dt><dd>{{c.pricing_scraped_at|default('—')}}</dd>
              <dt>交叉验证引擎</dt><dd>{{c.pricing_engines|join(' + ') if c.pricing_engines else '—'}}</dd>
            </dl>
          </details>
          {% if c.pricing_addon_note %}<div class="pc-note">ℹ {{c.pricing_addon_note}}</div>{% endif %}
          {% if c.pricing_crawl_note %}<div class="pc-note">⚠ {{c.pricing_crawl_note}}</div>{% endif %}
          {% if c.pricing_billing_note %}<div class="pc-note">ℹ {{c.pricing_billing_note}}</div>{% endif %}
        </div>
      </div>
      {% endfor %}
```

- [ ] **Step 4: 更新定价 CSS（`.pricing-card` 区段）**

在 `templates/report.html` 的 `.pricing-card` CSS 区追加/替换：

```css
  /* V2:月付-only 降级 —— 无年付数据时收缩为两列,绝不留半空列 */
  .pricing-card.mo-only .pp-row {
    grid-template-columns: minmax(4.5rem, 1fr) 1fr;
  }
  .pricing-card.mo-only .pp-row.pp-head span:nth-child(2) { text-align: right; }
  /* Custom 档独立语义块(不占月/年格子) */
  .pricing-card .pc-custom {
    display: flex; align-items: center; justify-content: space-between;
    gap: 0.5rem; padding: 0.45rem 0.35rem;
    border-top: 1px dashed var(--line);
    background: var(--bg-soft);
    border-radius: 0 var(--radius-sm) var(--radius-sm) 0;
    font-size: 0.8rem;
  }
  .pricing-card .pc-custom .pp-custom-note {
    color: var(--fg-mute); font-size: 0.72rem; text-align: right;
  }
  /* 证据链折叠 */
  .pricing-card .ev-chain summary {
    list-style: none; cursor: pointer; display: flex; align-items: center;
    gap: 0.5rem; font-size: 0.72rem; color: var(--fg-mute);
  }
  .pricing-card .ev-chain summary::-webkit-details-marker { display: none; }
  .pricing-card .ev-chain summary::after {
    content: "▸"; font-size: 0.6rem; transition: transform 0.15s;
  }
  .pricing-card .ev-chain[open] summary::after { transform: rotate(90deg); }
  .pricing-card .ev-detail {
    margin: 0.4rem 0 0; padding: 0.5rem 0.6rem;
    background: var(--bg-soft); border-radius: var(--radius-sm);
    font-size: 0.7rem; display: grid; grid-template-columns: auto 1fr; gap: 0.2rem 0.6rem;
  }
  .pricing-card .ev-detail dt { color: var(--fg-mute); font-weight: 600; }
  .pricing-card .ev-detail dd { margin: 0; word-break: break-all; }
  .pricing-card .ev-detail a { color: var(--accent); }
```

（同时删除旧 `.pp-row.pp-span` / `.pp-customline` CSS —— 新块不再使用。）

- [ ] **Step 5: 视觉刷新 —— `:root` token 升级**

模板 `<style>` 开头的 `:root` 变量区替换为（保持变量名不变，只调值，深浅双主题同步微调 `@media (prefers-color-scheme: dark)` / `[data-theme=dark]` 对应块）：

```css
  :root {
    --accent: #4f6ef7;
    --accent-soft: rgba(79, 110, 247, 0.10);
    --good: #0e9f6e; --good-soft: rgba(14, 159, 110, 0.12);
    --radius-sm: 6px; --radius-md: 12px; --radius-lg: 18px;
    --card-shadow: 0 1px 2px rgba(16, 24, 40, 0.04),
                   0 8px 24px -12px rgba(16, 24, 40, 0.16);
  }
```

并将 `.pricing-card`/竞品卡片的 `box-shadow` 统一引用 `var(--card-shadow)`、圆角引用 `var(--radius-*)`（sed 逐个替换，不改布局）。

- [ ] **Step 6: 跑新测试 + 离线 e2e**

```bash
python3 -m pytest tests/test_report_v2.py tests/test_e2e_offline.py -x -q
```
Expected: 全 PASS（e2e_offline 用冻结 fixture 钉住旧数据兼容 —— 有年付的显示双列，月付-only 的新逻辑不炸）

- [ ] **Step 7: 提交**

```bash
git add templates/report.html tests/test_report_v2.py && git commit -m "feat: 定价区V2 — 无年付数据优雅降级+Custom语义块+证据链折叠+token刷新"
```

---

### Task 6: 测试收敛 — 删提取通道用例

**Files:**
- Delete: `tests/test_pipeline.py`（28 个用例全部依赖被删提取函数）、`tests/test_pricing_extract.py`（import crawl_competitors）、`tests/test_accuracy_loop.py`（同）
- Test: 全量 pytest 绿

**Interfaces:**
- Produces: `python3 -m pytest tests/ -q` 全绿且零 collection error

- [ ] **Step 1: 确认三个文件的依赖面**

```bash
grep -c "crawl_competitors" tests/test_pipeline.py tests/test_pricing_extract.py tests/test_accuracy_loop.py
```
Expected: 24 / 1 / 1（全部依赖被删模块）

- [ ] **Step 2: 删除**

```bash
git rm tests/test_pipeline.py tests/test_pricing_extract.py tests/test_accuracy_loop.py
```

- [ ] **Step 3: 全量跑**

Run: `python3 -m pytest tests/ -q`
Expected: test_adapters_v2 / test_fetch / test_report_v2 / test_verify / test_e2e_offline 全 PASS（test_e2e_real 若因网络标记 skip/忽略则不动它）

- [ ] **Step 4: 提交**

```bash
git add -A && git commit -m "test: 删除提取通道用例 — V2管线测试收敛到取证层+门禁+报告"
```

---

### Task 7: 文档更新 — SKILL.md / 使用手册 / crawl-strategy

**Files:**
- Modify: `SKILL.md`
- Modify: `references/crawl-strategy.md`
- Modify: `使用手册.md`
- Modify: `README.md`（如提及引擎数）

**Interfaces:**
- Produces: 文档描述与代码一致（5 引擎、fetch.py 用法、三层职责）

- [ ] **Step 1: SKILL.md 修改**

1. `allowed-tools` 行去掉已删引擎名（crawl4ai 等），保留 firecrawl/playwright MCP 引用（MCP 工具与本地 adapter 无关，保留）。
2. 「2️⃣ 网页爬取」整节替换为：

```markdown
**2️⃣ 网页爬取（V2 白名单 4+1 引擎，按信息类型自动路由）**

本地引擎仅保留实测能打的（engine-stats n=700+ 校准）：

- **playwright** — JS 页王者（定价/首页/功能页主力；唯一能登录交互）
- **trafilatura** — 静态正文王者（docs/about/changelog 主力）
- **newspaper3k** — 文章型（blog/customer/testimonials 主力）
- **jina** — 第三方渲染，交叉验证第二票（免 key）
- **firecrawl** — 商业最强；检测到 `FIRECRAWL_API_KEY` 时自动插入优先位，无 key 不尝试

⭐ **统一入口**：`python3 scripts/fetch.py --competitors "wati,respond.io" --out-dir OUT`
- 按 URL 类型自动路由引擎组合；定价页 JS 组 + 静态对照双通道
- 定价 ≥2 独立引擎看到相同价格才 sufficient；不达标沿升级梯自动换引擎重爬
- 全灭时搜索发现官方定价页兜底；预算(300s/竞品)耗尽诚实标「未验证」
- 台账 `claims-manifest.json` + 每引擎原文 `02-raw/*.engines.json`（verify.py 消费）
- **fetch.py 只取证不做语义提取** —— 套餐/功能/定位的提取全部是你的工作（Step 3）
```

3. Step 2 段落改为调用 fetch.py 一次完成（不再逐 URL 手动 scrape_smart）。
4. 「反模式」追加一条：`❌ 重新引入脚本侧语义提取（正则套餐/功能/翻译对齐）—— 165KB 单体的历史教训，提取是 LLM 的活`。

- [ ] **Step 2: crawl-strategy.md 重写引擎表**（13 行 → 5 行白名单 + 路由表引用 `adapters._URL_TYPE_SCRAPERS`，删 crawl4ai/camoufox 等宣传段）。

- [ ] **Step 3: 使用手册.md / README.md** 同步引擎描述与 fetch.py 用法（grep `13 个爬虫|crawl4ai|camoufox|crawlee|scrapy` 找到全部提及处逐一改）。

- [ ] **Step 4: 验证文档一致性**

```bash
grep -rn "13 个爬虫\|13 引擎\|crawl4ai\|camoufox\|crawlee\|scrapy\|requests_html\|html2text\|markdownify\|readability" \
  SKILL.md README.md 使用手册.md 安装说明.md references/
```
Expected: 零命中（历史教训引用除外，如「删掉了 crawl4ai」这类表述允许）。

- [ ] **Step 5: 提交**

```bash
git add -A && git commit -m "docs: V2三层职责+5引擎白名单+fetch.py用法 — 文档与代码对齐"
```

---

### Task 8: 双 topic 实爬验收（网络）

**Files:**
- 无代码修改；产出 `~/youzi-out/whatsapp-bsp-v2-验收/` 与 `~/youzi-out/<新topic>-v2-验收/`

**Interfaces:**
- Consumes: 全部前序任务产物
- Produces: 两份 report.html + verify-report.json 全绿证据

- [ ] **Step 1: 回归 topic 实爬**

```bash
python3 scripts/fetch.py \
  --competitors "wati,respond.io,ycloud,sleekflow.io,unifonic" \
  --out-dir ~/youzi-out/whatsapp-bsp-v2-验收
```
Expected: 每竞品输出 pages 清单；pricing 页 sufficient=true 或诚实 insufficient。

- [ ] **Step 2: 与第四轮历史比对**

对照 `~/youzi-out/WhatsApp-BSP-五家第四轮-2026-08-27/03-analysis.json`：
- YCloud Growth $39 / Pro $89 / Enterprise $399 是否复现
- Sleekflow Pro AI US$149 / Premium AI US$349 是否复现
- pricing_verified=true 家数 ≥ 第四轮

- [ ] **Step 3: LLM Step 3 提取**（会话内执行，非脚本）

按 SKILL.md Step 3 规则从 02-raw 提取 13 字段 + 证据三元组，写 `03-analysis.json` 与 `claims-manifest.json` 的 claims 数组。tech_signals 必须锚定 docs 具体子页（`sufficiency.assess_tech_signals` 校验）。

- [ ] **Step 4: 渲染 + 双门禁**

```bash
python3 render.py --input OUT/03-analysis.json --output OUT/report.html
python3 verify.py --analysis OUT/03-analysis.json --manifest OUT/claims-manifest.json \
  --raw-dir OUT/02-raw --json OUT/verify-report.json
```
Expected: render exit 0（自检过）+ verify exit 0（G1-G6 绿）。

- [ ] **Step 5: 新 topic 复跑 Step 1-4**（任选未分析过的赛道，如 `AI 代码审查工具`，5 家）。

- [ ] **Step 6: 报告人工过目**

`open OUT/report.html`：定价区无半空年付列、Custom 档独立块、证据链可展开；雷达图/热力图正常。

- [ ] **Step 7: 冻结新 fixture + 提交**

把验收轮的 03-analysis.json + claims-manifest.json + 02-raw 摘要复制进 `tests/fixtures/v2-acceptance/`，供 test_e2e_offline.py 回放（如格式与现 fixture 加载器不同，最小适配加载路径即可，不改断言语义）。

```bash
git add -A && git commit -m "test: V2双topic实爬验收fixture冻结 — G1-G6全绿"
```

---

## Self-Review 结论

- **Spec 覆盖**：引擎白名单(T1/T2)、firecrawl key 门控(T1)、fetch.py 四职责+台账(T3)、删除清单(T4)、定价优雅降级+证据链+视觉刷新(T5)、测试收敛(T6)、文档对齐(T7)、双 topic 验收(T8)——spec §5-§10 全部落位。
- **占位符扫描**：无 TBD/TODO；所有代码步骤含完整代码。
- **类型一致性**：`fetch_competitor(name, out_dir, budget_s)`、`vote_price_lines(all_results)`、`ladder_engines(url_type, already_used)`、manifest `{run, fetched, claims, failures}` 在 T2/T3/T4/T8 间签名一致；模板消费的 `pcs.plans` 字段与 render.py `_synthesize_plans_from_tiers` 现有产出完全一致。
