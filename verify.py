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
    python3 verify.py --analysis OUT/03-analysis.json \
                      --manifest OUT/claims-manifest.json \
                      --raw-dir OUT/02-raw [--network] [--sample 10] \
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
    import gates  # 延迟 import,门禁函数在各自任务中注册(ROOT 需在 sys.path)
    gates.run_all(analysis, manifest, engine_index, rep)

    # Layer 2 网络门禁(Task 5 填充)
    if network:
        import network_gates
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
