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
from typing import Dict, List
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
        r"features?|functionalit|capabilities|platform|product|产品|功能", re.I
    ),
    "about": re.compile(
        r"^about|about[-\s]us|company|our[-\s]story|team$|关于|公司", re.I
    ),
    "docs": re.compile(r"^docs?$|documentation|developers?|api[-\s]docs", re.I),
    "testimonials": re.compile(
        r"testimonials?|customer[-\s]?stor(y|ies)|case[-\s]?stud|success[-\s]?stor"
        r"|customers$|reviews?|口碑|客户案例|用户评价",
        re.I,
    ),
    "blog": re.compile(
        r"^blog$|blogs?/|news|changelog|release[-\s]?notes?|updates?|博客|动态", re.I
    ),
}
# 深链栏目限制:testimonials/blog 只收栏目页(路径 ≤2 段)
_PAGE_ORDER = ["pricing", "features", "docs", "about", "testimonials", "blog"]

_PRICE_TOKEN_RX = re.compile(
    r"(?<![.\d])[$€£¥]\s?\d[\d,]*(?:\.\d+)?(?:\s?/\s?(?:mo|month|yr|year|user/month|seat/mo))?",
    re.I,
)


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
            if urlparse(full).netloc.replace("www.", "") != urlparse(
                base_url
            ).netloc.replace("www.", ""):
                continue
        except Exception:
            continue
        for kind, pat in _DISCOVER_PATTERNS.items():
            if kind in found:
                continue
            if pat.search(text) or pat.search(
                full.replace(base_url.rstrip("/"), "", 1).split("?")[0]
            ):
                url_clean = full.split("?")[0].split("#")[0]
                if kind in ("testimonials", "blog"):
                    depth = len([p for p in urlparse(url_clean).path.split("/") if p])
                    if depth > 2:
                        continue
                found[kind] = url_clean
    return found


def vote_price_lines(all_results: List[Dict]) -> List[Dict]:
    """跨引擎价格行投票(证据级,非语义提取):同一价格 token 被 ≥2 独立
    引擎看到 = 交叉验证票。LLM Step 3/G3 门禁消费。

    投票键 = 裸价格值:"$39/user/month" 与 "$39/user/mo" 是同一票
    (周期/单位语义留给 LLM Step 3),否则写法差异会拆散交叉验证。
    """
    by_token: Dict[str, Dict] = {}
    for r in all_results or []:
        eng = r.get("scraper", "?")
        if not (r.get("success") and r.get("markdown")):
            continue
        seen_in_engine = set()
        for m in _PRICE_TOKEN_RX.finditer(r["markdown"]):
            tok = re.sub(r"\s+", "", m.group(0).split("/")[0]).lower()
            if tok in seen_in_engine:
                continue
            seen_in_engine.add(tok)
            slot = by_token.setdefault(
                tok, {"token": m.group(0), "engines": [], "independent_votes": 0}
            )
            if eng not in slot["engines"]:
                slot["engines"].append(eng)
                slot["independent_votes"] += 1
    return [v for v in by_token.values() if v["independent_votes"] >= 1]


def _price_cross_validated(all_results: List[Dict]) -> bool:
    """定价页取证充分性:存在 ≥2 独立引擎看到相同价格(交叉验证)。"""
    return any(v["independent_votes"] >= 2 for v in vote_price_lines(all_results or []))


def _merged_ok(result: Dict) -> bool:
    return bool(result.get("success") and result.get("markdown"))


def fetch_competitor(name: str, out_dir: Path, budget_s: float = None) -> Dict:
    """采集单个竞品全部证据页。预算内不充分则沿升级梯重爬定价页。"""
    budget_s = sufficiency.COMPETITOR_BUDGET_SECONDS if budget_s is None else budget_s
    t0 = time.monotonic()
    resolved = resolve_competitor(name)
    if not resolved:
        return {
            "name": name,
            "url": "",
            "pages": {},
            "failures": [
                {"competitor": name, "url": "", "kind": "resolve", "error": "not_found"}
            ],
        }
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
        for x in result.get("all_results") or []:
            if x.get("scraper") and x.get("success") and x.get("markdown"):
                engines_md[x["scraper"]] = x["markdown"][:50000]
                engines_meta[x["scraper"]] = {
                    "ok": True,
                    "chars": len(x["markdown"]),
                    "content_hash": _content_hash(x["markdown"]),
                }
        engines_doc[url] = engines_md
        ok = _merged_ok(result)
        fetched[url] = {
            "status": "ok" if ok else "failed",
            "kind": kind,
            "engines": engines_meta,
            "fetched_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        }
        if not ok:
            failures.append(
                {
                    "competitor": cname,
                    "url": url,
                    "kind": kind,
                    "error": (result.get("error") or "all engines empty")[:200],
                }
            )
        if engines_md:
            scrapers = ",".join(engines_md)
            raw_sections.append(
                f"# Kind: {kind}\n# Source: {url}\n# Scrapers: {scrapers}\n"
                f"# Time: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}\n\n"
                + (result.get("markdown") or "")
            )

    # ── 首页 ──
    home = scrape_smart(base)
    _record(base, home, "homepage")
    home_md = home.get("markdown") or ""

    # ── URL 发现:resolver 猜路径优先,导航发现补缺/纠错 ──
    targets: Dict[str, str] = {}
    guessed = {
        "pricing": resolved.get("pricing_url"),
        "features": resolved.get("features_url"),
        "docs": resolved.get("docs_url"),
    }
    nav = discover_urls(home_md, base)
    for kind in _PAGE_ORDER:
        targets[kind] = nav.get(kind) or guessed.get(kind)
    targets = {k: v for k, v in targets.items() if v and v != base}

    # ── 逐页爬取 + 页面级充分性 + 定价交叉验证升级梯 ──
    for kind, url in targets.items():
        result = scrape_smart(url)
        used = [
            r.get("scraper")
            for r in (result.get("all_results") or [])
            if r.get("success")
        ]
        if kind == "pricing":
            # 页面壳 ok 但价格未被 ≥2 引擎交叉验证 → 沿升级梯换引擎重爬
            while _merged_ok(result) and not _price_cross_validated(
                result.get("all_results") or []
            ):
                if time.monotonic() - t0 > budget_s:
                    break  # 预算耗尽 → 诚实 insufficient,不再重爬
                extra = sufficiency.ladder_engines("pricing", already_used=used)
                if not extra:
                    break
                retry = scrape_smart(url, enabled_scrapers=extra[:2])
                if not _merged_ok(retry):
                    failures.append(
                        {
                            "competitor": cname,
                            "url": url,
                            "kind": kind,
                            "error": "ladder retry failed",
                        }
                    )
                    break
                merged_all = (result.get("all_results") or []) + (
                    retry.get("all_results") or []
                )
                result = {
                    "success": True,
                    "scraper": "+".join(
                        dict.fromkeys(
                            (result.get("scraper") or "").split("+")
                            + (retry.get("scraper") or "").split("+")
                        )
                    ),
                    "markdown": (result.get("markdown") or "")
                    + "\n\n"
                    + (retry.get("markdown") or ""),
                    "all_results": merged_all,
                    "stats": {
                        "successful": len([r for r in merged_all if r.get("success")])
                    },
                }
                used += [
                    r.get("scraper")
                    for r in (retry.get("all_results") or [])
                    if r.get("success")
                ]
            # 全灭(页面壳都拿不到) → deep_link 搜索发现官方定价页
            if not _merged_ok(result):
                try:
                    from scripts import deep_link

                    alt = deep_link.locate_pricing_page(
                        urlparse(base).netloc.replace("www.", "")
                    )
                    if alt and alt.get("url"):
                        _record(url, result, kind)  # 原失败留痕
                        url = alt["url"]
                        result = {
                            "success": True,
                            "scraper": "deep_link+search",
                            "markdown": alt.get("markdown") or "",
                            "all_results": [
                                {
                                    "success": True,
                                    "scraper": "deep_link",
                                    "markdown": alt.get("markdown") or "",
                                }
                            ],
                            "stats": {"successful": 1},
                        }
                except Exception:
                    pass
            pages[kind] = {
                "url": url,
                "engines": [e for e in (used or []) if e],
                "sufficient": _merged_ok(result)
                and _price_cross_validated(result.get("all_results") or []),
            }
        else:
            pages[kind] = {
                "url": url,
                "engines": used,
                "sufficient": _merged_ok(result)
                and sufficiency.assess_page_content(kind, result.get("markdown") or ""),
            }
        _record(url, result, kind)

    # ── 落盘 ──
    (raw_dir / f"{cname}.engines.json").write_text(
        json.dumps(engines_doc, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    (raw_dir / f"{cname}.md").write_text(
        "\n\n---\n\n".join(raw_sections), encoding="utf-8"
    )
    manifest_path = out_dir / "claims-manifest.json"
    manifest = {
        "run": {
            "topic": "",
            "started_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
            "pipeline_version": "3.0",
        },
        "fetched": fetched,
        "claims": [],
        "failures": failures,
    }
    if manifest_path.exists():
        try:
            old = json.loads(manifest_path.read_text(encoding="utf-8"))
            old.setdefault("fetched", {}).update(fetched)
            old.setdefault("failures", []).extend(failures)
            manifest = old
        except Exception:
            pass
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return {
        "name": resolved.get("name") or name,
        "url": base,
        "pages": pages,
        "failures": failures,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="youzi V2 取证采集器(无语义提取)")
    ap.add_argument(
        "--competitors", required=True, help="逗号分隔的竞品名或域名,如 wati,respond.io"
    )
    ap.add_argument("--out-dir", required=True, help="输出目录(OUT_DIR)")
    ap.add_argument(
        "--budget", type=float, default=None, help="每竞品墙钟预算秒数(默认 300)"
    )
    args = ap.parse_args()
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    ok = True
    for n in [x.strip() for x in args.competitors.split(",") if x.strip()]:
        t0 = time.time()
        r = fetch_competitor(n, out_dir, budget_s=args.budget)
        insuff = [k for k, v in r["pages"].items() if not v["sufficient"]]
        print(
            f"[{'✓' if r['pages'] else '✗'} {r['name']}] {time.time() - t0:5.1f}s | "
            f"{len(r['pages'])} pages | 不充分: {','.join(insuff) or '无'} | "
            f"failures: {len(r['failures'])}"
        )
        if not r["pages"]:
            ok = False
    print(f"\n台账: {out_dir / 'claims-manifest.json'}")
    print(f"原文: {out_dir / '02-raw'}/")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
