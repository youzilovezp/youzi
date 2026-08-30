#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""youzi · 情报自我审计器(audit.py) —— 全面性 × 准确性 × 反哺。

在两个时点运行:
  Step 2.5(爬取后、分析前): 审计抓取覆盖面,产出 next_actions 驱动同会话补爬
  Step 5(交付前,带 --analysis): 审计最终分析质量,写 04-audit.json + 经验沉淀

三层审计:
  L1 覆盖率   页面类型齐全度 / 定价深度(月付·年付·Free·Custom·价格token数)
              / 字段完整度(需 --analysis)
  L2 准确性   每个分析价格值的跨引擎投票 / quote 逐字回查(抽样)
              / pricing_verified 与引擎独立性一致性
  L3 反哺     next_actions(可执行补采动作) + NEXT_CRAWL(下次运行建议)
              + storage/intel-lessons.json 跨会话经验沉淀(自我进化闭环)

状态分类(核心设计 —— 区分「没爬到」与「厂商不公示」):
  ok            达标
  partial       部分(有缺口但已有替代证据)
  gap           缺口(需要 next_action 补采)
  not-published 终态:厂商确实不公示(0 价格token×全引擎 + 替代路径已探测)
                —— 记为「已解决的情报」而非失败,经验写入 lessons
  n-a           不适用

exit 0 = 无未解决 gap;exit 1 = 存在 gap(Step 2.5 循环补爬的信号)。
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from scripts.sufficiency import _PRICE_RX, is_free_tier, is_custom_tier  # noqa: E402

# ── 常量 ──
EXPECTED_PAGE_KINDS = ["homepage", "pricing", "features", "docs", "testimonials"]
PRICE_TOKEN_RX = _PRICE_RX
MONTHLY_RX = re.compile(r"^/?mo(nthly)??$|月付", re.I)
ANNUAL_RX = re.compile(r"billed|/yr|year|annual|年付|年", re.I)


def _norm(s: str) -> str:
    return " ".join((s or "").split())


def _load_json(p: Path, what: str) -> dict:
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"✗ 无法读取 {what}: {p} ({e})")
        sys.exit(2)


def _is_monthly(period: str) -> bool:
    p = (period or "").strip()
    return bool(p) and not bool(ANNUAL_RX.search(p)) and bool(MONTHLY_RX.search(p))


def _is_annual(period: str) -> bool:
    return bool(ANNUAL_RX.search(period or ""))


# ═══════════════ L1 · 覆盖率 ═══════════════


def audit_page_coverage(comp: dict, manifest: dict) -> dict:
    """页面类型覆盖:homepage/pricing/features/docs/testimonials。

    kinds 多值:fetched[url]["kinds"](2026-08-29 起 home_as_pricing 的
    首页同时是 homepage+pricing,不再互相覆盖);兼容旧数据的单 kind 字段。
    """
    domain = urlparse(comp.get("url") or "").netloc.replace("www.", "")
    kinds_got = {}
    for url, ent in (manifest.get("fetched") or {}).items():
        if ent.get("status") != "ok":
            continue
        u = urlparse(url)
        if domain and domain not in u.netloc.replace("www.", ""):
            # 第三方证据源(help center/app 市场/集团官网)也算覆盖
            if not any(k in u.netloc for k in (domain.split(".")[0],)):
                continue
        for k in ent.get("kinds") or [ent.get("kind")] or ["?"]:
            if k:
                kinds_got.setdefault(k, []).append(url)
    missing = [k for k in EXPECTED_PAGE_KINDS if k not in kinds_got]
    actions = []
    if "pricing" in missing:
        actions.append("定价页未抓到 → 搜索 '<域名> pricing' 定位官方定价页")
    if "features" in missing:
        actions.append(
            "功能页未抓到 → 试 /features /product /solutions 或 site: 搜索产品子页"
        )
    if "docs" in missing:
        actions.append("docs 未抓到 → scripts/deep_link.py site: 搜索文档子页")
    if "testimonials" in missing:
        actions.append("口碑页未抓到 → G2/Trustpilot/Reddit JSON 兜底(多数评测站反爬)")
    status = "ok" if not missing else ("partial" if len(missing) <= 2 else "gap")
    return {
        "status": status,
        "kinds_fetched": {k: v for k, v in kinds_got.items()},
        "missing_kinds": missing,
        "next_actions": actions,
    }


def _price_tokens_per_engine(comp: dict, raw_index: dict) -> Dict[str, int]:
    """该竞品所有官方域页面里,每引擎看到的价格 token 数。"""
    domain = urlparse(comp.get("url") or "").netloc.replace("www.", "")
    counts: Dict[str, int] = {}
    for url, engines in raw_index.items():
        u = urlparse(url)
        if domain and domain not in u.netloc.replace("www.", ""):
            continue
        for eng, md in engines.items():
            n = len(PRICE_TOKEN_RX.findall(md or ""))
            counts[eng] = counts.get(eng, 0) + n
    return counts


def _has_genuine_pricing_url(comp: dict, manifest: dict) -> bool:
    """判「厂商不公示」前先确认抓到的定价页是真实定价页。

    E2E 测试事故:SleekFlow 的涨价 webinar LP(长 slug 博客)被 fetch 当成
    pricing 页,0 价格 token → 误判 not-published。守卫:至少存在一个
    短路径定价 URL(/pricing /plans /price /定价格式)才算探测到位。
    """
    domain = urlparse(comp.get("url") or "").netloc.replace("www.", "")
    for url, ent in (manifest.get("fetched") or {}).items():
        if (ent.get("kind") or "") != "pricing":
            continue
        u = urlparse(url)
        if domain and domain not in u.netloc.replace("www.", ""):
            continue
        segs = [s.lower() for s in (u.path or "").split("/") if s]
        if not segs:
            return True  # 域名根定价页(少见但真实)
        if segs[-1] in ("pricing", "plans", "price", "prices", "定价", "价格", "套餐"):
            return True
    return False


def audit_pricing_depth(comp: dict, manifest: dict, raw_index: dict) -> dict:
    """定价深度:月付/年付配对、Free/Custom 语义、价格 token 覆盖。

    两种形态:
      Step 2.5(无 analysis,tiers 空): 只评原始 token 覆盖 —— ≥2 引擎有价格=ok,
        1 引擎=partial,0 引擎+有替代探测=not-published,0 引擎+无探测=gap
      Step 5(带 analysis): 在 token 覆盖之上评套餐结构(月/年配对、Free/Custom)
    """
    tiers = comp.get("pricing_tiers") or []
    token_engines = _price_tokens_per_engine(comp, raw_index)
    engines_with_price = {e: n for e, n in token_engines.items() if n > 0}
    findings, actions = [], []

    def _base(extra_findings=None, extra_actions=None):
        return {
            "monthly": False,
            "annual": False,
            "free": False,
            "custom": False,
            "priced_tiers": 0,
            "price_token_engines": token_engines,
            "engines_with_price": sorted(engines_with_price),
            "findings": extra_findings or [],
            "next_actions": extra_actions or [],
        }

    # ── 0 价格 token:不公示 vs 采集失败 ──
    if not engines_with_price:
        genuine = _has_genuine_pricing_url(comp, manifest)
        probes = [
            u
            for u in (manifest.get("fetched") or {})
            if urlparse(u).netloc.replace("www.", "")
            != urlparse(comp.get("url") or "").netloc.replace("www.", "")
            and any(
                k in ((manifest["fetched"][u].get("kind")) or "")
                for k in ("pricing", "docs", "about")
            )
        ]
        if genuine and (probes or len((manifest.get("fetched") or {})) > 2):
            r = _base(
                ["厂商不公示订阅价格(全引擎 0 价格 token)—— 本身是情报:B2B 询价制打法"],
                ["把「价格不公示(询价制)」写进 pricing 字段并附证据链,经验入 lessons"],
            )
            r["status"] = "not-published"
            return r
        if not genuine:
            r = _base(
                [
                    "抓到的「定价页」不是真实定价页(长 slug LP/博客被误判),"
                    "0 价格 token 不能下不公示结论"
                ],
                [
                    "web 搜索 '<竞品> pricing' 定位真实定价页(通常 /pricing /plans 短路径),"
                    "补爬后再审"
                ],
            )
            r["status"] = "gap"
            return r
        r = _base(
            ["全引擎 0 价格 token 且无替代探测 → 疑似采集失败而非不公示"],
            ["补爬 /price /plans / help center 定价页 + web 搜索 '<名> pricing'"],
        )
        r["status"] = "gap"
        return r

    # ── Step 2.5 形态:有 token 但还没做 Step 3 提取 ──
    if not tiers:
        single = len(engines_with_price) == 1
        r = _base(
            [
                f"{len(engines_with_price)} 个引擎看到价格 token"
                + ("(单引擎,交叉验证不足)" if single else "")
            ],
            (["对定价页沿升级梯换引擎重爬,凑 ≥2 引擎交叉验证"] if single else []),
        )
        r["status"] = "partial" if single else "ok"
        return r

    # ── Step 5 形态:套餐结构审计 ──
    monthly = [t for t in tiers if _is_monthly(t.get("billing_period", ""))]
    annual = [t for t in tiers if _is_annual(t.get("billing_period", ""))]
    free = [t for t in tiers if is_free_tier(t.get("name", ""), t.get("price", ""))]
    custom = [t for t in tiers if is_custom_tier(t.get("name", ""), t.get("price", ""))]
    priced = [t for t in tiers if PRICE_TOKEN_RX.search(t.get("price", "") or "")]

    if monthly and annual:
        pass  # 双周期齐全
    elif annual and not monthly:
        findings.append("仅年付价,缺月付价")
        actions.append(
            "定价页找月/年切换 toggle(JS 常见),月付价常需 playwright 点击后抓取"
        )
    elif monthly and not annual:
        findings.append("仅月付价,缺年付价")
        actions.append(
            "查年付折扣(常见 'Save up to X%' toggle);确认厂商确实无年付后记 partial"
        )
    if not free and not custom:
        findings.append("无 Free 档也无 Custom 档")
    if priced and len(engines_with_price) == 1:
        findings.append(
            f"分析有 {len(priced)} 个数字价格但原始证据仅 1 引擎可见({sorted(engines_with_price)})"
        )
        actions.append(
            "换引擎重爬定价页交叉验证,或把 pricing_verified 降为 false 诚实标注"
        )
    if comp.get("pricing_verified") is False and priced:
        findings.append("有数字价格但 pricing_verified=false(单引擎)")
        actions.append(
            "沿升级梯换引擎重爬(jina/trafilatura/playwright 至少两个拿到相同价格)"
        )
    status = (
        "ok"
        if (monthly and annual and priced and len(engines_with_price) >= 2)
        else ("partial" if priced or engines_with_price else "gap")
    )
    return {
        "status": status,
        "monthly": bool(monthly),
        "annual": bool(annual),
        "free": bool(free),
        "custom": bool(custom),
        "priced_tiers": len(priced),
        "price_token_engines": token_engines,
        "engines_with_price": sorted(engines_with_price),
        "findings": findings,
        "next_actions": actions,
    }


FIELD_MIN = {
    "core_features": 12,
    "strengths": 3,
    "weaknesses": 1,
    "differentiators": 1,
    "tech_signals": 1,
}


def audit_field_completeness(comp: dict) -> dict:
    """字段完整度(需要 --analysis;Step 5 用)。"""
    missing, actions = [], []
    for field, minimum in FIELD_MIN.items():
        n = len(comp.get(field) or [])
        if n < minimum:
            missing.append(f"{field} {n}/{minimum}")
            actions.append(
                f"{field} 不足 {minimum} 条 → 回 02-raw 补证据;无证据则如实留空"
            )
    for f in ("tagline", "pricing"):
        if not (comp.get(f) or "").strip():
            missing.append(f)
    if not (comp.get("user_feedback") or []):
        actions.append(
            "user_feedback 空 → 官网 testimonials 没抓到时用 G2/Reddit/应用市场评论兜底"
        )
    return {
        "status": "ok" if not missing else "partial",
        "missing": missing,
        "next_actions": actions,
    }


# ═══════════════ L2 · 准确性 ═══════════════


def _digits(s: str) -> str:
    """价格比较用纯数字形态('$1,068' ≡ '$1068' ≡ 'US$ 1068')。"""
    return re.sub(r"[^\d.]", "", s or "")


def audit_price_votes(comp: dict, raw_index: dict) -> dict:
    """分析里的每个价格值,统计能看到它的引擎数(跨引擎一致性)。"""
    votes = []
    for t in comp.get("pricing_tiers") or []:
        price = (t.get("price") or "").strip()
        if not PRICE_TOKEN_RX.search(price):
            continue
        src = (t.get("source_url") or comp.get("pricing_source") or "").strip()
        engines_hit = []
        for eng, md in (raw_index.get(src) or {}).items():
            if _digits(price) and any(
                _digits(price) == m
                for m in (_digits(x) for x in PRICE_TOKEN_RX.findall(_norm(md or "")))
            ):
                engines_hit.append(eng)
        votes.append(
            {
                "tier": t.get("name"),
                "price": price,
                "engines_seen": engines_hit,
                "agree": len(engines_hit) >= 2,
            }
        )
    bad = [v for v in votes if not v["agree"]]
    return {
        "status": "ok" if votes and not bad else ("partial" if votes else "n-a"),
        "votes": votes,
        "next_actions": [
            f"价格 {v['price']}({v['tier']}) 仅 {len(v['engines_seen'])} 引擎可见 → 换引擎重爬交叉验证"
            for v in bad
        ],
    }


def audit_quote_accuracy(comp: dict, raw_index: dict, sample: int = 8) -> dict:
    """quote 逐字回查(抽样;全量由 verify.py G2 把关)。"""
    quotes = []
    for key in (
        "strengths",
        "weaknesses",
        "differentiators",
        "tech_signals",
        "user_feedback",
    ):
        for item in comp.get(key) or []:
            if not isinstance(item, dict):
                continue
            q = item.get("quote") or item.get("evidence") or ""
            src = item.get("source") or item.get("source_url") or ""
            if q and src:
                quotes.append((key, src, q))
    checked, failed = 0, []
    for key, src, q in quotes[:sample]:
        engines = raw_index.get(src) or {}
        hit = any(_norm(q) in _norm(md or "") for md in engines.values())
        checked += 1
        if not hit:
            failed.append(f"{key}: {q[:40]}…")
    return {
        "status": "ok" if not failed else "gap",
        "checked": checked,
        "failed": failed,
        "next_actions": [f"quote 回查失败 → 修 quote 或换锚: {f}" for f in failed],
    }


# ═══════════════ L3 · 反哺(lessons / NEXT_CRAWL) ═══════════════


def _upsert_lesson(
    lessons: dict,
    domain: str,
    issue: str,
    resolution: str,
    evidence: List[str],
    alt_sources: List[str],
    hint: str = "",
):
    dom = lessons.setdefault(domain, {"lessons": []})
    for les in dom["lessons"]:
        if les["issue"] == issue:
            les["runs_seen"] = les.get("runs_seen", 1) + 1
            les["last_seen_at"] = time.strftime("%Y-%m-%d")
            if resolution:
                les["resolution"] = resolution
            if evidence:
                les["evidence"] = evidence  # 每次刷新,避免陈旧证据残留
            if alt_sources:
                les["alt_sources"] = alt_sources
            if hint:
                les["hint"] = hint
            return
    dom["lessons"].append(
        {
            "issue": issue,
            "resolution": resolution,
            "evidence": evidence,
            "alt_sources": alt_sources,
            "hint": hint,
            "learned_at": time.strftime("%Y-%m-%d"),
            "runs_seen": 1,
        }
    )


def build_lessons(competitors: List[dict], audits: dict) -> dict:
    """从审计结果沉淀跨会话经验(storage/intel-lessons.json)。"""
    lessons: dict = {}
    for comp in competitors:
        name = comp.get("name", "?")
        a = audits.get(name, {})
        domain = urlparse(comp.get("url") or "").netloc.replace("www.", "") or name
        pr = a.get("pricing_depth") or {}
        if pr.get("status") == "not-published":
            _upsert_lesson(
                lessons,
                domain,
                "pricing_not_published",
                "confirmed_not_published",
                evidence=[
                    "官方定价页全引擎 0 价格 token",
                    "替代路径已探测(help center/app市场/集团官网)",
                ],
                alt_sources=sorted(
                    {
                        u
                        for u in (a.get("page_coverage") or {})
                        .get("kinds_fetched", {})
                        .get("pricing", [])
                    }
                ),
                hint="下次运行直接采信「询价制」结论,跳过定价补爬,把预算让给功能/口碑深挖",
            )
        elif pr.get("status") in ("partial", "gap"):
            engs = pr.get("price_token_engines") or {}
            only = "/".join(sorted(engs)) or "none"
            _upsert_lesson(
                lessons,
                domain,
                "pricing_single_period_or_engine",
                f"engines={only}",
                evidence=[
                    f"月付:{pr.get('monthly')} 年付:{pr.get('annual')} "
                    f" priced_tiers:{pr.get('priced_tiers')}"
                ],
                alt_sources=[],
                hint="定价深挖抓手:月/年 toggle、JS 套餐卡片、help center 定价页、第三方比价页",
            )
    return lessons


# ═══════════════ 主流程 ═══════════════


def run_audit(manifest: dict, raw_dir: Path, analysis: Optional[dict]) -> dict:
    raw_index: Dict[str, Dict[str, str]] = {}
    for f in sorted(Path(raw_dir).glob("*.engines.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            continue
        for url, engines in (d or {}).items():
            raw_index.setdefault(url, {}).update(engines or {})

    comps = (analysis or {}).get("competitors") or []
    if not comps:
        # 无 analysis(Step 2.5):用 manifest 里的竞品域构造最小 comp
        seen = {}
        for url, ent in (manifest.get("fetched") or {}).items():
            if ent.get("kind") == "homepage":
                seen[urlparse(url).netloc.replace("www.", "")] = {
                    "name": url,
                    "url": url,
                }
        comps = list(seen.values())

    audits: Dict[str, dict] = {}
    for comp in comps:
        name = comp.get("name", "?")
        audits[name] = {
            "page_coverage": audit_page_coverage(comp, manifest),
            "pricing_depth": audit_pricing_depth(comp, manifest, raw_index),
        }
        if analysis:
            audits[name]["field_completeness"] = audit_field_completeness(comp)
            audits[name]["price_votes"] = audit_price_votes(comp, raw_index)
            audits[name]["quote_accuracy"] = audit_quote_accuracy(comp, raw_index)

    # 汇总
    next_actions: List[dict] = []
    unresolved_gaps = []
    for name, a in audits.items():
        for dim, res in a.items():
            for act in res.get("next_actions") or []:
                next_actions.append({"competitor": name, "dim": dim, "action": act})
            if res.get("status") == "gap":
                unresolved_gaps.append(f"{name}.{dim}")

    lessons = build_lessons(comps, audits)
    return {
        "audited_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "has_analysis": bool(analysis),
        "competitors": audits,
        "summary": {
            "total": len(audits),
            "unresolved_gaps": unresolved_gaps,
            "next_actions": next_actions,
            "not_published": [
                n
                for n, a in audits.items()
                if (a.get("pricing_depth") or {}).get("status") == "not-published"
            ],
        },
        "lessons_new": lessons,
    }


def _print_report(result: dict):
    print("\n=== 情报自我审计 ===")
    for name, a in result["competitors"].items():
        bits = []
        for dim, res in a.items():
            icon = {
                "ok": "✓",
                "partial": "◐",
                "gap": "✗",
                "not-published": "⊘",
                "n-a": "·",
            }.get(res.get("status"), "?")
            bits.append(f"{dim}:{icon}{res.get('status')}")
        pr = a.get("pricing_depth") or {}
        extra = ""
        if pr:
            extra = (
                f" [月付:{'Y' if pr.get('monthly') else 'N'}"
                f" 年付:{'Y' if pr.get('annual') else 'N'}"
                f" priced:{pr.get('priced_tiers')}]"
            )
        print(f"  {name:12s} {extra}")
        print(f"               {' · '.join(bits)}")
        for dim, res in a.items():
            for f_ in res.get("findings") or []:
                print(f"               · {dim}: {f_}")
    s = result["summary"]
    print(f"\n  未解决 gap: {len(s['unresolved_gaps'])} {s['unresolved_gaps'] or ''}")
    print(f"  不公示(终态情报): {', '.join(s['not_published']) or '无'}")
    print(f"  next_actions: {len(s['next_actions'])} 条")
    for na in s["next_actions"][:10]:
        print(f"    → [{na['competitor']}/{na['dim']}] {na['action']}")
    print("=" * 46)


def main() -> int:
    ap = argparse.ArgumentParser(description="youzi 情报自我审计器")
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--raw-dir", required=True)
    ap.add_argument("--analysis", default=None, help="03-analysis.json(Step 5 终审用)")
    ap.add_argument(
        "--out",
        default=None,
        help="审计报告 JSON 输出(默认 <manifest同目录>/04-audit.json)",
    )
    ap.add_argument(
        "--lessons-file",
        default=str(ROOT / "storage" / "intel-lessons.json"),
        help="跨会话经验沉淀文件",
    )
    ap.add_argument(
        "--evolve",
        action="store_true",
        default=True,
        help="把新经验合并进 lessons 文件(自我进化闭环)",
    )
    ap.add_argument("--no-evolve", dest="evolve", action="store_false")
    args = ap.parse_args()

    manifest = _load_json(Path(args.manifest), "manifest")
    analysis = _load_json(Path(args.analysis), "analysis") if args.analysis else None
    result = run_audit(manifest, Path(args.raw_dir), analysis)

    out_path = (
        Path(args.out) if args.out else Path(args.manifest).parent / "04-audit.json"
    )
    out_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    if args.evolve and result.get("lessons_new"):
        lp = Path(args.lessons_file)
        lessons_all: dict = {}
        try:
            lessons_all = json.loads(lp.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            lessons_all = {}
        for domain, dom in result["lessons_new"].items():
            for les in dom["lessons"]:
                _upsert_lesson(
                    lessons_all,
                    domain,
                    les["issue"],
                    les.get("resolution", ""),
                    les.get("evidence", []),
                    les.get("alt_sources", []),
                    les.get("hint", ""),
                )
        lp.parent.mkdir(parents=True, exist_ok=True)
        lp.write_text(
            json.dumps(lessons_all, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        result["lessons_file"] = str(lp)

    _print_report(result)
    print(f"审计报告: {out_path}")
    gaps = result["summary"]["unresolved_gaps"]
    return 1 if gaps else 0


if __name__ == "__main__":
    sys.exit(main())
