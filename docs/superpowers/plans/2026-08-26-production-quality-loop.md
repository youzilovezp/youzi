# youzi 生产级证据验证闭环 — 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为 youzi 竞品情报管线建立「证据包落盘 → 分层验证（离线硬门禁 + opt-in 网络复核）→ 修复回路」的生产级质量闭环，并修复爬取侧 8 个已定位的数据质量根因。

**Architecture:** 爬取管线（crawl_competitors.py / run_youzi.py）在产出 analysis.json 的同时落盘机器可读证据包（claims-manifest.json + 02-raw/*.engines.json）；新增独立验证器 verify.py 对证据包跑 G1-G6 离线硬门禁（<1s，进 pytest）和 N1-N2 网络门禁（--network opt-in）；交付条件 = render.py exit 0 且 verify.py exit 0。规格见 `docs/superpowers/specs/2026-08-26-production-quality-loop-design.md`。

**Tech Stack:** Python 3.13，stdlib only（json/re/hashlib/urllib/asyncio/unittest），无新增第三方依赖。测试沿用现有 unittest.TestCase 风格 + pytest 运行器。

## Global Constraints

- 不新增任何第三方依赖（网络层用 urllib.request，不用 requests）。
- 现有 76 个测试必须保持全绿（每个任务收尾跑 `python3 -m pytest tests/ -q`）。
- verify.py 退出码约定：0=通过，1=输入缺失/损坏，2=硬门禁失败（与 render.py 一致）。
- TTL = 14 天（verify.py 的 `TTL_DAYS` 与 crawl_competitors 的 `_PRICING_CACHE_TTL_DAYS` 必须同值）。
- 反伪造原则（SKILL.md 最高优先级）：任何修复不得引入硬编码数据兜底；抓不到就标「未验证/—」。
- 测试文件风格：unittest.TestCase，文件头 `ROOT = Path(__file__).resolve().parent.parent; sys.path.insert(0, str(ROOT))`，中文 docstring。
- 引擎原文截断上限：engines.json 每引擎 50000 字符（与 scrape_smart max_chars 一致）。
- 注释风格：代码内中文注释说明「为什么」，历史事故用「(真实事故: …)」格式（沿用现有惯例）。
- 提交信息用中文 `feat:/fix:/docs:/test:` 前缀（沿用现有 git log 惯例）。

---

### Task 1: verify.py 骨架 — CLI + 加载器 + 报告 + 退出码

**Files:**
- Create: `verify.py`（项目根，与 render.py 同级）
- Test: `tests/test_verify.py`

**Interfaces:**
- Consumes: 无（首个任务）
- Produces（后续任务依赖，签名不得变动）:
  - `norm_ws(s: str) -> str` — 空白归一化
  - `class Report` — `.hard(gate, field, source_url, detail, hint)` / `.warn(gate, field, detail)` / `.ok -> bool` / `.to_dict(passed_exit_code) -> dict`
  - `load_manifest(path: Path) -> dict`（损坏 JSON → raise SystemExit(1)）
  - `load_analysis(path: Path) -> dict`（同上）
  - `build_engine_index(raw_dir: Path) -> dict[str, dict[str, str]]` — 合并 `02-raw/*.engines.json` 为 `{url: {engine: markdown}}`
  - `verify_analysis(analysis_path, manifest_path, raw_dir, network=False, sample=None, report_path=None) -> dict` — 主 API，e2e 测试直接调用
  - `main() -> int` — CLI 入口

- [ ] **Step 1: 写失败测试**

创建 `tests/test_verify.py`：

```python
#!/usr/bin/env python3
"""tests/test_verify.py · verify.py 证据验证器测试。"""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from verify import (  # noqa: E402
    Report, build_engine_index, load_analysis, load_manifest, norm_ws,
    verify_analysis,
)


def _write(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


MINI_MANIFEST = {
    "run": {"topic": "t", "started_at": "2026-08-26 00:00 UTC"},
    "fetched": {},
    "claims": [],
    "failures": [],
}

MINI_ANALYSIS = {
    "topic": "t",
    "executive_summary": "x",
    "competitors": [],
}


class TestSkeleton(unittest.TestCase):
    """骨架:加载 / 归一化 / 引擎索引 / 退出码。"""

    def test_norm_ws(self):
        self.assertEqual(norm_ws("  a\tb\n\nc  "), "a b c")
        self.assertEqual(norm_ws(None), "")

    def test_load_manifest_ok(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = _write(Path(d) / "m.json", MINI_MANIFEST)
            self.assertEqual(load_manifest(p)["run"]["topic"], "t")

    def test_load_manifest_corrupt_exit1(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "m.json"
            p.write_text("{broken", encoding="utf-8")
            with self.assertRaises(SystemExit) as cm:
                load_manifest(p)
            self.assertEqual(cm.exception.code, 1)

    def test_missing_input_exit1(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit) as cm:
                verify_analysis(
                    Path(d) / "nope.json", Path(d) / "m.json", Path(d), 
                )
            self.assertEqual(cm.exception.code, 1)

    def test_empty_bundle_passes(self):
        """空证据包 + 空分析 = 三无报告,零 violation 通过。"""
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            m = _write(Path(d) / "m.json", MINI_MANIFEST)
            a = _write(Path(d) / "a.json", MINI_ANALYSIS)
            rep = verify_analysis(a, m, Path(d))
            self.assertTrue(rep["passed"])
            self.assertEqual(rep["exit_code"], 0)

    def test_report_collect(self):
        r = Report()
        r.hard("G1", "competitors[0].pricing", "https://x", "detail", "hint")
        r.warn("G6", "competitors[0].tagline", "detail2")
        self.assertFalse(r.ok)
        d = r.to_dict(2)
        self.assertEqual(d["summary"]["hard_failed"], 1)
        self.assertEqual(d["summary"]["warnings"], 1)
        self.assertEqual(d["violations"][0]["gate"], "G1")
        self.assertEqual(d["violations"][0]["hint"], "hint")

    def test_engine_index_merges_files(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            raw = Path(d) / "02-raw"
            _write(raw / "wati.engines.json", {
                "https://wati.io/pricing": {"playwright": "pw md", "trafilatura": "tr md"},
            })
            _write(raw / "respond.engines.json", {
                "https://respond.io/pricing": {"firecrawl": "fc md"},
                "https://wati.io/pricing": {"firecrawl": "fc2 md"},  # 跨文件同 URL 合并
            })
            idx = build_engine_index(raw)
            self.assertEqual(idx["https://wati.io/pricing"]["playwright"], "pw md")
            self.assertEqual(idx["https://wati.io/pricing"]["firecrawl"], "fc2 md")
            self.assertEqual(idx["https://respond.io/pricing"]["firecrawl"], "fc md")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_verify.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'verify'`

- [ ] **Step 3: 实现 verify.py 骨架**

创建 `verify.py`：

```python
#!/usr/bin/env python3
"""youzi · 证据验证器(生产级硬门禁)。

对爬取管线落盘的证据包(claims-manifest.json + 02-raw/*.engines.json)
与结论(03-analysis.json)做分层验证:

  Layer 1 离线硬门禁(必跑, <1s):
    G1 来源可回溯   每条 claim 的 source_url ∈ 本轮成功抓取集合
    G2 quote 回查   quote 在 source_url 对应引擎原文中逐字(归一化)命中
    G3 定价完整性   verified ⇒ ≥2 内容独立的引擎 + 时间戳新鲜 + tiers 非空
    G4 缺失诚实     失败页有记录;缺失字段不断言来源
    G5 反伪造       历史黑名单引文 / Python repr 泄漏 / 占位符
    G6 URL 卫生     URL 格式合法;指向其他竞品域 = 警告(警告级)
  Layer 2 网络门禁(--network opt-in):
    N1 可达性       被 claim 引用的 URL 回访非 2xx = 硬失败
    N2 quote 复核   轻量重抓后 quote 未命中 = 警告(页面会漂移,权威在 G2)

用法:
    python3 verify.py --analysis OUT/03-analysis.json \\
                      --manifest OUT/claims-manifest.json \\
                      --raw-dir OUT/02-raw [--network] [--sample 10] \\
                      [--json OUT/verify-report.json]

退出码: 0=通过  1=输入缺失/损坏  2=硬门禁失败
"""

import argparse
import json
import re
import sys
from pathlib import Path

TTL_DAYS = 14  # 与 scripts/crawl_competitors.py::_PRICING_CACHE_TTL_DAYS 保持同值


def norm_ws(s: str) -> str:
    """空白归一化:quote 回查时 markdown 折行/多空格不影响逐字命中。"""
    return re.sub(r"\s+", " ", (s or "")).strip()


class Report:
    """收集 violation(硬)/warning(软),输出机器可读报告。"""

    def __init__(self):
        self.violations = []
        self.warnings = []
        self.counters = {"claims_checked": 0, "urls_checked": 0}

    def hard(self, gate: str, field: str, source_url: str, detail: str, hint: str):
        self.violations.append(
            {"gate": gate, "severity": "hard", "field": field,
             "source_url": source_url, "detail": detail, "hint": hint}
        )

    def warn(self, gate: str, field: str, detail: str):
        self.warnings.append(
            {"gate": gate, "severity": "warning", "field": field, "detail": detail}
        )

    @property
    def ok(self) -> bool:
        return not self.violations

    def to_dict(self, exit_code: int) -> dict:
        return {
            "passed": self.ok,
            "exit_code": exit_code,
            "summary": {
                "hard_failed": len(self.violations),
                "warnings": len(self.warnings),
                **self.counters,
            },
            "violations": self.violations,
            "warnings": self.warnings,
        }


def _load_json_or_exit(path: Path, kind: str) -> dict:
    if not path.exists():
        print(f"✗ {kind} 不存在: {path}", file=sys.stderr)
        raise SystemExit(1)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"✗ {kind} 损坏: {path} ({e})", file=sys.stderr)
        raise SystemExit(1)


def load_manifest(path: Path) -> dict:
    return _load_json_or_exit(path, "claims-manifest")


def load_analysis(path: Path) -> dict:
    return _load_json_or_exit(path, "analysis")


def build_engine_index(raw_dir: Path) -> dict:
    """合并 02-raw/*.engines.json → {url: {engine: markdown}}(quote 回查依据)。"""
    index: dict = {}
    if not raw_dir.exists():
        return index
    for f in sorted(raw_dir.glob("*.engines.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue  # 单文件损坏不拖垮整体;缺失内容会体现在 G2 未命中
        for url, engines in (data or {}).items():
            index.setdefault(url, {}).update(engines or {})
    return index


def verify_analysis(analysis_path, manifest_path, raw_dir,
                    network=False, sample=None, report_path=None) -> dict:
    """主 API:e2e 测试直接调用。返回 Report.to_dict() 结果。"""
    analysis = load_analysis(Path(analysis_path))
    manifest = load_manifest(Path(manifest_path))
    engine_index = build_engine_index(Path(raw_dir))
    rep = Report()

    # Layer 1 离线门禁(Task 2-4 填充)
    from verify import gates  # 延迟 import,门禁函数在各自任务中注册
    gates.run_all(analysis, manifest, engine_index, rep)

    # Layer 2 网络门禁(Task 5 填充)
    if network:
        from verify import network_gates
        network_gates.run_all(analysis, manifest, rep, sample=sample)

    exit_code = 0 if rep.ok else 2
    result = rep.to_dict(exit_code)
    if report_path:
        rp = Path(report_path)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="youzi · 证据验证器(生产级硬门禁)")
    ap.add_argument("--analysis", required=True, help="03-analysis.json 路径")
    ap.add_argument("--manifest", required=True, help="claims-manifest.json 路径")
    ap.add_argument("--raw-dir", required=True, help="02-raw 目录(含 *.engines.json)")
    ap.add_argument("--network", action="store_true", help="启用网络门禁 N1/N2(慢)")
    ap.add_argument("--sample", type=int, default=None, help="网络层抽样 N 条 URL")
    ap.add_argument("--json", dest="report_path", default=None, help="验证报告 JSON 输出路径")
    args = ap.parse_args()

    result = verify_analysis(
        args.analysis, args.manifest, args.raw_dir,
        network=args.network, sample=args.sample, report_path=args.report_path,
    )
    for v in result["violations"]:
        print(f"  ✗ [{v['gate']}] {v['field']}: {v['detail']}")
        print(f"      → {v['hint']}")
    for w in result["warnings"]:
        print(f"  ⚠ [{w['gate']}] {w['field']}: {w['detail']}")
    s = result["summary"]
    verdict = "✓ 全部硬门禁通过" if result["passed"] else "✗ 硬门禁失败"
    print(
        f"\n{verdict} — 硬失败 {s['hard_failed']} · 警告 {s['warnings']} · "
        f"claims {s['claims_checked']} · urls {s['urls_checked']}"
    )
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
```

同时创建 `gates.py`（Task 2-4 逐门禁填充，先建注册表骨架）：

```python
#!/usr/bin/env python3
"""verify 的离线门禁集合(G1-G6)。每个 gate 函数签名:
    def gX(analysis: dict, manifest: dict, engine_index: dict, rep: Report) -> None
"""

from verify import Report

_GATES = []


def register(fn):
    _GATES.append(fn)
    return fn


def run_all(analysis, manifest, engine_index, rep: Report):
    for g in _GATES:
        g(analysis, manifest, engine_index, rep)
```

注意：`verify.py` 里 `from verify import gates` 在 e2e/pytest 直接 import verify 时依赖 `verify` 已在 sys.path（测试文件已插入 ROOT）——成立。

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_verify.py -q`
Expected: PASS（7 个测试）

- [ ] **Step 5: 全量回归 + 提交**

Run: `python3 -m pytest tests/ -q` → 83 passed

```bash
git add verify.py gates.py tests/test_verify.py
git commit -m "feat: verify.py 验证器骨架 — CLI/加载器/Report/退出码"
```

---

### Task 2: G1 来源可回溯 + G2 quote 回查

**Files:**
- Modify: `gates.py`
- Test: `tests/test_verify.py`（追加）

**Interfaces:**
- Consumes: Task 1 的 `Report` / `build_engine_index` / `norm_ws`
- Produces: `gates.py` 内 `@register def g1_source_traceability(...)` / `@register def g2_quote_grep(...)`；辅助 `iter_evidence_urls(competitor) -> list[(field, url)]`（G6/网络层复用，签名固定）

- [ ] **Step 1: 写失败测试**

在 `tests/test_verify.py` 追加（文件顶部 import 区已有需要的依赖）：

```python
def _bundle(tmp: Path, manifest: dict, analysis: dict, engines: dict = None):
    """构造最小证据包文件并跑验证。"""
    m = _write(tmp / "m.json", manifest)
    a = _write(tmp / "a.json", analysis)
    raw = tmp / "02-raw"
    if engines is not None:
        _write(raw / "x.engines.json", engines)
    return verify_analysis(a, m, raw)


def _ok_manifest(urls_ok=(), urls_failed=()):
    return {
        "run": {"topic": "t"},
        "fetched": {
            **{u: {"status": "ok", "engines": {}} for u in urls_ok},
            **{u: {"status": "failed", "engines": {}} for u in urls_failed},
        },
        "claims": [],
        "failures": [],
    }


def _one_comp_analysis(**fields):
    comp = {
        "name": "WATI", "url": "https://www.wati.io",
        "tagline": "x", "tagline_source": "",
        "founded": "—", "founded_source": "",
        "headquarters": "—", "headquarters_source": "",
        "team_size": "—", "team_size_source": "",
        "pricing": "—", "pricing_source": "", "pricing_verified": False,
        "pricing_tiers": [], "strengths": [], "weaknesses": [],
        "gtm_evidence": [], "moat_evidence": [], "tech_signals": [],
    }
    comp.update(fields)
    return {"topic": "t", "executive_summary": "x", "competitors": [comp]}


def _violations_by_gate(rep: dict, gate: str):
    return [v for v in rep["violations"] if v["gate"] == gate]


class TestG1SourceTraceability(unittest.TestCase):
    """G1: 分析引用的每个 source_url 必须在本轮成功抓取集合里。"""

    def test_url_not_fetched_is_hard_fail(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rep = _bundle(
                Path(d),
                _ok_manifest(urls_ok=["https://www.wati.io"]),
                _one_comp_analysis(pricing_source="https://www.wati.io/pricing"),
            )
            v = _violations_by_gate(rep, "G1")
            self.assertEqual(len(v), 1)
            self.assertIn("pricing_source", v[0]["field"])

    def test_url_fetch_failed_is_hard_fail(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rep = _bundle(
                Path(d),
                _ok_manifest(urls_failed=["https://www.wati.io/pricing"]),
                _one_comp_analysis(
                    pricing_tiers=[{
                        "name": "Growth", "price": "$59",
                        "billing_period": "/mo", "features": [],
                        "source_url": "https://www.wati.io/pricing",
                    }]
                ),
            )
            self.assertEqual(len(_violations_by_gate(rep, "G1")), 1)

    def test_fetched_ok_passes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rep = _bundle(
                Path(d),
                _ok_manifest(urls_ok=["https://www.wati.io/pricing"]),
                _one_comp_analysis(pricing_source="https://www.wati.io/pricing"),
            )
            self.assertTrue(rep["passed"])


class TestG2QuoteGrep(unittest.TestCase):
    """G2: quote 必须在 source_url 的引擎原文中归一化命中。"""

    def test_quote_hit_passes(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rep = _bundle(
                Path(d),
                _ok_manifest(urls_ok=["https://www.wati.io"]),
                _one_comp_analysis(strengths=[{
                    "point": "p", "evidence": '官网原文: "Trusted by 8000+ teams"',
                    "score": 0, "source": "https://www.wati.io",
                }]),
                engines={"https://www.wati.io": {
                    "playwright": "Some nav\nTrusted by 8000+  teams\nfooter",
                }},
            )
            self.assertTrue(rep["passed"])

    def test_quote_miss_is_hard_fail(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rep = _bundle(
                Path(d),
                _ok_manifest(urls_ok=["https://www.wati.io"]),
                _one_comp_analysis(gtm_evidence=[{
                    "name": "n", "quote": "not in page at all", "source": "https://www.wati.io",
                }]),
                engines={"https://www.wati.io": {"playwright": "completely different"}},
            )
            v = _violations_by_gate(rep, "G2")
            self.assertEqual(len(v), 1)
            self.assertIn("gtm_evidence", v[0]["field"])

    def test_no_engines_recorded_is_fail_not_crash(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            rep = _bundle(
                Path(d),
                _ok_manifest(urls_ok=["https://www.wati.io"]),
                _one_comp_analysis(moat_evidence=[{
                    "name": "n", "quote": "q", "source": "https://www.wati.io",
                }]),
                engines={},
            )
            self.assertEqual(len(_violations_by_gate(rep, "G2")), 1)
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_verify.py -q`
Expected: FAIL — G1/G2 测试失败（gates 为空，violation 数为 0 与断言不符）

- [ ] **Step 3: 实现 G1 + G2**

在 `gates.py` 追加：

```python
import re

from verify import norm_ws


# ── 证据 URL 收集:analysis 里所有「指向抓取原文」的字段 ──

def _evidence_fields(competitor: dict):
    """迭代 (field, url):竞品 entry 里所有携带来源 URL 的证据字段。"""
    name = competitor.get("name", "?")
    singles = (
        "pricing_source", "tagline_source", "founded_source",
        "headquarters_source", "team_size_source",
    )
    for k in singles:
        u = (competitor.get(k) or "").strip()
        if u:
            yield f"competitors[{name}].{k}", u
    for i, t in enumerate(competitor.get("pricing_tiers") or []):
        u = (t.get("source_url") or "").strip()
        if u:
            yield f"competitors[{name}].pricing_tiers[{i}].source_url", u
    for i, s in enumerate(competitor.get("strengths") or []):
        u = ((s.get("source") or "") if isinstance(s, dict) else "").strip()
        if u:
            yield f"competitors[{name}].strengths[{i}].source", u
    for key in ("gtm_evidence", "moat_evidence"):
        for i, ev in enumerate(competitor.get(key) or []):
            u = (ev.get("source") or "").strip() if isinstance(ev, dict) else ""
            if u:
                yield f"competitors[{name}].{key}[{i}].source", u
    for i, t in enumerate(competitor.get("tech_signals") or []):
        if isinstance(t, dict):
            u = (t.get("source") or "").strip()
        else:  # 兼容 "name|url" / 纯文本形态
            u = ""
            m = re.search(r"(https?://\S+)", str(t))
            u = m.group(1).rstrip(").,]") if m else ""
        if u:
            yield f"competitors[{name}].tech_signals[{i}].source", u
    # feature_catalog:仅检查非空 source(空 = 未定位出处,允许)
    fc = competitor.get("feature_catalog") or {}
    for cname, feats in fc.items():
        for i, f in enumerate(feats or []):
            u = (f.get("source") or "").strip() if isinstance(f, dict) else ""
            if u:
                yield f"competitors[{name}].feature_catalog[{cname}][{i}].source", u


def iter_evidence_urls(competitor: dict):
    """G6/网络层复用:该竞品全部去重证据 URL,保持字段顺序。"""
    seen, out = set(), []
    for field, u in _evidence_fields(competitor):
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


@register
def g1_source_traceability(analysis, manifest, engine_index, rep: Report):
    """G1: 每个被引用的 source_url 必须 ∈ manifest.fetched 且 status=ok。"""
    fetched = manifest.get("fetched") or {}
    for competitor in analysis.get("competitors") or []:
        for field, url in _evidence_fields(competitor):
            rep.counters["claims_checked"] += 1
            ent = fetched.get(url)
            if ent is None:
                rep.hard(
                    "G1", field, url,
                    f"source_url 不在本轮抓取记录中(未访问过的 URL 不得充当来源)",
                    "删除该字段,或重爬该 URL;绝不允许引用未抓取的地址",
                )
            elif ent.get("status") != "ok":
                rep.hard(
                    "G1", field, url,
                    f"source_url 本轮抓取失败(status={ent.get('status')})",
                    "失败页面的 URL 不能当来源;改为「未验证」并记录 failure",
                )


_STRENGTH_QUOTE_RX = re.compile(r'官网原文:\s*"(.+?)"\s*"?$')


def _quote_grep(quote: str, url: str, engine_index: dict) -> bool:
    """quote 在该 URL 任一引擎原文中归一化命中。"""
    q = norm_ws(quote)
    if not q:
        return True  # 空 quote 由 G4 管
    for md in (engine_index.get(url) or {}).values():
        if q in norm_ws(md):
            return True
    return False


@register
def g2_quote_grep(analysis, manifest, engine_index, rep: Report):
    """G2: quote 铁律(analysis-framework.md §1)的代码强制。"""
    for competitor in analysis.get("competitors") or []:
        name = competitor.get("name", "?")
        checks = []  # (field, url, quote)
        for i, s in enumerate(competitor.get("strengths") or []):
            if isinstance(s, dict):
                m = _STRENGTH_QUOTE_RX.search(s.get("evidence") or "")
                if m:
                    checks.append((
                        f"competitors[{name}].strengths[{i}].evidence",
                        (s.get("source") or "").strip(), m.group(1),
                    ))
        for key in ("gtm_evidence", "moat_evidence"):
            for i, ev in enumerate(competitor.get(key) or []):
                if isinstance(ev, dict) and ev.get("quote"):
                    checks.append((
                        f"competitors[{name}].{key}[{i}].quote",
                        (ev.get("source") or "").strip(), ev["quote"],
                    ))
        for i, v in enumerate(competitor.get("pricing_vote_detail") or []):
            if isinstance(v, dict) and v.get("line"):
                checks.append((
                    f"competitors[{name}].pricing_vote_detail[{i}].line",
                    (competitor.get("pricing_source") or "").strip(), v["line"],
                ))
        for field, url, quote in checks:
            if not url:
                continue  # 无来源的引文归 G1/G4
            if not _quote_grep(quote, url, engine_index):
                rep.hard(
                    "G2", field, url,
                    f"quote 未在该 URL 的任何引擎原文中命中: “{quote[:60]}…”",
                    "改写为引擎原文逐字引文(见 02-raw/*.engines.json),或重爬该 URL",
                )
```

同时把 `tests/test_verify.py` 顶部 import 行补上 `iter_evidence_urls`（Task 5/后续会用，现在导入防漂移）——不需要，YAGNI，用到再加。

注意 `_evidence_fields` 中 url 字段 `competitor.get("url")`（官网本身）**不进 G1**：官网可能被重定向后仍作为标识符；home 抓取状态由 manifest 记录，G4 检查。

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_verify.py -q`
Expected: PASS（13 个测试）

- [ ] **Step 5: 全量回归 + 提交**

Run: `python3 -m pytest tests/ -q` → 全绿

```bash
git add gates.py tests/test_verify.py
git commit -m "feat: G1 来源可回溯 + G2 quote 回查硬门禁"
```

---

### Task 3: G3 定价完整性（引擎独立性 + TTL + 结构）

**Files:**
- Modify: `gates.py`
- Test: `tests/test_verify.py`（追加）

**Interfaces:**
- Consumes: Task 1 `Report`；manifest.fetched[url].engines[engine].content_hash 结构（Task 8 爬虫侧产出；本任务只消费）
- Produces: `@register def g3_pricing_integrity(...)`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_verify.py`：

```python
def _pricing_comp(verified=True, engines=("playwright", "crawl4ai"),
                  hashes=None, scraped_at="2026-08-26 00:00 UTC",
                  source="https://www.wati.io/pricing", tiers=1):
    return _one_comp_analysis(
        pricing="Growth · $59 (/mo)", pricing_verified=verified,
        pricing_source=source, pricing_scraped_at=scraped_at,
        pricing_engines=list(engines),
        pricing_tiers=[{
            "name": "Growth", "price": "$59", "billing_period": "/mo",
            "features": [], "source_url": source,
        }] * tiers,
    )


def _manifest_with_hashes(url, engine_hashes: dict, status="ok"):
    return {
        "run": {}, "claims": [], "failures": [],
        "fetched": {url: {
            "status": status,
            "engines": {
                e: {"ok": True, "chars": 100, "content_hash": h}
                for e, h in engine_hashes.items()
            },
        }},
    }


class TestG3PricingIntegrity(unittest.TestCase):
    """G3: verified ⇒ ≥2 内容独立引擎 + 时间戳新鲜 + tiers 非空。"""

    def _run(self, analysis, manifest, engines=None):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            return _bundle(Path(d), manifest, analysis, engines or {})

    def test_independent_engines_pass(self):
        rep = self._run(
            _pricing_comp(engines=("playwright", "crawl4ai")),
            _manifest_with_hashes("https://www.wati.io/pricing", {
                "playwright": "aaaa", "crawl4ai": "bbbb"}),
        )
        self.assertTrue(rep["passed"])

    def test_same_hash_two_engines_fail(self):
        """两引擎拿到同一反爬变体(内容哈希相同)≠ 交叉验证。"""
        rep = self._run(
            _pricing_comp(engines=("playwright", "crawl4ai")),
            _manifest_with_hashes("https://www.wati.io/pricing", {
                "playwright": "same", "crawl4ai": "same"}),
        )
        self.assertEqual(len(_violations_by_gate(rep, "G3")), 1)

    def test_single_engine_fail(self):
        rep = self._run(
            _pricing_comp(engines=("playwright",)),
            _manifest_with_hashes("https://www.wati.io/pricing", {
                "playwright": "aaaa"}),
        )
        self.assertEqual(len(_violations_by_gate(rep, "G3")), 1)

    def test_stale_timestamp_fail(self):
        rep = self._run(
            _pricing_comp(scraped_at="2026-01-01 00:00 UTC"),
            _manifest_with_hashes("https://www.wati.io/pricing", {
                "playwright": "aaaa", "crawl4ai": "bbbb"}),
        )
        self.assertEqual(len(_violations_by_gate(rep, "G3")), 1)

    def test_verified_with_empty_tiers_fail(self):
        rep = self._run(
            _pricing_comp(tiers=0),
            _manifest_with_hashes("https://www.wati.io/pricing", {
                "playwright": "aaaa", "crawl4ai": "bbbb"}),
        )
        self.assertEqual(len(_violations_by_gate(rep, "G3")), 1)

    def test_unverified_pricing_not_checked(self):
        """未验证定价(⚠ 徽章)不受 G3 约束 —— 诚实降级可交付。"""
        rep = self._run(
            _pricing_comp(verified=False, engines=(), scraped_at="", tiers=0),
            _ok_manifest(),
        )
        self.assertTrue(rep["passed"])
```

注意 `test_stale_timestamp_fail` 依赖真实当前时间（今天 2026-08-26，1 月已 >14 天）。若将来测试在新机器跑，"2026-01-01" 相对任何 ≥2026-01-15 的日期都过期，稳定。

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_verify.py -q` → G3 测试 FAIL

- [ ] **Step 3: 实现 G3**

追加到 `gates.py`：

```python
import datetime as _dt
import time as _time

from verify import TTL_DAYS

_TS_FMT = "%Y-%m-%d %H:%M UTC"


def _ts_age_days(ts: str) -> float:
    """UTC 时间戳距今天数;解析失败返回 inf(视为最陈旧)。"""
    try:
        t = _time.mktime(_time.strptime(ts or "", _TS_FMT))
    except (ValueError, OverflowError):
        return float("inf")
    return (_time.time() - t) / 86400.0
```

（`time.mktime` 按本地时区解析会差几小时；TTL=14 天的粒度下可忽略。若要精确，用 `calendar.timegm(time.strptime(...))` — 采用后者更正确，写成 `import calendar; t = calendar.timegm(_time.strptime(...))`。）

```python
@register
def g3_pricing_integrity(analysis, manifest, engine_index, rep: Report):
    """G3: pricing_verified=true 的三重完整性。

    历史事故:①两引擎拿到同一反爬/区域变体页互证错误价格(关联捕获);
    ②pricing-cache.json 的 TTL 从未生效,陈旧价格永久 verified。
    """
    fetched = manifest.get("fetched") or {}
    for competitor in analysis.get("competitors") or []:
        name = competitor.get("name", "?")
        field = f"competitors[{name}]"
        if not competitor.get("pricing_verified"):
            continue  # 未验证定价由 ⚠ 徽章诚实展示,不属于 G3
        src = (competitor.get("pricing_source") or "").strip()
        engines = competitor.get("pricing_engines") or []
        tiers = competitor.get("pricing_tiers") or []
        if not tiers:
            rep.hard(
                "G3", f"{field}.pricing_tiers", src,
                "pricing_verified=true 但 tiers 为空",
                "补 tiers 或把 pricing_verified 改为 false",
            )
        # 引擎独立性:验证引擎在本轮 manifest 里的内容哈希必须 ≥2 个不同值
        hashes = set()
        for e in engines:
            h = ((fetched.get(src) or {}).get("engines") or {}).get(e, {})
            if h.get("content_hash"):
                hashes.add(h["content_hash"])
        if len(hashes) < 2:
            rep.hard(
                "G3", f"{field}.pricing_engines", src,
                f"verified 定价的内容独立引擎不足({len(hashes)} 个不同哈希,"
                f"engines={engines})—— 同一变体页被多引擎抓到不算交叉验证",
                "重爬(换网络环境/等反爬窗口),或降级 pricing_verified=false",
            )
        age = _ts_age_days(competitor.get("pricing_scraped_at"))
        if age > TTL_DAYS:
            rep.hard(
                "G3", f"{field}.pricing_scraped_at", src,
                f"定价证据已陈旧({age:.0f} 天前,TTL={TTL_DAYS} 天)",
                "重爬定价页刷新证据",
            )
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_verify.py -q` → PASS（19 个）

- [ ] **Step 5: 回归 + 提交**

```bash
git add gates.py tests/test_verify.py
git commit -m "feat: G3 定价完整性门禁 — 引擎内容独立性 + TTL + tiers 非空"
```

---

### Task 4: G4 缺失诚实 + G5 反伪造 + G6 URL 卫生

**Files:**
- Modify: `gates.py`
- Test: `tests/test_verify.py`（追加）

**Interfaces:**
- Consumes: Task 2 `iter_evidence_urls`、Task 3 `_ts_age_days`
- Produces: `@register def g4_missing_honesty(...)` / `@register def g5_antifabrication(...)` / `@register def g6_url_hygiene(...)`；`_registrable_domain(host) -> str`（Task 5 复用）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_verify.py`：

```python
class TestG4MissingHonesty(unittest.TestCase):
    """G4: 失败有记录;缺失字段不断言来源。"""

    def _run(self, manifest, analysis, engines=None):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            return _bundle(Path(d), manifest, analysis, engines or {})

    def test_failed_fetch_without_failure_record(self):
        m = _ok_manifest(urls_failed=["https://www.wati.io/pricing"])
        rep = self._run(m, _one_comp_analysis())  # failures 列表为空 = 静默吞掉
        self.assertEqual(len(_violations_by_gate(rep, "G4")), 1)

    def test_failed_fetch_with_failure_record_passes(self):
        m = _ok_manifest(urls_failed=["https://www.wati.io/pricing"])
        m["failures"] = [{
            "competitor": "WATI", "url": "https://www.wati.io/pricing",
            "kind": "pricing", "error": "404",
        }]
        rep = self._run(m, _one_comp_analysis())
        self.assertEqual(len(_violations_by_gate(rep, "G4")), 0)

    def test_missing_value_with_source_asserted(self):
        """founded="—" 却断言 founded_source → 读者点开找不到任何东西。"""
        rep = self._run(
            _ok_manifest(urls_ok=["https://www.wati.io/about"]),
            _one_comp_analysis(
                founded="—", founded_source="https://www.wati.io/about",
                headquarters="—", headquarters_source="",
                team_size="—", team_size_source="",
            ),
        )
        v = _violations_by_gate(rep, "G4")
        self.assertEqual(len(v), 1)
        self.assertIn("founded_source", v[0]["field"])

    def test_value_with_source_passes(self):
        rep = self._run(
            _ok_manifest(urls_ok=["https://www.wati.io/about"]),
            _one_comp_analysis(
                founded="2019", founded_source="https://www.wati.io/about",
            ),
        )
        self.assertEqual(len(_violations_by_gate(rep, "G4")), 0)


_FABRICATED_QUOTES = ["Pricing gets expensive at scale"]


class TestG5Antifabrication(unittest.TestCase):
    """G5: 已知伪造引文黑名单 / repr 泄漏 / 占位符。"""

    def _run(self, analysis):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            return _bundle(Path(d), _ok_manifest(), analysis)

    def test_blacklisted_strength_quote(self):
        rep = self._run(_one_comp_analysis(strengths=[{
            "point": "p",
            "evidence": '官网原文: "Pricing gets expensive at scale"',
            "score": 0, "source": "",
        }]))
        self.assertEqual(len(_violations_by_gate(rep, "G5")), 1)

    def test_repr_leak_in_field(self):
        rep = self._run(_one_comp_analysis(tagline="['Best', 'tool']"))
        self.assertEqual(len(_violations_by_gate(rep, "G5")), 1)

    def test_placeholder_in_opportunities(self):
        a = _one_comp_analysis()
        a["opportunities"] = [{"title": "待补充", "inspiration": ""}]
        rep = self._run(a)
        self.assertEqual(len(_violations_by_gate(rep, "G5")), 1)

    def test_clean_analysis_passes(self):
        rep = self._run(_one_comp_analysis())
        self.assertTrue(rep["passed"])


class TestG6UrlHygiene(unittest.TestCase):
    """G6: URL 格式(硬) + 跨竞品域名(警告)。"""

    def _run(self, analysis, manifest=None, engines=None):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            return _bundle(Path(d), manifest or _ok_manifest(), analysis, engines)

    def test_malformed_url_hard_fail(self):
        rep = self._run(_one_comp_analysis(
            pricing_source="notaurl", pricing="x",
        ))
        self.assertEqual(len(_violations_by_gate(rep, "G6")), 1)

    def test_cross_competitor_domain_warning_only(self):
        rep = self._run(_one_comp_analysis(
            founded="2019",
            founded_source="https://competitor-x.com/about",
        ), manifest=_ok_manifest(urls_ok=["https://competitor-x.com/about"]))
        self.assertEqual(len(_violations_by_gate(rep, "G6")), 0)  # 仅警告
        w = [x for x in rep["warnings"] if x["gate"] == "G6"]
        self.assertEqual(len(w), 1)

    def test_subdomain_of_own_site_no_warning(self):
        m = _ok_manifest(urls_ok=["https://docs.wati.io/api"])
        rep = self._run(_one_comp_analysis(
            tech_signals=[{"name": "REST API", "source": "https://docs.wati.io/api"}],
        ), manifest=m)
        self.assertEqual(
            len([x for x in rep["warnings"] if x["gate"] == "G6"]), 0)
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_verify.py -q` → G4/G5/G6 测试 FAIL

- [ ] **Step 3: 实现 G4 + G5 + G6**

追加到 `gates.py`：

```python
from urllib.parse import urlparse

# 历史伪造事故引文黑名单(与 render.py _FAKE_QUOTE_BLACKLIST 同源,
# 在数据侧再跑一遍 —— render 只查 HTML,verify 查 JSON 源头)
_FABRICATED_QUOTE_RX = re.compile(
    r"Pricing gets expensive at scale"
    r"|Best (?:value|tool) for (?:small|growing) (?:teams|businesses)"
    r"|Highly rated by (?:thousands of )?users worldwide",
    re.I,
)

_REPR_LEAK_RX = re.compile(r"\['|\{\"|\"&#39;|\{'name':")

_MISSING_MARKERS = ("", "—", None)


def _is_missing(v) -> bool:
    return v in _MISSING_MARKERS


@register
def g4_missing_honesty(analysis, manifest, engine_index, rep: Report):
    """G4: 抓取失败必须有记录;字段缺失时不得断言来源。

    历史事故:①run_youzi.py 爬取失败 print+continue 静默跳过,无失败清单;
    ②founded 抓不到仍标 founded_source=官网,读者点开找不到任何东西。
    """
    fetched = manifest.get("fetched") or {}
    failures = manifest.get("failures") or []
    failed_urls = {f.get("url") for f in failures if f.get("url")}

    # a) fetched 里 status=failed 的 URL 必须出现在 failures 清单
    for url, ent in fetched.items():
        if ent.get("status") == "failed" and url not in failed_urls:
            rep.hard(
                "G4", "manifest.failures", url,
                "抓取失败的 URL 没有进 failures 清单(静默吞掉)",
                "把该失败写入 manifest.failures {competitor,url,kind,error}",
            )

    # b) 缺失字段不得断言来源
    for competitor in analysis.get("competitors") or []:
        name = competitor.get("name", "?")
        for field in ("founded", "headquarters", "team_size", "tagline"):
            if _is_missing(competitor.get(field)) and (competitor.get(f"{field}_source") or "").strip():
                rep.hard(
                    "G4", f"competitors[{name}].{field}_source",
                    competitor[f"{field}_source"],
                    f"{field} 缺失({competitor.get(field)!r})却断言了来源 —— 读者点开找不到内容",
                    f"清空 {field}_source,或补上真实值",
                )


@register
def g5_antifabrication(analysis, manifest, engine_index, rep: Report):
    """G5: 已知伪造形态在 JSON 源头拦截(render 只查 HTML,这里查数据)。"""

    def _scan(obj, path):
        if isinstance(obj, str):
            if _FABRICATED_QUOTE_RX.search(obj):
                rep.hard("G5", path, "", f"命中历史伪造引文黑名单: “{obj[:60]}…”",
                         "删除或替换为 02-raw 可 grep 的真实引文")
            elif _REPR_LEAK_RX.search(obj):
                rep.hard("G5", path, "", f"Python repr 泄漏: {obj[:60]}",
                         "数据应为字符串/数组,不是 str(list/dict) 产物")
        elif isinstance(obj, dict):
            for k, v in obj.items():
                _scan(v, f"{path}.{k}")
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                _scan(v, f"{path}[{i}]")

    _scan(analysis.get("competitors") or [], "competitors")

    # 占位符不得混入派生板块(与 render.py 同规则)
    for key in ("opportunities", "gaps"):
        for i, item in enumerate(analysis.get(key) or []):
            blob = " ".join(str(v) for v in (item or {}).values()) if isinstance(item, dict) else str(item)
            if "待补充" in blob:
                rep.hard("G5", f"{key}[{i}]", "",
                         "派生板块出现「待补充」占位符",
                         f"{key} 要么有证据支撑,要么整条删除")


def _registrable_domain(host: str) -> str:
    """粗取可注册域:wati.io / docs.wati.io → wati.io。

    ponytail: 不引 tldextract,取最后两 label —— 对本工具的目标域
    (SaaS 官网)足够;co.uk 类公共后缀会误判,仅在警告级使用,可接受。
    """
    parts = (host or "").lower().split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else (host or "").lower()


@register
def g6_url_hygiene(analysis, manifest, engine_index, rep: Report):
    """G6: URL 格式硬检查;证据指向其他竞品主域 = 警告。"""
    for competitor in analysis.get("competitors") or []:
        name = competitor.get("name", "?")
        own = _registrable_domain(urlparse(competitor.get("url") or "").hostname or "")
        for field, url in _evidence_fields(competitor):
            rep.counters["claims_checked"] += 0  # G1 已计数,这里不重复
            p = urlparse(url)
            if p.scheme not in ("http", "https") or not p.netloc:
                rep.hard("G6", field, url, "URL 格式非法(须为绝对 http(s) 地址)",
                         "修正为合法 URL 或清空")
                continue
            src = _registrable_domain(p.hostname or "")
            if own and src and src != own:
                rep.warn("G6", field, f"证据域名 {src} 与竞品主域 {own} 不同"
                         "(第三方来源需在 Step 3 确认已实际抓取)")
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_verify.py -q` → PASS（28 个）

- [ ] **Step 5: 回归 + 提交**

```bash
git add gates.py tests/test_verify.py
git commit -m "feat: G4 缺失诚实 + G5 反伪造 + G6 URL 卫生门禁"
```

---

### Task 5: N1/N2 网络门禁（--network，opt-in）

**Files:**
- Create: `network_gates.py`
- Modify: `verify.py`（Task 1 已预留 `from verify import network_gates` 调用点——文件名已匹配，无需改）
- Test: `tests/test_verify.py`（追加）

**Interfaces:**
- Consumes: `gates.iter_evidence_urls`、`gates._registrable_domain`、`verify.norm_ws`
- Produces: `network_gates.run_all(analysis, manifest, rep, sample=None)`；`fetch_url(url, timeout=10, retries=1) -> dict {ok, http_status, final_url, error}`（urllib，浏览器 UA）；`verify_analysis(..., network=True)` 生效

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_verify.py`：

```python
class TestNetworkGates(unittest.TestCase):
    """N1/N2: 全部 mock,不发真实网络请求。"""

    def test_n1_dead_url_hard_fail(self):
        import tempfile
        import network_gates
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            m = _ok_manifest(urls_ok=["https://www.wati.io"])
            a = _write(Path(d) / "a.json", _one_comp_analysis(
                pricing_source="https://www.wati.io/pricing",
                pricing="x",
            ))
            mm = _write(Path(d) / "m.json", m)
            with mock.patch.object(
                network_gates, "fetch_url",
                return_value={"ok": False, "http_status": 404,
                              "final_url": "", "error": "HTTP 404"},
            ):
                rep = verify_analysis(a, mm, Path(d), network=True)
            v = [x for x in rep["violations"] if x["gate"] == "N1"]
            self.assertEqual(len(v), 1)

    def test_n1_live_url_passes(self):
        import tempfile
        import network_gates
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            m = _ok_manifest(urls_ok=["https://www.wati.io"])
            a = _write(Path(d) / "a.json", _one_comp_analysis(
                pricing_source="https://www.wati.io/pricing",
                pricing="x",
            ))
            mm = _write(Path(d) / "m.json", m)
            with mock.patch.object(
                network_gates, "fetch_url",
                return_value={"ok": True, "http_status": 200,
                              "final_url": "https://www.wati.io/pricing", "error": ""},
            ):
                rep = verify_analysis(a, mm, Path(d), network=True)
            self.assertTrue(rep["passed"])

    def test_n1_cross_domain_redirect_warning(self):
        import tempfile
        import network_gates
        from unittest import mock
        with tempfile.TemporaryDirectory() as d:
            m = _ok_manifest(urls_ok=["https://www.wati.io"])
            a = _write(Path(d) / "a.json", _one_comp_analysis(
                pricing_source="https://www.wati.io/pricing",
                pricing="x",
            ))
            mm = _write(Path(d) / "m.json", m)
            with mock.patch.object(
                network_gates, "fetch_url",
                return_value={"ok": True, "http_status": 200,
                              "final_url": "https://other-cdn.net/pricing", "error": ""},
            ):
                rep = verify_analysis(a, mm, Path(d), network=True)
            self.assertEqual(
                len([w for w in rep["warnings"] if w["gate"] == "N1"]), 1)

    def test_sample_limits_urls(self):
        """--sample 只抽查前 N 个 URL(monkeypatch 计数)。"""
        import network_gates
        calls = []
        orig = network_gates.fetch_url
        from unittest import mock

        def counting(url, **kw):
            calls.append(url)
            return {"ok": True, "http_status": 200, "final_url": url, "error": ""}

        comp = _one_comp_analysis(
            pricing_source="https://a.com/p", tagline="t",
        )
        with mock.patch.object(network_gates, "fetch_url", side_effect=counting):
            r = network_gates.Report()
            network_gates.run_all(
                {"competitors": [comp]}, {"fetched": {}}, r, sample=1)
        self.assertEqual(len(calls), 1)
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_verify.py -q` → FAIL — `No module named 'network_gates'`

- [ ] **Step 3: 实现 network_gates.py**

创建 `network_gates.py`：

```python
#!/usr/bin/env python3
"""verify 的网络门禁(N1/N2)。仅 --network 时启用。

ponytail: urllib + 串行限速(~1 req/s)足够 —— N1 的对象是几十个 URL,
并发加速收益 < 反爬风险。N2 用裸 HTML 剥标签做容错匹配,不引引擎。
"""

import html
import re
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from gates import _registrable_domain, iter_evidence_urls
from verify import Report, norm_ws

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)


def fetch_url(url: str, timeout: int = 10, retries: int = 1) -> dict:
    """GET 一个 URL,返回 {ok, http_status, final_url, error}。

    浏览器 UA + 1 次重试;3xx 跟随(urllib 默认),final_url 记录落点。
    """
    last = {"ok": False, "http_status": 0, "final_url": "", "error": ""}
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resp.read(65536)  # 只确认可达,不全量下载
                return {
                    "ok": 200 <= resp.status < 300,
                    "http_status": resp.status,
                    "final_url": resp.geturl(),
                    "error": "",
                }
        except urllib.error.HTTPError as e:
            last = {"ok": False, "http_status": e.code, "final_url": url,
                    "error": f"HTTP {e.code}"}
        except Exception as e:
            last = {"ok": False, "http_status": 0, "final_url": url,
                    "error": f"{type(e).__name__}: {e}"}
        if attempt < retries:
            time.sleep(1.0)
    return last


def _collect_urls(analysis, manifest) -> list:
    """被交付 claim 实际引用的证据 URL(去重保序)。"""
    urls = []
    for competitor in analysis.get("competitors") or []:
        for u in iter_evidence_urls(competitor):
            if u not in urls:
                urls.append(u)
    return urls


def run_all(analysis, manifest, rep: Report, sample=None):
    urls = _collect_urls(analysis, manifest)
    if sample is not None and sample >= 0:
        urls = urls[: max(sample, 0)]
    rep.counters["urls_checked"] = len(urls)

    last_t = 0.0
    for url in urls:
        # 限速 ~1 req/s
        dt = time.time() - last_t
        if dt < 1.0:
            time.sleep(1.0 - dt)
        last_t = time.time()

        r = fetch_url(url)
        if not r["ok"]:
            rep.hard(
                "N1", "source_url", url,
                f"回访不可达({r['error'] or r['http_status']})",
                "来源已失效:删除该字段或换可達来源;诚实标注「未验证」可交付",
            )
            continue
        src_domain = _registrable_domain(urlparse(url).hostname or "")
        final_domain = _registrable_domain(urlparse(r["final_url"]).hostname or "")
        if src_domain and final_domain and src_domain != final_domain:
            rep.warn("N1", "source_url",
                     f"{url} 跨域重定向到 {r['final_url']}(内容归属需人工确认)")

    # N2 quote 实时复核:只对 strengths/gtm/moat 的 quote 抽查(页面漂移常态,
    # 权威比对是离线 G2,故 N2 仅警告)
    n2_checked = 0
    for competitor in analysis.get("competitors") or []:
        name = competitor.get("name", "?")
        for key in ("gtm_evidence", "moat_evidence"):
            for i, ev in enumerate(competitor.get(key) or []):
                if n2_checked >= 5:  # ponytail: 固定抽查 5 条,够信号
                    break
                if not (isinstance(ev, dict) and ev.get("quote") and ev.get("source")):
                    continue
                n2_checked += 1
                quote, src = ev["quote"], ev["source"]
                try:
                    req = urllib.request.Request(src, headers={"User-Agent": _UA})
                    with urllib.request.urlopen(req, timeout=10) as resp:
                        body = resp.read(200000).decode("utf-8", "ignore")
                except Exception:
                    continue  # 可达性已由 N1 报告
                text = norm_ws(html.unescape(re.sub(r"<[^>]+>", " ", body)))
                if norm_ws(quote) not in text:
                    rep.warn("N2", f"competitors[{name}].{key}[{i}]",
                             f"quote 在当前页面已不可见(可能已更新): “{quote[:50]}…”")
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_verify.py -q` → PASS（32 个）

- [ ] **Step 5: 回归 + 提交**

```bash
git add network_gates.py tests/test_verify.py
git commit -m "feat: N1 可达性 + N2 quote 复核网络门禁(opt-in --network)"
```

---

### Task 6: 爬取侧修复 I — F1 TTL 生效 + F2 诚实来源 + F5 引擎独立性

**Files:**
- Modify: `scripts/crawl_competitors.py`（L746-764 缓存、L524-743 投票、L1752-1753、L2004-2057、L2092）
- Test: `tests/test_pricing_extract.py`（追加）+ `tests/test_pipeline.py`（追加）

**Interfaces:**
- Consumes: 无
- Produces:
  - `_cache_fresh(cached: dict) -> bool`（TTL 判定）
  - `_content_hash(md: str) -> str`（空白归一化 SHA-256 前 16 位，manifest 也用）
  - `_extract_pricing_evidence` 返回值新增 `vote_detail[i].independent_votes: int`；`source_url` 仅在有证据时非空
  - entry 新增 `pricing_from_cache: bool`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_pricing_extract.py`（沿用该文件现有 import 风格，文件头已有 `from scripts.crawl_competitors import (` 块——在该块内追加导入 `_cache_fresh, _content_hash, _extract_pricing_evidence` 中缺的名称）：

```python
class TestCacheTTL(unittest.TestCase):
    """F1: 14 天 TTL 此前定义了但从未生效 —— 过期缓存必须视为 miss。"""

    def test_fresh_cache(self):
        import time as _t
        ts = _t.strftime("%Y-%m-%d %H:%M UTC", _t.gmtime())
        self.assertTrue(_cache_fresh({"scraped_at": ts}))

    def test_stale_cache(self):
        self.assertFalse(_cache_fresh({"scraped_at": "2026-01-01 00:00 UTC"}))

    def test_garbage_timestamp(self):
        self.assertFalse(_cache_fresh({"scraped_at": "???"}))
        self.assertFalse(_cache_fresh({}))


def _mk_result(engine_mds: dict) -> dict:
    """构造 scrape_smart 形态的 result:engine → markdown。"""
    return {
        "success": bool(engine_mds),
        "all_results": [
            {"scraper": e, "success": True, "markdown": md}
            for e, md in engine_mds.items()
        ],
    }


class TestEngineIndependence(unittest.TestCase):
    """F5: 两引擎内容哈希相同(同一反爬变体)不得交叉验证。"""

    PRICING_MD_A = "# Pricing\nGrowth $59/mo\nPro $119/mo\n"
    PRICING_MD_B = "# Plans\nGrowth $59 /mo\nPro $119 /mo\n"

    def test_identical_content_not_verified(self):
        ev = _extract_pricing_evidence(
            _mk_result({"playwright": self.PRICING_MD_A,
                        "crawl4ai": self.PRICING_MD_A}),  # 完全相同 = 变体互证
            "https://x.com/pricing",
        )
        self.assertFalse(ev["verified"])
        self.assertEqual(ev["vote_detail"][0]["independent_votes"], 1)

    def test_different_content_verified(self):
        ev = _extract_pricing_evidence(
            _mk_result({"playwright": self.PRICING_MD_A,
                        "crawl4ai": self.PRICING_MD_B}),
            "https://x.com/pricing",
        )
        self.assertTrue(ev["verified"])
        self.assertEqual(ev["vote_detail"][0]["independent_votes"], 2)

    def test_source_url_empty_when_no_evidence(self):
        """F2: 全引擎无价格行时 source_url 必须为空(不得让 404 URL 当来源)。"""
        ev = _extract_pricing_evidence(
            _mk_result({"playwright": "nothing here",
                        "crawl4ai": "no prices"}),
            "https://x.com/pricing-404",
        )
        self.assertEqual(ev["source_url"], "")

    def test_content_hash_stable(self):
        h1 = _content_hash("a  b\nc")
        h2 = _content_hash("a b   c")
        self.assertEqual(h1, h2)
        self.assertNotEqual(h1, _content_hash("different"))
```

追加到 `tests/test_pipeline.py`（放在现有 TestNoFabrication 类之后）：

```python
class TestHonestPricingSource(unittest.TestCase):
    """F2: 定价证据为空时,entry 不得用猜测 URL 充当 pricing_source。"""

    def test_no_evidence_no_source(self):
        from scripts.crawl_competitors import _build_competitor_entry
        scraped = {
            "name": "X", "url": "https://x.com",
            "pricing_source": "",  # _scrape_one 修复后失败时保持 ""
            "tagline_source": "https://x.com",
            "founded_source": "", "team_size_source": "",
            "headquarters_source": "",
            "raw_markdown": {
                "home": "# X\nTool for teams",
                "pricing": "",  # 定价页全失败
            },
            "page_urls": {"home": "https://x.com"},
            "pricing_evidence": {"pricing": "—", "verified": False,
                                 "engines": [], "source_url": "",
                                 "scraped_at": "", "vote_detail": [],
                                 "tiers": []},
            "site_title": "X — tool",
        }
        entry, warnings = _build_competitor_entry(scraped)
        self.assertEqual(entry["pricing_source"], "")
        self.assertFalse(entry["pricing_verified"])
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_pricing_extract.py tests/test_pipeline.py -q`
Expected: FAIL — `_cache_fresh` 不存在；identical-content 测试拿到 verified=True；no-evidence 测试拿到 source_url 非空

- [ ] **Step 3: 实现修复**

3a. `scripts/crawl_competitors.py` 文件头 import 区（现有 `import argparse/hashlib 检查后决定` —— 文件头目前有 `import json, re, sys, time, argparse` 等；查看文件头后若无 `hashlib` 与 `calendar` 则补 `import calendar` 与 `import hashlib`）。

3b. 替换 L746-764 区域（缓存函数）为：

```python
_PRICING_CACHE_PATH = ROOT / "storage" / "pricing-cache.json"
_PRICING_CACHE_TTL_DAYS = 14


def _content_hash(md: str) -> str:
    """引擎原文的内容指纹(空白归一化后 SHA-256 前 16 位)。

    用途:①manifest.fetched 记录(G3 独立性判定);②定价投票的引擎
    独立性(F5)—— 两引擎拿到同一反爬变体页时指纹相同,不算交叉验证。
    """
    import hashlib
    return hashlib.sha256(
        re.sub(r"\s+", " ", (md or "")).encode("utf-8")
    ).hexdigest()[:16]


def _load_pricing_cache() -> Dict:
    try:
        return json.loads(_PRICING_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_pricing_cache(cache: Dict) -> None:
    # 原子写(tmp+rename):避免并发运行读到半截 JSON 被静默判损坏
    import os
    try:
        _PRICING_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PRICING_CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        os.replace(tmp, _PRICING_CACHE_PATH)
    except Exception:
        pass


def _cache_fresh(cached: Dict) -> bool:
    """缓存是否仍在 TTL 内(scraped_at 距今 ≤14 天)。

    历史缺陷:_PRICING_CACHE_TTL_DAYS 定义后从未被引用,陈旧缓存
    永久有效 —— verified 定价可能是数月前的价格。
    """
    import calendar
    try:
        ts = time.strptime((cached or {}).get("scraped_at", ""), "%Y-%m-%d %H:%M UTC")
        age_days = (time.time() - calendar.timegm(ts)) / 86400.0
    except (ValueError, OverflowError):
        return False
    return 0 <= age_days <= _PRICING_CACHE_TTL_DAYS
```

3c. `_build_competitor_entry` 缓存读取点（原 L2014-2015）加 TTL：

```python
        cached = _pcache.get(_ckey)
        if cached and _has_real_prices(cached.get("tiers")) and _cache_fresh(cached):
```

并在 starved-else 分支后（用缓存时）给 entry 增加 `pricing_from_cache`（在 entry dict 的 `"pricing_verified": ...` 行后加）：

```python
        "pricing_from_cache": bool(_pricing_starved_note and pricing_ev.get("verified")),
```

3d. `_extract_pricing_evidence`（L524-743）三处修改：

在 `per_engine` 构建后加引擎指纹（L538 后）：

```python
    # F5 引擎独立性:按引擎原文内容哈希判定 —— 名称不同的两个引擎若拿到
    # 逐字相同的内容(同一反爬/区域变体),只是一次取证,不是交叉验证
    engine_hash = {}
    for r in (scrape_result.get("all_results") or []):
        if r.get("success") and r.get("markdown"):
            engine_hash[r.get("scraper", "?")] = _content_hash(r["markdown"])
```

cluster 构建处（`ent["engines"].add(eng)` 之后）：

```python
            ent.setdefault("hashes", set()).add(
                engine_hash.get(eng) or f"no-md:{eng}"
            )
```

verified 判定处（原 L653-654）替换：

```python
    # verified 按「内容独立引擎数」:同一变体页被 N 个引擎复述仍只有 1 票
    max_indep = max(
        (len(e.get("hashes") or e["engines"]) for e in picked), default=0
    )
    verified = max_indep >= 2
    engines = sorted({e for ent in picked for e in ent["engines"]})
```

vote_detail（原 L735-738）加独立性票数：

```python
        "vote_detail": [
            {"line": e["line"], "engines": sorted(e["engines"]),
             "independent_votes": len(e.get("hashes") or e["engines"])}
            for e in picked
        ],
```

source_url 诚实化（return dict 中 `"source_url": pricing_url` 替换；`plan_names` 需在 `if not picked:` 前初始化 `plan_names = []`）：

```python
        "source_url": pricing_url if (per_engine or plan_names) else "",
```

3e. `_scrape_one` L1752-1753 删除猜测兜底：

```python
        # F2:定价全失败时不再回填猜测 URL —— 未抓到的页面不能当来源
        # (历史缺陷:guess/disc 404 时 pricing_source 指向死链)
```
（即整段 `if kind == "pricing" and not result.get("pricing_source"): result["pricing_source"] = guess or disc or ""` 删除。）

3f. entry 的 pricing_source（原 L2092）替换为：

```python
        "pricing_source": (
            pricing_ev.get("source_url")
            # 稀松回退提取自首页时,来源如实指向首页
            or (scraped["url"] if (pricing != "—" and not pricing_ev.get("pricing")
                                   and _extract_price(pricing_md) in ("", None))
                else "")
        ),
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_pricing_extract.py tests/test_pipeline.py -q` → PASS

- [ ] **Step 5: 全量回归 + 提交**

Run: `python3 -m pytest tests/ -q` → 全绿

```bash
git add scripts/crawl_competitors.py tests/test_pricing_extract.py tests/test_pipeline.py
git commit -m "fix: 定价缓存 TTL 生效 + 无证据不设来源 + 引擎内容独立性验证"
```

---

### Task 7: 爬取侧修复 II — F3 行级归属 + F4 feature 出处诚实化

**Files:**
- Modify: `scripts/crawl_competitors.py`（`_build_competitor_entry` L1970-2003、L1960、L2156-2163）
- Test: `tests/test_pipeline.py`（追加）

**Interfaces:**
- Consumes: 现有 `_extract_founded/_extract_location/_extract_team_size`
- Produces: `_extract_company_field(pages, extractor, ctx_rx) -> (value, url, quote)`；entry 新增 `founded_quote/headquarters_quote/team_size_quote` 字段（值非空时必有 url+quote）

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_pipeline.py`：

```python
class TestCompanyFieldAttribution(unittest.TestCase):
    """F3: founded/HQ/team 来源指向真正命中该值的页面(行级归属)。"""

    def _scrape(self, pages):
        return {
            "name": "X", "url": "https://x.com",
            "pricing_source": "", "tagline_source": "https://x.com",
            "founded_source": "", "team_size_source": "",
            "headquarters_source": "",
            "raw_markdown": {k: md for k, (md, _) in pages.items()},
            "page_urls": {k: u for k, (_, u) in pages.items()},
            "pricing_evidence": {"pricing": "—", "verified": False,
                                 "engines": [], "source_url": "",
                                 "scraped_at": "", "vote_detail": [],
                                 "tiers": []},
            "site_title": "X — tool",
        }

    def test_year_on_pricing_page_attributed_there(self):
        """年份在 pricing 页命中 → founded_source 指向 pricing 页而非官网。"""
        from scripts.crawl_competitors import _build_competitor_entry
        scraped = self._scrape({
            "home": ("# X\nTool for teams", "https://x.com"),
            "about": ("", ""),
            "pricing": ("# Pricing\nFounded in 2019 by ex-Googlers\n$59/mo",
                        "https://x.com/pricing"),
        })
        entry, _ = _build_competitor_entry(scraped)
        self.assertEqual(entry["founded"], "2019")
        self.assertEqual(entry["founded_source"], "https://x.com/pricing")
        self.assertIn("2019", entry["founded_quote"])

    def test_about_page_priority(self):
        from scripts.crawl_competitors import _build_competitor_entry
        scraped = self._scrape({
            "home": ("# X\nFounded in 2020", "https://x.com"),
            "about": ("# About\nFounded in 2015, headquartered in Kuala Lumpur",
                      "https://x.com/about"),
        })
        entry, _ = _build_competitor_entry(scraped)
        self.assertEqual(entry["founded"], "2015")  # about 优先
        self.assertEqual(entry["founded_source"], "https://x.com/about")
        self.assertEqual(entry["headquarters"], "Kuala Lumpur")
        self.assertEqual(entry["headquarters_source"], "https://x.com/about")

    def test_not_found_keeps_empty_source(self):
        from scripts.crawl_competitors import _build_competitor_entry
        scraped = self._scrape({
            "home": ("# X\nnothing useful", "https://x.com"),
        })
        entry, _ = _build_competitor_entry(scraped)
        self.assertEqual(entry["founded"], "—")
        self.assertEqual(entry["founded_source"], "")
        self.assertEqual(entry["founded_quote"], "")

    def test_feature_without_evidence_has_empty_source(self):
        """F4: 定位不到出处的功能,source 留空而不是挂 default 页。"""
        from scripts.crawl_competitors import _build_competitor_entry
        md_home = "# X\n## Features\n- Team Inbox\n- Broadcasts\n"
        scraped = self._scrape({
            "home": (md_home, "https://x.com"),
            "features": ("", ""),
        })
        scraped["raw_markdown"]["features"] = "slug-derived-fake-feature"
        entry, _ = _build_competitor_entry(scraped)
        for f in entry["feature_catalog"]["X"]:
            if f["name"] not in md_home:
                self.assertEqual(f["source"], "")
            else:
                self.assertEqual(f["source"], "https://x.com")
```

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_pipeline.py -q` → 新测试 FAIL（现状:定价页命中 → founded_source=官网；feature 无出处 → default_src）

- [ ] **Step 3: 实现修复**

3a. 在 `_extract_team_size`（约 L821）之后新增：

```python
# F3 行级归属:founded/HQ/team 命中行的上下文特征(找 quote 用)
_COMPANY_CTX_RX = {
    "founded": re.compile(
        r"founded|established|launched|成立于|创立于|\b(19|20)\d{2}\b", re.I),
    "headquarters": re.compile(
        r"headquartered|based in|总部|位于|address", re.I),
    "team_size": re.compile(r"employees|people|团队|员工|人", re.I),
}


def _extract_company_field(pages, extractor, ctx_rx):
    """逐页跑 extractor,返回 (value, url, quote) —— 行级归属。

    历史缺陷:来源只标到「about 页或官网」页级 —— 年份在定价页命中
    也被标成官网来源,读者点开首页找不到任何公司信息。
    pages: [(markdown, url)] 按优先级排序(about 先,home 次之)。
    """
    for md, url in pages:
        if not md or not url:
            continue
        val = extractor(md)
        if val and val != "—":
            quote = ""
            for line in md.split("\n"):
                t = line.strip().strip("*_`#> ")
                if 3 <= len(t) <= 160 and ctx_rx.search(t):
                    quote = t[:120]
                    break
            return val, url, quote
    return "", "", ""
```

3b. `_build_competitor_entry` 中替换原公司信息提取块（原 L1970-1973 的 `all_md/company_md` 构造与 L2001-2003 的三次提取）为：

```python
    # F3 公司信息行级归属:about 页优先,逐页提取并记录命中页 + quote
    company_pages = [
        (md, u)
        for md, u in (
            (about_md, scraped.get("page_urls", {}).get("about") or ""),
            (home_md, scraped["url"]),
            (feat_md, scraped.get("page_urls", {}).get("features") or ""),
            (pricing_md, scraped.get("page_urls", {}).get("pricing") or ""),
        )
        if md and u
    ]
```

（放在 `warnings = []` 定义之前），并把：

```python
    founded = _extract_founded(company_md) or _extract_founded(all_md)
    location = _extract_location(company_md) or _extract_location(all_md)
    team_size = _extract_team_size(company_md) or _extract_team_size(all_md)
```

替换为：

```python
    founded, founded_src, founded_quote = _extract_company_field(
        company_pages, _extract_founded, _COMPANY_CTX_RX["founded"])
    location, hq_src, hq_quote = _extract_company_field(
        company_pages, _extract_location, _COMPANY_CTX_RX["headquarters"])
    team_size, team_src, team_quote = _extract_company_field(
        company_pages, _extract_team_size, _COMPANY_CTX_RX["team_size"])
```

3c. entry 组装处（原 `"team_size": team_size,` 行后）加 quote 字段：

```python
        "founded_quote": founded_quote,
        "headquarters_quote": hq_quote,
        "team_size_quote": team_quote,
```

3d. 尾部 source 兜底块（原 L2156-2163）中三行删除 fallback（founded/headquarters/team_size 三行改为直接用行级来源）：

```python
    entry["founded_source"] = founded_src
    entry["headquarters_source"] = hq_src
    entry["team_size_source"] = team_src
```

（`pricing_source/tagline_source` 两行保持不变。）

3e. F4：feature 归因块（原 L1948-1960）—— 删除 `default_src` 计算与使用：

```python
    enriched_features = []
    for f in page_features:
        ftxt = (f.get("name") or "").strip()
        src = ""
        for md, u in page_candidates:
            if md and u and ftxt and ftxt[:30] in md:
                src = u
                break
        # F4:定位不到出处 → source 留空(render 已兼容空 source),
        # 不再默认挂 default_src —— 挂错页比不挂更误导
        enriched_features.append({**f, "source": src})
```

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_pipeline.py -q` → PASS

- [ ] **Step 5: 全量回归 + 提交**

Run: `python3 -m pytest tests/ -q` → 全绿（若旧测试依赖 `default_src` 行为失败，检查 test_pipeline.py 现有 feature 测试断言 —— 现有测试不覆盖 default_src 路径，预期无影响）

```bash
git add scripts/crawl_competitors.py tests/test_pipeline.py
git commit -m "fix: founded/HQ/team 行级归属 + feature 出处诚实化(无证据不留 source)"
```

---

### Task 8: F8 证据包落盘 + F6 run_youzi 失败清单 + F7 resolver 漂移

**Files:**
- Modify: `scripts/crawl_competitors.py`（`_scrape_one`、`_build_competitor_entry` 签名、`crawl_and_build`、`main`）
- Modify: `scripts/run_youzi.py`（step2_crawl）
- Modify: `adapters/competitor_resolver.py`（docstring L5-7）
- Test: `tests/test_pipeline.py`（追加）

**Interfaces:**
- Consumes: Task 6 `_content_hash`
- Produces:
  - `_scrape_one` 返回值新增 `"_manifest": {"fetched": {...}, "engines_by_url": {...}, "failures": [...]}`
  - `_build_competitor_entry(scraped, idx=0) -> (entry, warnings, claims)` — **签名变更**（claims 为 list[dict]，schema 见下）
  - `crawl_and_build(names, topic, timeout=30, manifest_path=None, raw_dir=None) -> dict`（可选参数，默认行为不变）
  - 磁盘契约：`OUT/claims-manifest.json`（run/fetched/claims/failures）+ `OUT/02-raw/<safe_name>.engines.json`（`{url: {engine: md}}`）
  - `run_youzi.step2_crawl` 写 `raw_dir.parent/claims-manifest.json` + `raw_dir/<name>.engines.json`，返回 `(results, failures)`
  - claim schema（与 verify.py Task 2-4 消费端一致）：`{"field", "value", "source_url", "quote", "engine", "verified_by", "from_cache", "scraped_at"}`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_pipeline.py`：

```python
class TestManifestEmission(unittest.TestCase):
    """F8: crawl_and_build 落盘 claims-manifest.json + engines.json。"""

    def test_manifest_and_engines_written(self):
        import tempfile
        from unittest import mock
        import scripts.crawl_competitors as cc

        def fake_scrape_one(resolved, timeout=30, max_chars=25000):
            url = resolved["url"]
            return {
                "name": resolved["canonical_name"], "url": url,
                "pricing_source": "", "tagline_source": url,
                "founded_source": "", "team_size_source": "",
                "headquarters_source": "",
                "raw_markdown": {
                    "home": "# WATI\nWhatsApp API platform for teams",
                    "pricing": "# Pricing\nGrowth $59/mo",
                },
                "page_urls": {"home": url,
                              "pricing": url + "/pricing"},
                "pricing_evidence": {
                    "pricing": "Growth · $59 (/mo)", "verified": True,
                    "engines": ["playwright", "crawl4ai"],
                    "source_url": url + "/pricing",
                    "scraped_at": "2026-08-26 00:00 UTC",
                    "vote_detail": [{"line": "Growth $59/mo",
                                     "engines": ["playwright", "crawl4ai"],
                                     "independent_votes": 2}],
                    "tiers": [{"name": "Growth", "price": "$59",
                               "billing_period": "/mo", "features": [],
                               "source_url": url + "/pricing"}],
                },
                "site_title": "WATI — WhatsApp API",
                "_manifest": {
                    "fetched": {
                        url: {"status": "ok", "engines": {
                            "playwright": {"ok": True, "chars": 500,
                                           "content_hash": "h1"}},
                        }, "fetched_at": "2026-08-26 00:00 UTC"},
                        url + "/pricing": {"status": "ok", "engines": {
                            "playwright": {"ok": True, "chars": 400,
                                           "content_hash": "h2"},
                            "crawl4ai": {"ok": True, "chars": 380,
                                         "content_hash": "h3"},
                        }, "fetched_at": "2026-08-26 00:00 UTC"},
                    },
                    "engines_by_url": {
                        url: {"playwright": "# WATI md"},
                        url + "/pricing": {
                            "playwright": "# Pricing\nGrowth $59/mo",
                            "crawl4ai": "# Plans\nGrowth $59 /mo"},
                    },
                    "failures": [],
                },
            }

        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "03-analysis.json"
            with mock.patch.object(cc, "_scrape_one", fake_scrape_one), \
                 mock.patch.object(cc, "resolve_competitors",
                                   return_value={"wati": {
                                       "canonical_name": "WATI",
                                       "url": "https://www.wati.io",
                                       "confidence": 0.95,
                                       "source": "builtin"}}):
                analysis = cc.crawl_and_build(
                    ["wati"], "WhatsApp 赛道",
                    manifest_path=Path(d) / "claims-manifest.json",
                    raw_dir=Path(d) / "02-raw",
                )
            self.assertTrue((Path(d) / "claims-manifest.json").exists())
            self.assertTrue((Path(d) / "02-raw" / "WATI.engines.json").exists())

            manifest = json.loads(
                (Path(d) / "claims-manifest.json").read_text(encoding="utf-8"))
            self.assertIn("https://www.wati.io", manifest["fetched"])
            # 定价 claim 必须带 vote 行 quote + 两引擎
            pricing_claims = [c for c in manifest["claims"]
                              if "pricing" in c["field"]]
            self.assertTrue(pricing_claims)
            self.assertEqual(pricing_claims[0]["quote"], "Growth $59/mo")
            self.assertEqual(sorted(pricing_claims[0]["verified_by"]),
                             ["crawl4ai", "playwright"])

            engines = json.loads(
                (Path(d) / "02-raw" / "WATI.engines.json").read_text(encoding="utf-8"))
            self.assertIn("Growth $59/mo",
                          engines["https://www.wati.io/pricing"]["playwright"])


class TestRunYouziFailures(unittest.TestCase):
    """F6: run_youzi step2 失败不再静默 —— 写入 manifest.failures。"""

    def test_failure_manifest_written(self):
        import tempfile
        from scripts import run_youzi

        with tempfile.TemporaryDirectory() as d:
            raw = Path(d) / "02-raw"
            with mock.patch.object(
                run_youzi, "step2_crawl_wrapper", create=True, side_effect=None
            ):  # 占位不用;直接调函数:
                pass
            from adapters import adapters_ns  # noqa: F401 — 不存在,占位删除
```

最后一行是错的 —— 删掉 `TestRunYouziFailures` 中占位段，改为直接测 step2_crawl：

```python
class TestRunYouziFailures(unittest.TestCase):
    """F6: run_youzi step2 失败不再静默 —— 写入 manifest.failures。"""

    def test_failure_manifest_written(self):
        import tempfile
        from unittest import mock
        from scripts import run_youzi

        with tempfile.TemporaryDirectory() as d:
            raw = Path(d) / "02-raw"
            with mock.patch.object(
                run_youzi, "scrape_smart",
                side_effect=RuntimeError("boom"),
            ) if hasattr(run_youzi, "scrape_smart") else _dummy():
                pass
```

注意 `run_youzi.step2_crawl` 内部是 `from adapters import scrape_smart`（函数内 import），mock 目标是 `adapters.scrape_smart`。最终写法：

```python
class TestRunYouziFailures(unittest.TestCase):
    """F6: run_youzi step2 失败不再静默 —— 写入 manifest.failures。"""

    def test_failure_manifest_written(self):
        import tempfile
        from unittest import mock
        import adapters
        from scripts import run_youzi

        with tempfile.TemporaryDirectory() as d:
            raw = Path(d) / "02-raw"
            with mock.patch.object(
                adapters, "scrape_smart", side_effect=RuntimeError("boom"),
            ):
                results, failures = run_youzi.step2_crawl(
                    ["https://dead.example.com"], raw)
            self.assertEqual(results, {})
            self.assertEqual(len(failures), 1)
            self.assertEqual(failures[0]["url"], "https://dead.example.com")
            mpath = raw.parent / "claims-manifest.json"
            self.assertTrue(mpath.exists())
            manifest = json.loads(mpath.read_text(encoding="utf-8"))
            self.assertEqual(manifest["failures"][0]["url"],
                             "https://dead.example.com")
            self.assertEqual(
                manifest["fetched"]["https://dead.example.com"]["status"],
                "failed")
```

`tests/test_pipeline.py` 顶部需确认已 `import json / from pathlib import Path / from unittest import mock`（文件头已有 json/Path；mock 需补 `from unittest import mock`）。

- [ ] **Step 2: 运行确认失败**

Run: `python3 -m pytest tests/test_pipeline.py -q` → 新测试 FAIL（`crawl_and_build` 无 manifest_path 参数 / `_build_competitor_entry` 返回二元组 / step2_crawl 返回 dict / engines.json 不存在）

- [ ] **Step 3: 实现修复**

3a. `scripts/crawl_competitors.py` `_scrape_one`：

result 初始化（L1573-1583）加：

```python
        "_manifest": {"fetched": {}, "engines_by_url": {}, "failures": []},
```

`_crawl_page` 内，`r = scrape_smart(...)` 与 `md = ...` 之后（`if kind == "pricing":` 块之前）插入：

```python
            # F8 证据包:本轮抓取记录(状态 + 各引擎指纹)+ 引擎原文
            m_ent = {
                "status": "ok" if (r.get("success") and r.get("markdown")) else "failed",
                "engines": {},
                "fetched_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
            }
            for x in (r.get("all_results") or []):
                if x.get("scraper") and x.get("success") and x.get("markdown"):
                    m_ent["engines"][x["scraper"]] = {
                        "ok": True, "chars": len(x["markdown"]),
                        "content_hash": _content_hash(x["markdown"]),
                    }
                    result["_manifest"]["engines_by_url"].setdefault(
                        url, {}
                    )[x["scraper"]] = x["markdown"][:50000]
            result["_manifest"]["fetched"][url] = m_ent
```

`_crawl_page` 的 `except` 分支（L1672-1674）补失败记录：

```python
        except Exception as e:
            print(f"    [{resolved['canonical_name']}] {kind:8s} FAIL: {e}")
            result["_manifest"]["fetched"][url] = {
                "status": "failed", "engines": {},
                "fetched_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
            }
            result["_manifest"]["failures"].append({
                "competitor": resolved["canonical_name"],
                "url": url, "kind": kind.replace("*", ""),  # about?* → about
                "error": f"{type(e).__name__}: {e}",
            })
            return ""
```

3b. `_build_competitor_entry` 签名与返回值：

```python
def _build_competitor_entry(scraped: Dict, idx: int = 0) -> Tuple[Dict, List[str], List[Dict]]:
```

docstring 的 Returns 改为 `(entry, warnings, claims)`。函数末尾 `return entry, warnings` 前组装 claims：

```python
    # F8 claims:本竞品全部可验证断言(verify.py G1/G2 的消费对象)
    claims = []
    name = scraped["name"]

    def _claim(field, value, source_url, quote="", engine="", verified_by=None,
               from_cache=False, scraped_at=""):
        claims.append({
            "field": f"competitors[{idx}].{field}", "value": str(value),
            "source_url": source_url, "quote": (quote or "")[:120],
            "engine": engine, "verified_by": verified_by or [],
            "from_cache": from_cache, "scraped_at": scraped_at,
        })

    _purl = entry.get("pricing_source") or ""
    _psat = entry.get("pricing_scraped_at") or ""
    for j, t in enumerate(entry.get("pricing_tiers") or []):
        if t.get("price") and "未能提取" not in str(t.get("price")):
            _claim(f"pricing_tiers[{j}].price", t["price"],
                   t.get("source_url") or _purl, quote="", scraped_at=_psat,
                   verified_by=entry.get("pricing_engines") or [],
                   from_cache=bool(entry.get("pricing_from_cache")))
    for k, v in enumerate(entry.get("pricing_vote_detail") or []):
        _claim(f"pricing_vote_detail[{k}].line", v.get("line", ""),
               _purl, quote=v.get("line", ""), scraped_at=_psat,
               verified_by=v.get("engines") or [],
               from_cache=bool(entry.get("pricing_from_cache")))
    for key in ("gtm_evidence", "moat_evidence"):
        for k, ev in enumerate(entry.get(key) or []):
            _claim(f"{key}[{k}]", ev.get("name", ""), ev.get("source", ""),
                   quote=ev.get("quote", ""))
    for k, s in enumerate(entry.get("strengths") or []):
        m = re.search(r'官网原文:\s*"(.+?)"', s.get("evidence") or "")
        _claim(f"strengths[{k}]", s.get("point", ""), s.get("source", ""),
               quote=m.group(1) if m else "")
    if entry.get("tagline") and entry["tagline"] != "—":
        _claim("tagline", entry["tagline"], entry.get("tagline_source", ""),
               quote=entry["tagline"])
    for fld in ("founded", "headquarters", "team_size"):
        if entry.get(fld) and entry[fld] != "—":
            _claim(fld, entry[fld], entry.get(f"{fld}_source", ""),
                   quote=entry.get(f"{fld}_quote", ""))
    return entry, warnings, claims
```

3c. `crawl_and_build`（L2194-2268）：

签名：

```python
def crawl_and_build(names: List[str], topic: str, timeout: int = 30,
                    manifest_path=None, raw_dir=None) -> Dict:
```

循环内（原 L2219-2224）：

```python
        scraped = _scrape_one(info, timeout=timeout)
        entry, warnings, claims = _build_competitor_entry(scraped, idx=ci)
        competitors.append(entry)
        all_claims.extend(claims)
        for w in warnings:
            print(f"     ⚠ {w}")
            all_warnings.append(f"[{name}] {w}")
```

（`for ci, name in enumerate(found):` 替换原 `for name in found:`；循环前 `all_claims = []`；同时把每个 `scraped["_manifest"]` 的 fetched/engines_by_url/failures 合并进 `manifest` 累积器，竞品 home url 去重冲突时后写覆盖即可。）

函数末尾、`return analysis` 之前：

```python
    # F8 证据包落盘
    if manifest_path:
        manifest = {
            "run": {
                "topic": topic,
                "started_at": run_started_at,
                "finished_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                "pipeline_version": "2.0",
            },
            "fetched": manifest_fetched,
            "claims": all_claims,
            "failures": manifest_failures,
        }
        mpath = Path(manifest_path)
        mpath.parent.mkdir(parents=True, exist_ok=True)
        mpath.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        if raw_dir:
            rd = Path(raw_dir)
            rd.mkdir(parents=True, exist_ok=True)
            for name_key, engines_by_url in manifest_engines.items():
                safe = re.sub(r"[^\w.-]", "_", name_key)
                (rd / f"{safe}.engines.json").write_text(
                    json.dumps(engines_by_url, ensure_ascii=False, indent=1),
                    encoding="utf-8")
        print(f"💾 证据包: {mpath}")
```

（`run_started_at` 在函数开头记录；`manifest_fetched/manifest_engines/manifest_failures` 三个累积器在爬取循环里逐竞品 merge。）

3d. `main()`（L2419）：

```python
    out = Path(args.output)
    analysis = crawl_and_build(
        names, args.topic, timeout=args.timeout,
        manifest_path=out.parent / "claims-manifest.json",
        raw_dir=out.parent / "02-raw",
    )
```

（`out.parent.mkdir` 移到调用前。末尾 print 增加：`python3 verify.py --analysis {out} --manifest {out.parent}/claims-manifest.json --raw-dir {out.parent}/02-raw`）

3e. `scripts/run_youzi.py` `step2_crawl`（L35-80）重写：

```python
def step2_crawl(competitor_urls, raw_dir, max_chars=20000, timeout=60):
    """Step 2: 并行爬取 — 13 爬虫智能合并。

    F6:失败不再静默 —— 返回 (results, failures) 并落盘部分
    claims-manifest.json(fetched + failures),供 verify.py 与人工排查。
    """
    from adapters import scrape_smart

    raw_dir.mkdir(parents=True, exist_ok=True)
    results, failures = {}, []
    fetched = {}
    print(f"\n📡 Step 2 · 并行爬取 {len(competitor_urls)} 个竞品\n")
    for url in competitor_urls:
        name = (
            url.replace("https://", "").replace("http://", "")
            .replace("www.", "").split("/")[0].replace(".", "_")
        )
        t0 = time.time()
        try:
            r = scrape_smart(url, max_chars=max_chars, timeout=timeout)
        except Exception as e:
            print(f"  [{name}] ❌ {type(e).__name__}: {e}")
            failures.append({"competitor": name, "url": url,
                             "kind": "home", "error": f"{type(e).__name__}: {e}"})
            fetched[url] = {"status": "failed", "engines": {},
                            "fetched_at": time.strftime("%Y-%m-%d %H:%M UTC",
                                                        time.gmtime())}
            continue
        dt = time.time() - t0
        scrapers = r.get("scraper", "?").split("+") if r.get("scraper") else []
        stats = r.get("stats") or {}
        ok = stats.get("successful", 0)
        total = stats.get("total_scrapers", 0)
        md_len = len(r.get("markdown", ""))
        flag = "✓" if r.get("success") else "✗"
        print(
            f"  [{flag} {name}] {dt:5.1f}s | {ok}/{total} scrapers | "
            f"{md_len:>6} chars | "
            f"{','.join(scrapers[:4])}{'…' if len(scrapers) > 4 else ''}"
        )

        # 落盘
        out_file = raw_dir / f"{name}.md"
        header = (
            f"# Source: {url}\n"
            f"# Scrapers: {','.join(scrapers)}\n"
            f"# Time: {dt:.1f}s\n"
            f"# Success: {r.get('success')}\n\n"
        )
        out_file.write_text(header + r.get("markdown", ""), encoding="utf-8")
        results[name] = r

        # F8 引擎原文 + fetched 记录
        import hashlib as _hl
        engines_md, engines_meta = {}, {}
        for x in (r.get("all_results") or []):
            if x.get("scraper") and x.get("success") and x.get("markdown"):
                engines_md[x["scraper"]] = x["markdown"][:50000]
                engines_meta[x["scraper"]] = {
                    "ok": True, "chars": len(x["markdown"]),
                    "content_hash": _hl.sha256(
                        " ".join(x["markdown"].split()).encode("utf-8")
                    ).hexdigest()[:16],
                }
        (raw_dir / f"{name}.engines.json").write_text(
            json.dumps({url: engines_md}, ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        fetched[url] = {
            "status": "ok" if r.get("success") and r.get("markdown") else "failed",
            "engines": engines_meta,
            "fetched_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        }
        if fetched[url]["status"] == "failed":
            failures.append({"competitor": name, "url": url,
                             "kind": "home", "error": "all engines empty/failed"})

    manifest = {
        "run": {"topic": "", "started_at": time.strftime("%Y-%m-%d %H:%M UTC",
                                                         time.gmtime()),
                "pipeline_version": "2.0"},
        "fetched": fetched, "claims": [], "failures": failures,
    }
    (raw_dir.parent / "claims-manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    if failures:
        print(f"\n  ⚠ {len(failures)} 个 URL 爬取失败(已记录到 "
              f"{raw_dir.parent / 'claims-manifest.json'}):")
        for f in failures:
            print(f"    - [{f['competitor']}] {f['url']}: {f['error']}")
    return results, failures
```

`main()` 中调用点同步改（L175）：`step2_crawl(urls, raw_dir, ...)` 返回二元组 —— `_results, _failures = step2_crawl(...)`。

3f. `adapters/competitor_resolver.py` docstring L5-7 修正：

```python
支持两种模式:
1. 内置映射表（最可靠，无需网络）
2. 域名直通（名称形如域名/URL 时构造 base/features|pricing|docs 猜测路径）
```

同时 `_scrape_one` 结果透传 resolution 来源：`crawl_and_build` 循环里 entry 加 `"url_resolution": info.get("source", "")`，非 builtin 时打印警告行 `print(f"     ⚠ URL 为域名猜测(confidence={info.get('confidence')}),请核对")`。

- [ ] **Step 4: 运行确认通过**

Run: `python3 -m pytest tests/test_pipeline.py tests/test_pricing_extract.py -q` → PASS

- [ ] **Step 5: 全量回归 + 提交**

Run: `python3 -m pytest tests/ -q` → 全绿

```bash
git add scripts/crawl_competitors.py scripts/run_youzi.py adapters/competitor_resolver.py tests/test_pipeline.py
git commit -m "feat: 证据包落盘(claims-manifest + engines.json) + step2 失败清单 + resolver 漂移修正"
```

---

### Task 9: SKILL.md 双门禁 + e2e 真实验收 + fixture 冻结

**Files:**
- Create: `pytest.ini`、`tests/test_e2e_real.py`
- Modify: `SKILL.md`（Step 3 铁律、Step 5 双门禁）
- Test: `tests/test_e2e_offline.py`（fixture 冻结后的离线回归）

**Interfaces:**
- Consumes: Task 1-8 全部
- Produces: 验收产物 `tests/fixtures/e2e-<date>/`（真实运行沉淀）+ 可重复的发布验收流程

- [ ] **Step 1: pytest.ini + 网络标记**

创建 `pytest.ini`：

```ini
[pytest]
markers =
    network: real-network e2e (deselected by default; run with -m network)
addopts = -m "not network"
```

Run: `python3 -m pytest tests/ -q` → 确认默认不选 network 且全绿。

- [ ] **Step 2: 写 e2e 真实测试（默认跳过）**

创建 `tests/test_e2e_real.py`：

```python
#!/usr/bin/env python3
"""tests/test_e2e_real.py · 真实网络 e2e 验收(发布前手动跑)。

跑完整管线:爬取(WATI/respond.io/YCloud,内置表最熟的三家)→ 证据包
→ 渲染 → verify 离线门禁 + 网络门禁。全部硬门禁绿灯 = 生产级验收通过。

运行:
    python3 -m pytest tests/test_e2e_real.py -m network -v
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.mark.network
class TestE2EReal(unittest.TestCase):
    """真实数据验收:全管线 + verify --network。耗时 5-15 分钟。"""

    def test_full_pipeline_real_competitors(self):
        from verify import verify_analysis

        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "03-analysis.json"
            # Step 2+3: 爬取 + 证据包
            r = subprocess.run(
                [sys.executable, str(ROOT / "scripts" / "crawl_competitors.py"),
                 "--competitors", "wati,respond.io,ycloud",
                 "--topic", "WhatsApp BSP 赛道(生产验收)",
                 "--output", str(out)],
                cwd=ROOT, capture_output=True, text=True, timeout=1200,
            )
            self.assertEqual(
                r.returncode, 0, f"crawl failed:\n{r.stdout}\n{r.stderr}")

            analysis = json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(
                (out.parent / "claims-manifest.json").exists(),
                "证据包未落盘")
            self.assertGreaterEqual(len(analysis["competitors"]), 2,
                                    "真实爬取至少 2 家成功")

            # Step 4: 渲染
            html = out.parent / "report.html"
            r2 = subprocess.run(
                [sys.executable, str(ROOT / "render.py"),
                 "--input", str(out), "--output", str(html)],
                cwd=ROOT, capture_output=True, text=True, timeout=300,
            )
            self.assertIn(r2.returncode, (0, 2), r2.stdout + r2.stderr)

            # Step 5: verify 离线门禁(必须全绿)
            rep = verify_analysis(
                out, out.parent / "claims-manifest.json", out.parent / "02-raw",
                report_path=out.parent / "verify-report.json",
            )
            self.assertTrue(
                rep["passed"],
                "离线硬门禁失败:\n" + json.dumps(
                    rep["violations"], ensure_ascii=False, indent=1))

            # 网络门禁:N1 可达性(真实回访被引用 URL)
            rep_net = verify_analysis(
                out, out.parent / "claims-manifest.json", out.parent / "02-raw",
                network=True, sample=10,
            )
            self.assertFalse(
                [v for v in rep_net["violations"] if v["gate"] == "N1"],
                "N1 死链:\n" + json.dumps(
                    [v for v in rep_net["violations"] if v["gate"] == "N1"],
                    ensure_ascii=False, indent=1))
```

Run: `python3 -m pytest tests/ -q` → 全绿（network 默认跳过）。

- [ ] **Step 3: SKILL.md 双门禁更新**

`SKILL.md` Step 3 铁律列表（第 1 条后）追加一条：

```markdown
7. **写 claim**:你从 02-raw 提取/改写的每个字段,同步追加到 `OUT_DIR/claims-manifest.json` 的 `claims` 数组(schema 见 docs/superpowers/specs/2026-08-26-production-quality-loop-design.md §3.1)—— verify.py 会拒收任何没有抓取记录支撑的 source_url 和 grep 不到的 quote
```

Step 5 标题改为「自检 + 验证(双门禁)」，在 render.py 自检说明后追加：

```markdown
**证据硬门禁(verify.py,与渲染自检同级 —— 不过 = 不交付):**

```bash
python3 ~/.claude/skills/youzi/verify.py \
  --input "$OUT_DIR/03-analysis.json" \
  --manifest "$OUT_DIR/claims-manifest.json" \
  --raw-dir "$OUT_DIR/02-raw" --json "$OUT_DIR/verify-report.json"
```

- exit 2 → 读 verify-report.json 的 `{gate, field, hint}`,修 03-analysis.json 或重爬,再验 —— **修复回路,不是绕过**
- 网络复核(可选,慢):加 `--network --sample 10`
- 新发现的坏数据形状 → 冻结成离线 fixture 进 tests/(延续 test_pricing_extract.py 模式)
```

（注意：SKILL.md 中 verify.py 的参数名以实际实现为准 —— `--analysis` 不是 `--input`，上面 CLI 示例写 `--analysis`。）

「重要原则」追加一行：`- **双门禁交付**：render.py exit 0 且 verify.py exit 0 才算交付;verify-report.json 是修复回路的输入,不是可选日志。`

- [ ] **Step 4: 跑真实 e2e 验收**

Run: `python3 -m pytest tests/test_e2e_real.py -m network -v`（timeout 放宽）

期望:PASS。若 FAIL:
- G1/G2 红 → 检查 Task 8 claims 产出与 engines.json 是否遗漏字段(修 crawl 侧)
- G3 红 → 检查 manifest.fetched 的 content_hash 是否写入
- N1 红(反爬 403)→ 确认该 URL 确实被 claim 引用;若为真死链修数据,若为反爬误伤在 fetch_url UA/重试上调整
- 循环「修 → 重跑 e2e」直到绿灯(这正是闭环本身)。

- [ ] **Step 5: 冻结真实 fixture 为离线回归**

e2e 通过后,把该次运行产物拷入仓库:

```bash
mkdir -p tests/fixtures/e2e-2026-08-26
cp /tmp/<该次e2e的tmp目录>/03-analysis.json tests/fixtures/e2e-2026-08-26/
cp /tmp/<该次e2e的tmp目录>/claims-manifest.json tests/fixtures/e2e-2026-08-26/
cp -r /tmp/<该次e2e的tmp目录>/02-raw tests/fixtures/e2e-2026-08-26/
```

（e2e 测试代码同步加一段:tmp 目录固定为 `/tmp/youzi-e2e-acceptance`(不再 TemporaryDirectory),便于此步拷贝 —— 修改 `test_full_pipeline_real_competitors` 开头为 `d = "/tmp/youzi-e2e-acceptance"; Path(d).mkdir(parents=True, exist_ok=True)`,并清理旧目录。）

创建 `tests/test_e2e_offline.py`：

```python
#!/usr/bin/env python3
"""tests/test_e2e_offline.py · 冻结的真实运行回归。

用 e2e 验收当天的真实产物(analysis + manifest + engines.json)离线重放
verify 全部门禁 —— 新 bad shape 修复后不得让已验收数据退化。
"""

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from verify import verify_analysis  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "e2e-2026-08-26"


@unittest.skipUnless((FIXTURE / "claims-manifest.json").exists(),
                     "fixture 未冻结(先跑 -m network 验收)")
class TestFrozenE2E(unittest.TestCase):

    def test_frozen_real_run_passes_all_gates(self):
        rep = verify_analysis(
            FIXTURE / "03-analysis.json",
            FIXTURE / "claims-manifest.json",
            FIXTURE / "02-raw",
        )
        self.assertTrue(
            rep["passed"],
            f"冻结数据回放失败:\n{rep['violations']}")
        self.assertGreaterEqual(rep["summary"]["claims_checked"], 5,
                                "真实运行至少检查 5 条 claim")
```

Run: `python3 -m pytest tests/test_e2e_offline.py -q` → PASS

- [ ] **Step 6: 最终全量验收 + 提交**

Run:
```bash
python3 -m pytest tests/ -q            # 全部离线(含冻结 fixture)
python3 -m pytest tests/ -m network -q # 真实网络(可选重放)
```

```bash
git add pytest.ini SKILL.md tests/test_e2e_real.py tests/test_e2e_offline.py tests/fixtures/e2e-2026-08-26
git commit -m "feat: 双门禁交付(SKILL.md) + 真实e2e验收 + fixture冻结回归"
```

---

## Self-Review 记录

1. **Spec coverage 对照**：
   - §3.1 证据包契约 → Task 8（manifest + engines.json）✓
   - §3.2 G1-G6 → Task 2/3/4 ✓；N1-N2 → Task 5 ✓；verify-report.json → Task 1（report_path）✓；退出码 → Task 1 ✓
   - §3.3 F1→Task 6、F2→Task 6、F3→Task 7、F4→Task 7、F5→Task 6、F6→Task 8、F7→Task 8、F8→Task 8 ✓
   - §3.4 闭环运行 → Task 9（SKILL.md Step 5 修复回路）✓
   - §4 测试计划 → tests/test_verify.py（Task 1-5）、test_pipeline/test_pricing_extract 扩展（Task 6-8）、test_e2e_real/offline（Task 9）✓
   - §5 验收标准 → Task 9 Step 4-6（真实 3 家 + verify 全绿 + fixture 冻结 + 根因回归）✓
2. **Placeholder scan**：无 TBD/TODO；Task 8 Step 1 的两版 `TestRunYouziFailures` 草稿已收敛为最终版（mock `adapters.scrape_smart`）。
3. **Type consistency**：`_build_competitor_entry` 三元组返回在 Task 7（仍二元）→ Task 8 改三元 — Task 7 的测试用 `entry, _ = _build_competitor_entry(scraped)`，Task 8 改签名后需同步更新 Task 7 测试解包为 `entry, _, _ =`（**Task 8 Step 3 实施时注意：tests 里所有 `_build_competitor_entry` 解包处一并改三元组**）。`verify_analysis` 签名全程一致；`gates.run_all` / `network_gates.run_all` 签名一致。
