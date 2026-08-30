#!/usr/bin/env python3
"""
youzi · 一站式竞品分析 runner

按 SKILL.md 的管线串起来(双门禁交付:render.py exit 0 且 verify.py exit 0 才算交付):
  Step 1 发现竞品     — (本 runner 跳过;分析 JSON 由用户/上游提供)
  Step 2 深度爬取     — 委托 scripts/fetch.py 的 fetch_competitor(预算/升级梯/充分性/台账)
  Step 3 结构化分析   — 读取 03-analysis.json(已含 13 字段提取结果)
  Step 4 渲染 HTML    — 调用 render.py 输出精美报告(内置自检)
  Step 5 证据硬门禁   — 调用 verify.py 的 verify_analysis(G1-G7),不过 = 不交付
  Step 6 交付         — 打印报告路径 + 1 段 TL;DR

用法：
    python3 scripts/run_youzi.py \
        --topic "基于 WhatsApp 的广告运营平台" \
        --analysis OUT/03-analysis.json \
        --output OUT/report.html

也支持：只爬取 + 不渲染（用于 Step 2 缓存场景）：
    python3 scripts/run_youzi.py --topic "..." --crawl-only \
        --competitors "wati,respond.io,manychat" \
        --raw-dir /tmp/youzi-out/whatsapp/02-raw
"""

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def _out_dir_for(raw_dir: Path) -> Path:
    """fetch.py 落盘布局是 OUT_DIR/02-raw + OUT_DIR/claims-manifest.json;
    --raw-dir 语义是 02-raw 目录本身,据此推导 OUT_DIR。"""
    return raw_dir.parent if raw_dir.name == "02-raw" else raw_dir


def step2_fetch(competitors, out_dir, topic="", budget_s=None):
    """Step 2: 取证爬取 — 唯一实现是 fetch.fetch_competitor(禁双轨)。

    fetch 侧内建:URL 发现 + 引擎路由 + 定价交叉验证升级梯 + 预算控制 +
    充分性判断 + claims-manifest.json 台账(fetched 带 kind)+ 引擎原文。

    2026-08-30 修复:竞品级并行(与 fetch.py CLI 同款 3 并发)。历史实现
    串行 for 循环 —— SKILL.md 宣称的竞品级并行在 runner 主路径上不存在
    (5 竞品实测 150s+ → 并行后 ~60s)。单竞品崩溃降级为诚实失败条目。
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    from scripts import fetch

    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n📡 Step 2 · 取证爬取 {len(competitors)} 个竞品(fetch.py 统一入口)\n")

    def _run(comp):
        t0 = time.time()
        r = fetch.fetch_competitor(comp, out_dir, budget_s=budget_s, topic=topic)
        # 返回输入名做排序键(resolver 归一名与输入名不一致时排序会失效)
        return comp, r, time.time() - t0

    collected = []
    with ThreadPoolExecutor(
        max_workers=min(fetch._COMPETITOR_CONCURRENCY, len(competitors))
    ) as ex:
        futs = {ex.submit(_run, comp): comp for comp in competitors}
        for fut in as_completed(futs):
            comp = futs[fut]
            try:
                _, r, dt = fut.result()
            except Exception as e:
                r, dt = (
                    {
                        "name": comp,
                        "url": "",
                        "pages": {},
                        "failures": [
                            {
                                "competitor": comp,
                                "url": "",
                                "kind": "crash",
                                "error": f"{type(e).__name__}: {e}"[:200],
                            }
                        ],
                    },
                    0.0,
                )
            insuff = [k for k, v in r["pages"].items() if not v["sufficient"]]
            ok = bool(r["pages"])
            print(
                f"  [{'✓' if ok else '✗'} {r['name']}] {dt:5.1f}s | "
                f"{len(r['pages'])} pages | 不充分: {','.join(insuff) or '无'} | "
                f"failures: {len(r['failures'])}"
            )
            for hint in (r.get("lessons_hints") or [])[:2]:
                print(f"    💡 lesson: {hint[:96]}")
            collected.append((comp, r))
    # 保持输入顺序(打印是完成序,返回值按输入序)
    order = {c: i for i, c in enumerate(competitors)}
    collected.sort(key=lambda t: order.get(t[0], len(competitors)))
    return [r for _, r in collected]


def step4_render(analysis_path, output_path, template_path=None):
    """Step 4: 渲染 HTML"""
    import subprocess

    cmd = [
        "python3",
        str(REPO / "render.py"),
        "--input",
        str(analysis_path),
        "--output",
        str(output_path),
    ]
    if template_path:
        cmd += ["--template", str(template_path)]
    print(f"\n🎨 Step 4 · 渲染 HTML\n  $ {' '.join(cmd[1:])}\n")
    r = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True)
    print(r.stdout)
    if r.returncode == 2:
        # 自检失败(如 0 来源证据)但 HTML 已写出:不中断流程,
        # 报告内有显式警告横幅,提醒重跑 Step 2/3 补证据
        print(
            "  ⚠ 渲染自检未通过(见上方 ✗ 项)—— HTML 已生成,但带数据可信度警告。\n"
            "    通常是 03-analysis.json 缺 source/quote 证据字段,请重跑 Step 3。",
            file=sys.stderr,
        )
    elif r.returncode != 0:
        print(r.stderr, file=sys.stderr)
        raise RuntimeError("render.py failed")


def step5_verify(analysis_path, manifest_path, raw_dir, report_path=None):
    """Step 5: 证据硬门禁 — verify.py 的 G1-G7(直接 import,非 subprocess)。

    返回 verify_analysis 的结果 dict;输入缺失/损坏(verify exit 1 语义)
    归一化为 passed=False,由调用方决定不交付。
    """
    from verify import verify_analysis

    print("\n🛡  Step 5 · 证据硬门禁(verify.py G1-G7)\n")
    try:
        result = verify_analysis(
            analysis_path, manifest_path, raw_dir, report_path=report_path
        )
    except SystemExit as e:  # verify exit 1:manifest/analysis 缺失或损坏
        print(f"  ✗ verify 输入缺失/损坏(exit {e.code})", file=sys.stderr)
        return {
            "passed": False,
            "exit_code": int(e.code or 1),
            "summary": {},
            "violations": [],
            "warnings": [],
        }
    for v in result["violations"]:
        print(f"  ✗ [{v['gate']}] {v['field']}: {v['detail']}")
        print(f"      → 修复: {v['hint']}")
    for w in result["warnings"]:
        print(f"  ⚠ [{w['gate']}] {w['field']}: {w['detail']}")
    s = result["summary"]
    verdict = "✓ 全部硬门禁通过" if result["passed"] else "✗ 硬门禁失败"
    print(
        f"\n  {verdict} — 硬失败 {s.get('hard_failed', 0)} · "
        f"警告 {s.get('warnings', 0)} · "
        f"claims {s.get('claims_checked', 0)} · urls {s.get('urls_checked', 0)}"
    )
    return result


def step6_deliver(analysis, output_path):
    """Step 6: 打印交付摘要"""
    data = json.loads(Path(analysis).read_text(encoding="utf-8"))
    print("\n🚀 Step 6 · 交付\n")
    print(f"  📊 主题: {data.get('topic', '(未提供主题)')}")
    print(
        f"  🏷  分析竞品数: {data.get('competitor_count', len(data.get('competitors', [])))}"
    )
    print(
        f"  💡 颠覆性机会: {data.get('opportunity_count', len(data.get('opportunities', [])))}"
    )
    print(f"  📄 报告路径: {output_path}")
    # render rc==2 时"HTML 已写出"是跨进程隐式契约,不能盲信 —— 先 exists 再 stat
    op = Path(output_path)
    if op.exists():
        size_kb = op.stat().st_size / 1024
        print(f"  📦 文件大小: {size_kb:.1f} KB")
    else:
        print("  ⚠ 报告文件不存在(render 未写出?)", file=sys.stderr)

    # 1 段 TL;DR
    es = data.get("executive_summary", "")
    if es:
        summary = es if len(es) <= 200 else es[:197] + "..."
        print(f"\n  ★ TL;DR: {summary}")

    # 最大机会
    opps = data.get("opportunities", [])
    if opps:
        top_opp = max(
            opps,
            key=lambda o: o.get("disrupt_score", 0)
            if isinstance(o.get("disrupt_score"), (int, float))
            else 0,
        )
        print(
            f"\n  🎯 最大机会: {top_opp.get('title', '(无标题)')} "
            f"(disrupt_score={top_opp.get('disrupt_score', '?')})"
        )
        print(f"     → {top_opp.get('inspiration', '')[:120]}")


def main():
    ap = argparse.ArgumentParser(description="youzi · 一站式竞品分析 runner")
    ap.add_argument("--topic", required=True, help="分析主题")
    ap.add_argument("--analysis", help="Step 3 的 13 字段分析 JSON")
    ap.add_argument(
        "--output", help="最终报告 HTML 路径（默认: ./<topic>-report.html 当前目录）"
    )
    ap.add_argument("--template", help="可选: 自定义模板路径")
    ap.add_argument("--crawl-only", action="store_true", help="只跑 Step 2 爬取")
    ap.add_argument("--competitors", help="爬取的竞品名/域名/URL 列表（逗号分隔）")
    ap.add_argument("--raw-dir", help="Step 2 原始数据落盘目录（默认: ./02-raw/）")
    ap.add_argument(
        "--budget",
        type=float,
        default=None,
        help="每竞品墙钟预算秒数（默认 300,由 fetch.py 管理）",
    )
    # 2026-08-30 清理:删除 --max-chars/--timeout —— 解析后从未被消费
    # (grep 证实),留着只会误导使用者以为能调引擎行为。
    args = ap.parse_args()

    print(f"\n{'=' * 60}")
    print("  🍊 youzi · 竞品颠覆性分析")
    print(f"  📌 主题: {args.topic}")
    print(f"  📂 当前目录: {Path.cwd()}")
    print(f"{'=' * 60}")

    # Step 2: 爬取（如指定）
    manifest_path = raw_dir = None
    if args.competitors:
        competitors = [u.strip() for u in args.competitors.split(",") if u.strip()]
        # 默认在当前目录下创建 02-raw
        raw_dir = Path(args.raw_dir) if args.raw_dir else Path.cwd() / "02-raw"
        out_dir = _out_dir_for(raw_dir)
        step2_fetch(competitors, out_dir, topic=args.topic, budget_s=args.budget)
        if args.crawl_only:
            print(f"\n✅ Step 2 完成，原始数据落盘到: {out_dir / '02-raw'}")
            return 0
        manifest_path = out_dir / "claims-manifest.json"
        raw_dir = out_dir / "02-raw"

    # Step 4: 渲染（如指定 analysis）
    if not args.analysis:
        print("\n⚠️  需要 --analysis 才会渲染最终报告")
        print("   示例: --analysis OUT/03-analysis.json")
        return 0

    analysis_path = Path(args.analysis).expanduser().resolve()
    # 默认输出到当前目录
    if args.output:
        output_path = Path(args.output).expanduser().resolve()
    else:
        safe_topic = "".join(c if c.isalnum() or c in "-_" else "_" for c in args.topic)
        output_path = (Path.cwd() / f"{safe_topic}-report.html").resolve()
    if not analysis_path.exists():
        print(f"\n❌ 分析 JSON 不存在: {analysis_path}", file=sys.stderr)
        return 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    step4_render(analysis_path, output_path, args.template)

    # Step 5: 证据硬门禁(render 之后、deliver 之前;不过 = 不交付)
    if manifest_path is None:
        # 未在本轮爬取:按 OUT_DIR 布局在 analysis 旁找证据包
        manifest_path = analysis_path.parent / "claims-manifest.json"
        raw_dir = analysis_path.parent / "02-raw"
    verify_report = step5_verify(
        analysis_path,
        manifest_path,
        raw_dir,
        report_path=output_path.parent / "verify-report.json",
    )
    if not verify_report["passed"]:
        print(
            "\n❌ verify 硬门禁未通过 —— 按上方 {gate, field, hint} 修 "
            "03-analysis.json 或重爬 Step 2 后重跑;本次不交付。",
            file=sys.stderr,
        )
        return int(verify_report.get("exit_code") or 2)

    # Step 6: 交付
    step6_deliver(analysis_path, output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
