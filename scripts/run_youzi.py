#!/usr/bin/env python3
"""
youzi · 一站式竞品分析 runner

按 SKILL.md 的 6 步管线串起来:
  Step 1 发现竞品     — （本 runner 跳过；分析 JSON 由用户/上游提供）
  Step 2 深度爬取     — 调用 scrape_smart() 抓 competitor URL（13 爬虫并行）
  Step 3 结构化分析   — 读取 03-analysis.json（已含 13 字段提取结果）
  Step 4 渲染 HTML    — 调用 render.py 输出精美报告
  Step 5 自检         — render.py 内置 7 项严格检查
  Step 6 交付         — 打印报告路径 + 1 段 TL;DR

用法：
    python3 scripts/run_youzi.py \
        --topic "基于 WhatsApp 的广告运营平台" \
        --analysis examples/whatsapp-advertising-demo.json \
        --output /tmp/youzi-out/whatsapp-final/report.html

也支持：只爬取 + 不渲染（用于 Step 2 缓存场景）：
    python3 scripts/run_youzi.py --crawl-only \
        --competitors "https://www.wati.io,https://respond.io,https://manychat.com" \
        --raw-dir /tmp/youzi-out/whatsapp/02-raw
"""

import argparse
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


def step2_crawl(competitor_urls, raw_dir, max_chars=20000, timeout=60):
    """Step 2: 并行爬取 — 13 爬虫智能合并"""
    from adapters import scrape_smart

    raw_dir.mkdir(parents=True, exist_ok=True)
    results = {}
    print(f"\n📡 Step 2 · 并行爬取 {len(competitor_urls)} 个竞品\n")
    for url in competitor_urls:
        name = (
            url.replace("https://", "")
            .replace("http://", "")
            .replace("www.", "")
            .split("/")[0]
            .replace(".", "_")
        )
        t0 = time.time()
        try:
            r = scrape_smart(url, max_chars=max_chars, timeout=timeout)
        except Exception as e:
            print(f"  [{name}] ❌ {type(e).__name__}: {e}")
            continue
        dt = time.time() - t0
        scrapers = r.get("scraper", "?").split("+") if r.get("scraper") else []
        stats = r.get("stats") or {}
        ok = stats.get("successful", 0)
        total = stats.get("total_scrapers", 0)
        md_len = len(r.get("markdown", ""))
        flag = "✓" if r.get("success") else "✗"
        print(
            f"  [{flag} {name}] {dt:5.1f}s | "
            f"{ok}/{total} scrapers | {md_len:>6} chars | "
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

    return results


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


def step6_deliver(analysis, output_path):
    """Step 6: 打印交付摘要"""
    data = json.loads(Path(analysis).read_text(encoding="utf-8"))
    print("\n🚀 Step 6 · 交付\n")
    print(f"  📊 主题: {data['topic']}")
    print(
        f"  🏷  分析竞品数: {data.get('competitor_count', len(data.get('competitors', [])))}"
    )
    print(
        f"  💡 颠覆性机会: {data.get('opportunity_count', len(data.get('opportunities', [])))}"
    )
    print(f"  📄 报告路径: {output_path}")
    size_kb = Path(output_path).stat().st_size / 1024
    print(f"  📦 文件大小: {size_kb:.1f} KB")

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
            f"\n  🎯 最大机会: {top_opp['title']} (disrupt_score={top_opp.get('disrupt_score', '?')})"
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
    ap.add_argument("--competitors", help="爬取的 URL 列表（逗号分隔）")
    ap.add_argument("--raw-dir", help="Step 2 原始数据落盘目录（默认: ./02-raw/）")
    ap.add_argument("--max-chars", type=int, default=20000, help="单页最大字符数")
    ap.add_argument("--timeout", type=int, default=60, help="单爬虫超时秒")
    args = ap.parse_args()

    print(f"\n{'=' * 60}")
    print("  🍊 youzi · 竞品颠覆性分析")
    print(f"  📌 主题: {args.topic}")
    print(f"  📂 当前目录: {Path.cwd()}")
    print(f"{'=' * 60}")

    # Step 2: 爬取（如指定）
    if args.competitors:
        urls = [u.strip() for u in args.competitors.split(",") if u.strip()]
        # 默认在当前目录下创建 02-raw
        raw_dir = Path(args.raw_dir) if args.raw_dir else Path.cwd() / "02-raw"
        step2_crawl(urls, raw_dir, max_chars=args.max_chars, timeout=args.timeout)
        if args.crawl_only:
            print(f"\n✅ Step 2 完成，原始数据落盘到: {raw_dir}")
            return

    # Step 4: 渲染（如指定 analysis）
    if not args.analysis:
        print("\n⚠️  需要 --analysis 才会渲染最终报告")
        print("   示例: --analysis examples/whatsapp-advertising-demo.json")
        return

    analysis_path = Path(args.analysis).expanduser().resolve()
    # 默认输出到当前目录
    if args.output:
        output_path = Path(args.output).expanduser().resolve()
    else:
        safe_topic = "".join(c if c.isalnum() or c in "-_" else "_" for c in args.topic)
        output_path = (Path.cwd() / f"{safe_topic}-report.html").resolve()
    if not analysis_path.exists():
        print(f"\n❌ 分析 JSON 不存在: {analysis_path}", file=sys.stderr)
        sys.exit(1)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    step4_render(analysis_path, output_path, args.template)

    # Step 6: 交付
    step6_deliver(analysis_path, output_path)


if __name__ == "__main__":
    main()
