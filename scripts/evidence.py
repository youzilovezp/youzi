#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""evidence.py · Step 3 证据助手 —— 给 LLM 分析阶段的工具支撑。

诞生背景(2026-08-30 实测摩擦):手写 03-analysis.json 时,LLM 需要反复
grep engines.json 找引文,且容易踩三类坑 —— ①改写偏差 ②markdown 粗体/
弯引号不可逐字匹配 ③锚点选到域名根/定价页被 G7 拦。本工具把这三件事
产品化:

  digest  每竞品的可读证据摘要(G7 合规锚点 × grep 安全引文 × 定价票上下文)
  quote   在指定页的引擎原文里搜模式,返回【逐字可 grep】的原文行

铁律不变:只做取证整理,不做语义提取(那是 LLM 的活)。

用法:
    python3 scripts/evidence.py digest <OUT_DIR> [--chars 1500]
    python3 scripts/evidence.py quote <OUT_DIR> <URL> <PATTERN>
"""

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gates import _quote_grep, _weak_anchor  # noqa: E402
from pricing_tokens import PRICE_TOKEN_RX  # noqa: E402
from scripts import fetch as fetch_mod  # noqa: E402

# trafilatura 输出的 YAML front-matter 行(证据摘要要剥掉)
_FRONT_MATTER_RX = re.compile(
    r"^(---|title:|url:|hostname:|description:|sitename:|date:|tags:|published:)", re.I
)
# 语言切换器/纯导航特征:一行内 ≥3 种文字系统切换,或全是短词堆叠
_LANG_SWITCH_RX = re.compile(r"[一-鿿][A-Za-z]|[A-Za-z][一-鿿]")
# 句子质量:以标点收尾,或标题行
_SENT_END_RX = re.compile(r"[.。!?;:]$")


def load_evidence(out_dir: Path):
    """→ (manifest, engine_index) —— 与 verify.build_engine_index 同构。"""
    manifest = json.loads(
        (out_dir / "claims-manifest.json").read_text(encoding="utf-8")
    )
    engine_index: dict = {}
    for f in sorted((out_dir / "02-raw").glob("*.engines.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        for url, engines in (d or {}).items():
            engine_index.setdefault(url, {}).update(engines or {})
    return manifest, engine_index


def _clean_lines(md: str) -> list[str]:
    """剥 front-matter/链接语法/空行,返回规范化行列表。"""
    out: list[str] = []
    for ln in (md or "").split("\n"):
        s = " ".join(ln.split())
        if not s or _FRONT_MATTER_RX.match(s):
            continue
        s = re.sub(r"!\[[^\]]*\]\([^)]+\)", "", s)
        s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
        s = s.lstrip("#> ").strip()
        if s:
            out.append(s)
    return out


def safe_quotes(url: str, engine_index: dict, n: int = 3, lo: int = 35, hi: int = 130):
    """该 URL 上【逐字 grep 安全】的引文候选(与 verify G2 同款归一化验证)。

    过滤:链接语法行(归一化后不可稳定匹配)、语言切换器行、价格行
    (留给定价板块)、front-matter。"""
    got, seen = [], set()
    for md in (engine_index.get(url) or {}).values():
        for s in _clean_lines(md):
            if not (lo <= len(s) <= hi) or s in seen:
                continue
            if "http" in s or "](" in s:
                continue
            if len(_LANG_SWITCH_RX.findall(s)) >= 2:
                continue
            if PRICE_TOKEN_RX.search(s):
                continue
            if len(re.findall(r"[一-鿿 a-zA-Z0-9]", s)) < len(s) * 0.7:
                continue  # 符号/代码为主的行
            if not (_SENT_END_RX.search(s) or s.startswith("#")):
                # 无标点收尾且非标题:多为截断的 UI 碎片,跳过
                continue
            if _quote_grep(s, url, engine_index):
                seen.add(s)
                got.append(s)
            if len(got) >= n:
                return got
    return got


def pricing_digest(comp_urls: dict, manifest: dict, engine_index: dict):
    """定价票摘要:交叉验证价 × 含价原文行(档位名上下文)。"""
    fetched = manifest.get("fetched") or {}
    purl = None
    for u in comp_urls:
        ent = fetched.get(u) or {}
        kinds = ent.get("kinds") or ([ent.get("kind")] if ent.get("kind") else [])
        if "pricing" in kinds:
            purl = u
            break
    if not purl:
        return None
    votes = [
        v
        for v in fetch_mod.vote_price_lines(
            [
                {"success": True, "scraper": e, "markdown": m}
                for e, m in (engine_index.get(purl) or {}).items()
            ]
        )
        if v["independent_votes"] >= 2
    ]
    lines = []
    for v in votes[:6]:
        ctx = None
        for md in (engine_index.get(purl) or {}).values():
            for s in _clean_lines(md):
                if v["token"] in s and "$" not in s.replace(v["token"], "", 1):
                    ctx = s
                    break
                if v["token"] in s:
                    ctx = s
                    break
            if ctx:
                break
        lines.append(
            {
                "price": v["token"],
                "engines": v["engines"],
                "votes": v["independent_votes"],
                "context_line": ctx,
            }
        )
    ent = fetched.get(purl) or {}
    return {
        "url": purl,
        "verified_votes": lines,
        "engines_with_hash": list((ent.get("engines") or {}).keys()),
        "scraped_at": ent.get("fetched_at"),
    }


def digest(out_dir: Path, chars: int = 1500) -> str:
    """每竞品 × 页面类型 → 可读摘要 + 安全引文 + 锚点分级。"""
    manifest, engine_index = load_evidence(out_dir)
    fetched = manifest.get("fetched") or {}
    parts = [
        f"# 证据摘要 · {out_dir}",
        "",
        "> **写 03-analysis.json 前的契约速查(踩坑实录 2026-08-30,详见",
        "> references/analysis-framework.md)**:",
        "> - `pricing_tiers.billing_period` 只用三通道:`/mo`(月价)·`billed`(年付结算月价)·`/yr`(年总价);",
        ">   Free/Custom/买断档**不带周期**(price 写 $0/免费 → Free 档;写描述 → 联系销售档)。",
        ">   月付+年付同公示时**同 name 写两条**,render 自动配对双栏+省%徽章。",
        "> - `opportunities` 的 `target_users`/`differentiators`/`validation` 都是**数组**;",
        ">   补 `pitch`(一句话)/`moat`(壁垒)/`evidence_urls`(本轮实抓 URL,出角标)。",
        "> - `gaps` 用 `{gap, evidence, severity, source}`。",
        "> - `feature_catalog` 用**跨厂商统一功能名**(团队共享收件箱/AI 客服代理/REST API/聊天机器人/",
        ">   自动化营销群发/语音通话能力/客户数据平台/电商变现…),矩阵才对齐;desc 必须逐字取自 source 页。",
        "> - 引文直接复制下方「安全引文」(已过 G2 同款回查);锚点只用 ✓ 标记的页。",
        "",
    ]

    for fp in sorted((out_dir / "02-raw").glob("*.engines.json")):
        comp = fp.stem.removesuffix(".engines")
        doc = json.loads(fp.read_text(encoding="utf-8"))
        parts.append(f"## {comp}")
        pr = pricing_digest(doc, manifest, engine_index)
        if pr:
            parts.append(
                f"### 定价({pr['url']},验证引擎 {len(pr['engines_with_hash'])})"
            )
            for v in pr["verified_votes"]:
                ctx = (v["context_line"] or "")[:100]
                parts.append(f"- **{v['price']}** ×{v['votes']} 引擎 {v['engines']}")
                if ctx:
                    parts.append(f"  - 上下文: `{ctx}`")
            parts.append("")
        for url in doc:
            ent = fetched.get(url) or {}
            kinds = ent.get("kinds") or ([ent.get("kind")] if ent.get("kind") else [])
            if not kinds:
                continue
            weak = _weak_anchor(url)
            anchor = (
                "⚠域名根(G7禁)"
                if weak == "root"
                else ("⚠定价页(仅定价语义可锚)" if weak == "pricing" else "✓G7合规锚点")
            )
            engines = engine_index.get(url) or {}
            md = next(
                (
                    engines[e]
                    for e in ("trafilatura", "playwright", "jina")
                    if engines.get(e)
                ),
                "",
            )
            body = "\n".join(_clean_lines(md))[:chars]
            parts.append(f"### {'/'.join(kinds)} · {url} [{anchor}]")
            parts.append(body)
            qs = safe_quotes(url, engine_index, n=3)
            if qs:
                parts.append("**安全引文(grep 已验证):**")
                parts.extend(f"- `{q}`" for q in qs)
            parts.append("")
    return "\n".join(parts)


def quote_search(out_dir: Path, url: str, pattern: str) -> str:
    """在指定 URL 的引擎原文里搜模式,返回逐字 grep 安全的命中行。"""
    _, engine_index = load_evidence(out_dir)
    rx = re.compile(pattern, re.I)
    hits = []
    for md in (engine_index.get(url) or {}).values():
        for s in _clean_lines(md):
            if rx.search(s) and _quote_grep(s, url, engine_index) and s not in hits:
                hits.append(s)
    return "\n".join(f"`{h}`" for h in hits[:20]) or "(无命中)"


def main() -> int:
    ap = argparse.ArgumentParser(description="youzi · Step 3 证据助手")
    sub = ap.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("digest", help="每竞品证据摘要(锚点分级+安全引文+定价票)")
    d.add_argument("out_dir")
    d.add_argument("--chars", type=int, default=1500)
    q = sub.add_parser("quote", help="页内模式搜索(返回逐字可 grep 的行)")
    q.add_argument("out_dir")
    q.add_argument("url")
    q.add_argument("pattern")
    a = ap.parse_args()
    out_dir = Path(a.out_dir)
    if a.cmd == "digest":
        print(digest(out_dir, a.chars))
    else:
        print(quote_search(out_dir, a.url, a.pattern))
    return 0


if __name__ == "__main__":
    sys.exit(main())
