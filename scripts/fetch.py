#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""youzi V2 · 取证层采集器 —— 只取证,不做语义提取。

职责(全部,没有更多):
  1. URL 发现   resolver 猜路径 + 多引擎首页导航发现兜底(404 语义)
  2. 爬取       scrape_smart 智能路由(4+1 白名单引擎),页面级并行
  3. 落盘       02-raw/<name>.md + <name>.engines.json(每引擎原文独立)
  4. 台账       claims-manifest.json.fetched(url × engine × hash × 时间)

充分性闭环:定价页 ≥2 独立引擎看到相同价格才 sufficient;不达标沿
升级梯换未用引擎重爬;全灭时 deep_link 搜索发现官方定价页 →
≤14 天已验证缓存回退(pricing-cache.json);预算(默认 300s/竞品,
全页面共享 deadline)耗尽 → 诚实标 insufficient。

2026-08-29 升级(审计修复):
  - 页面级并行:homepage 先行(发现依赖它),其余页面 asyncio 并发
    (实测串行 7 页 40-60s → 并行 15-25s);竞品级并行(main,3 并发)
  - 多引擎导航发现:对全部成功引擎的原文分别发现再取并集(历史只吃
    合并视图 —— YCloud 实测 primary=playwright 0 链接 CSS 垃圾,
    jina 明明看到 210 个链接却不用,4 类页面直接丢失)
  - 台账 kinds 多值:home_as_pricing 触发时 fetched[base] 的 kind 不再
    被 pricing 覆盖(历史 bug:audit 误判 homepage 缺失 → 假 gap)
  - 价格投票统一 pricing_tokens(₹/Rs./US$/后缀€ 全覆盖 + 投票键归一)
  - ≤14 天定价缓存回退落地(V2 重构时被删,SKILL.md 宣称落空)
  - robots.txt 检查 + 页面级错峰(礼貌性)

语义提取(tiers/features/tagline/...)是 LLM Step 3 的工作,这里不做。
"""

import argparse
import asyncio
import hashlib
import json
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters import scrape_smart, truncate_md  # noqa: E402
from adapters.competitor_resolver import resolve_competitor  # noqa: E402
from pricing_tokens import PRICE_TOKEN_RX, price_vote_key  # noqa: E402
from scripts import sufficiency  # noqa: E402

# 竞品间并发数(main 层):不同域并行,受 jina 全局限速自然约束
_COMPETITOR_CONCURRENCY = 3
# 页面级错峰间隔(同域礼貌性):并行页任务启动间隔
_PAGE_STAGGER_S = 0.8

# 台账/缓存文件锁:竞品级并行后,manifest 读写与 cname 判重需要互斥
_MANIFEST_LOCK = threading.Lock()

# 首页导航发现模式(自 V1 提取单体移植,语义不变)
_DISCOVER_PATTERNS = {
    "pricing": re.compile(r"pricing|price|plans?|定价|价格|套餐", re.I),
    "features": re.compile(
        r"features?|functionalit|capabilities|platform|product|产品|功能", re.I
    ),
    "about": re.compile(
        r"^about|about[-\s]us|company|our[-\s]story|team$|关于|公司", re.I
    ),
    "docs": re.compile(
        r"^docs?$|documentation|developers?|api[-\s]docs|//docs\.", re.I
    ),
    "testimonials": re.compile(
        r"testimonials?|customer[-\s]?stor(y|ies)|case[-\s]?stud|success[-\s]?stor"
        r"|customers$|reviews?|口碑|客户案例|用户评价",
        re.I,
    ),
    "blog": re.compile(
        r"^blog$|blogs?/|news|changelog|release[-\s]?notes?|updates?|博客|动态", re.I
    ),
}
# 深链栏目限制:testimonials/blog 只收栏目页(有效路径 ≤2 段;
# 纯数字/page 分页段不计深)
_PAGE_ORDER = ["pricing", "features", "docs", "about", "testimonials", "blog"]

_PRICE_TOKEN_RX = PRICE_TOKEN_RX

# ── ≤14 天定价缓存(与 verify.TTL_DAYS 同口径) ──
_PRICING_CACHE_PATH = ROOT / "storage" / "pricing-cache.json"
_PRICING_CACHE_TTL_DAYS = 14.0

# ── robots.txt 轻量检查(进程内缓存;拉取失败按允许处理) ──
_ROBOTS_CACHE: Dict[str, List[str]] = {}


def _content_hash(md: str) -> str:
    return hashlib.sha256(" ".join((md or "").split()).encode("utf-8")).hexdigest()[:16]


def _registrable_domain(host: str) -> str:
    """粗取可注册域(与 gates._registrable_domain 同规则,本地副本避免循环依赖)。"""
    parts = (host or "").lower().split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else (host or "").lower()


def _same_site(url_a: str, url_b: str) -> bool:
    """同站点 = 可注册域相同(docs.wati.io 与 www.wati.io 同站)。

    历史实现比对完整 netloc → docs 子域从首页导航里永远发现不了,
    非内置竞品(domain-guess)的 docs 页全靠猜 /docs(常 404)。
    """
    return _registrable_domain(urlparse(url_a).netloc) == _registrable_domain(
        urlparse(url_b).netloc
    )


def _effective_depth(path: str) -> int:
    """栏目深度:纯数字/page 分页段不计(docs/cat/page/1 → 1 段)。"""
    return len(
        [
            p
            for p in path.split("/")
            if p and not p.isdigit() and p.lower() not in ("page", "pages")
        ]
    )


# 非页面资源后缀:CDN 图片/字体/样式等(linear.app 事故:同站判定放宽到
# 可注册域后,webassets 子域的 PNG 截图被当成 features 页抓取)
_RESOURCE_EXT_RX = re.compile(
    r"\.(png|jpe?g|svg|webp|gif|avif|mp4|webm|css|js|mjs|ico|woff2?|ttf|otf"
    r"|pdf|zip)(?:[?#]|)$",
    re.I,
)


def discover_urls(home_md: str, base_url: str) -> Dict[str, str]:
    """首页 markdown 链接 → {kind: url}。只认同站 http(s) 页面,链接文本优先。

    排除:图片语法 ![alt](url)、非页面资源后缀、mailto/tel/javascript。
    """
    found: Dict[str, str] = {}
    for m in re.finditer(r"(?<!!)\[([^\]\[]{2,40})\]\(([^)#\s]+)\)", home_md or ""):
        text, href = m.group(1).strip(), m.group(2).strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        full = urljoin(base_url.rstrip("/") + "/", href)
        if not full.startswith("http"):
            continue
        if not _same_site(full, base_url):
            continue
        if _RESOURCE_EXT_RX.search(full.split("?")[0].split("#")[0]):
            continue
        for kind, pat in _DISCOVER_PATTERNS.items():
            if kind in found:
                continue
            if pat.search(text) or pat.search(
                full.replace(base_url.rstrip("/"), "", 1).split("?")[0]
            ):
                url_clean = full.split("?")[0].split("#")[0]
                if kind in ("testimonials", "blog"):
                    if _effective_depth(urlparse(url_clean).path) > 2:
                        continue
                found[kind] = url_clean
    return found


def discover_from_results(all_results: List[Dict], base_url: str) -> Dict[str, str]:
    """多引擎导航发现:对每个成功引擎的原文分别发现,取并集。

    单吃合并视图的历史事故(YCloud 实测):primary=playwright 的 0 链接
    CSS 垃圾 → 发现空;jina 原文 210 个链接明明能看到 blog/about/docs
    却被丢弃 → 4 类页面整类丢失,audit 报 partial、补爬空转。
    """
    merged: Dict[str, str] = {}
    # 引擎顺序 = 合并层信任序(质量高的先发现,优先占坑)
    for r in all_results or []:
        if not (r.get("success") and r.get("markdown")):
            continue
        for kind, url in discover_urls(r["markdown"], base_url).items():
            merged.setdefault(kind, url)
    return merged


# ── robots.txt ──


def _robots_disallows(domain_url: str) -> List[str]:
    """拉取该站 robots.txt 的 User-agent:* Disallow 前缀;失败返回空(允许)。"""
    try:
        p = urlparse(domain_url)
        origin = f"{p.scheme}://{p.netloc}"
        if origin in _ROBOTS_CACHE:
            return _ROBOTS_CACHE[origin]
        import urllib.request

        req = urllib.request.Request(
            f"{origin}/robots.txt",
            headers={"User-Agent": "Mozilla/5.0 (Macintosh) youzi-intel/2.0"},
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            body = resp.read(100000).decode("utf-8", "ignore")
        disallows, in_star = [], False
        for ln in body.splitlines():
            s = ln.strip().lower()
            if s.startswith("user-agent:"):
                in_star = s.split(":", 1)[1].strip() == "*"
            elif in_star and s.startswith("disallow:"):
                rule = s.split(":", 1)[1].strip()
                if rule:
                    disallows.append(rule)
        _ROBOTS_CACHE[origin] = disallows
        return disallows
    except Exception:
        _ROBOTS_CACHE.setdefault(domain_url, [])
        return []


def _robots_allowed(url: str) -> bool:
    for rule in _robots_disallows(url):
        if rule == "/":
            return False
        path = urlparse(url).path or "/"
        if path.startswith(rule):
            return False
    return True


# ── 价格投票(统一 pricing_tokens:₹/Rs./US$/后缀€ 全覆盖) ──


def vote_price_lines(all_results: List[Dict]) -> List[Dict]:
    """跨引擎价格行投票(证据级,非语义提取):同一价格 token 被 ≥2 独立
    引擎看到 = 交叉验证票。LLM Step 3/G3 门禁消费。

    投票键 = price_vote_key(归一货币+数字):"US$39"/"$39"/"39 USD" 是
    同一票(历史实现在写法差异下拆散交叉验证)。
    """
    by_token: Dict[str, Dict] = {}
    for r in all_results or []:
        eng = r.get("scraper", "?")
        if not (r.get("success") and r.get("markdown")):
            continue
        seen_in_engine = set()
        for m in _PRICE_TOKEN_RX.finditer(r["markdown"]):
            key = price_vote_key(m.group(0))
            if not key or key in seen_in_engine:
                continue
            seen_in_engine.add(key)
            slot = by_token.setdefault(
                key, {"token": m.group(0), "engines": [], "independent_votes": 0}
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


# ── ≤14 天定价缓存 ──


def _cache_load() -> Dict:
    try:
        return json.loads(_PRICING_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _cache_age_days(ent: Dict) -> float:
    try:
        t = time.strptime((ent.get("fetched_at") or ""), "%Y-%m-%d %H:%M UTC")
        import calendar

        return (time.time() - calendar.timegm(t)) / 86400.0
    except Exception:
        return float("inf")


def _cache_get(domain: str) -> Optional[Dict]:
    """≤14 天且带 ≥2 引擎【原文 dict】的缓存条目;否则 None。

    V1 遗留条目的 engines 是名字列表(无原文)——不满足 G3 哈希复现,
    直接跳过(不回退、不崩溃)。
    """
    ent = _cache_load().get(domain)
    if not ent or _cache_age_days(ent) > _PRICING_CACHE_TTL_DAYS:
        return None
    engines = ent.get("engines")
    if not isinstance(engines, dict) or len(engines) < 2:
        return None  # 单引擎/旧格式缓存无法支撑 G3 交叉验证
    return ent


def _cache_put(domain: str, url: str, engines_md: Dict[str, str]) -> None:
    """定价交叉验证成功后回写缓存(顺手清理过期条目)。"""
    if len(engines_md) < 2:
        return
    with _MANIFEST_LOCK:
        cache = _cache_load()
        cache[domain] = {
            "url": url,
            "engines": engines_md,
            "fetched_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        }
        # 过期清理
        cache = {
            d: e
            for d, e in cache.items()
            if _cache_age_days(e) <= _PRICING_CACHE_TTL_DAYS
        }
        try:
            _PRICING_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
            _PRICING_CACHE_PATH.write_text(
                json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except Exception:
            pass


# ── 页面任务(线程内运行;返回后由主流程按 _PAGE_ORDER 顺序组装) ──


def _empty_result() -> Dict:
    return {
        "success": False,
        "scraper": "none",
        "markdown": "",
        "all_results": [],
        "stats": {"successful": 0},
    }


def _fetch_page(
    kind: str,
    url: str,
    *,
    base: str,
    cname: str,
    home: Dict,
    home_md: str,
    deadline: float,
) -> Dict:
    """抓单页 + 页面级充分性 + 定价升级梯/深链/首页/缓存四级回退。

    返回 {kind, url, result, pages, records, failures}:
      records  = [(url, result, kind), ...] 按发生顺序(留痕优先)
      failures = 台账 failures 追加项
    """
    records: List[tuple] = []
    failures: List[Dict] = []

    def rec(u: str, r: Dict, k: str) -> None:
        records.append((u, r, k))

    if time.monotonic() > deadline:
        failures.append(
            {
                "competitor": cname,
                "url": url,
                "kind": kind,
                "error": "budget exhausted before page fetch",
            }
        )
        return {
            "kind": kind,
            "url": url,
            "result": _empty_result(),
            "pages": {
                "url": url,
                "engines": [],
                "sufficient": False,
                "problems": ["预算耗尽(整竞品共享 deadline)"],
            },
            "records": records,
            "failures": failures,
        }

    result = scrape_smart(url)
    used = [
        r.get("scraper") for r in (result.get("all_results") or []) if r.get("success")
    ]

    if kind == "pricing":
        # 页面壳 ok 但价格未被 ≥2 引擎交叉验证 → 沿升级梯换引擎重爬
        while _merged_ok(result) and not _price_cross_validated(
            result.get("all_results") or []
        ):
            if time.monotonic() > deadline:
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
                    rec(url, result, kind)  # 原失败留痕
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

        # 定价页仍全灭 → 首页含价回退(定价藏首页 FAQ/表格的站,如 greptile):
        # 首页有 ≥2 个价格 token 且 ≥2 独立引擎看到相同价格 → 首页作为
        # pricing 替代证据(台账照记原 404 留痕,note 标注非独立定价页)
        home_as_pricing = False
        if not _merged_ok(result) and home_md:
            voted = vote_price_lines(home.get("all_results") or [])
            if len(_PRICE_TOKEN_RX.findall(home_md)) >= 2 and any(
                v["independent_votes"] >= 2 for v in voted
            ):
                rec(url, result, kind)  # 原失败留痕
                url = base
                result = home
                used = [
                    r.get("scraper")
                    for r in (home.get("all_results") or [])
                    if r.get("success")
                ]
                home_as_pricing = True

        # 四级回退:≤14 天已验证缓存(反爬全灭时保持证据链可用;
        # manifest 记 engines=cache,LLM Step 3 置 pricing_from_cache)
        from_cache = False
        if not _merged_ok(result):
            cached = _cache_get(urlparse(base).netloc.replace("www.", ""))
            if cached and cached.get("url"):
                rec(url, result, kind)  # 原失败留痕
                url = cached["url"]
                result = {
                    "success": True,
                    "scraper": "+".join(cached.get("engines", {}).keys()),
                    "markdown": "\n\n".join(cached.get("engines", {}).values()),
                    "all_results": [
                        {"success": True, "scraper": eng, "markdown": md}
                        for eng, md in (cached.get("engines") or {}).items()
                    ],
                    "stats": {"successful": len(cached.get("engines") or {})},
                }
                used = list((cached.get("engines") or {}).keys())
                from_cache = True

        problems: List[str] = []
        if not _merged_ok(result):
            problems.append("页面壳获取失败(全部引擎无正文)")
        else:
            n_eng = max(
                (
                    v["independent_votes"]
                    for v in vote_price_lines(result.get("all_results") or [])
                ),
                default=0,
            )
            if n_eng < 2:
                problems.append(f"价格仅 {n_eng} 引擎见到(需 ≥2 交叉验证)")
        pages_entry = {
            "url": url,
            "engines": [e for e in (used or []) if e],
            "sufficient": not problems,
            "problems": problems,
        }
        if home_as_pricing:
            pages_entry["note"] = "定价来自首页非独立定价页"
        if from_cache:
            pages_entry["note"] = (
                f"定价来自 ≤{_PRICING_CACHE_TTL_DAYS:.0f} 天缓存回退(本轮全灭)"
            )
            pages_entry["from_cache"] = True
        # 交叉验证成功 → 回写缓存(带每引擎原文,G3 哈希可复现)
        if _merged_ok(result) and not from_cache and not home_as_pricing:
            engines_md_now = {
                x["scraper"]: x["markdown"]
                for x in (result.get("all_results") or [])
                if x.get("scraper") and x.get("success") and x.get("markdown")
            }
            if _price_cross_validated(result.get("all_results") or []):
                _cache_put(
                    urlparse(base).netloc.replace("www.", ""), url, engines_md_now
                )
    else:
        problems = []
        if not _merged_ok(result):
            problems.append("页面壳获取失败(全部引擎无正文)")
        elif not sufficiency.assess_page_content(kind, result.get("markdown") or ""):
            problems.append("页面内容不充分(过短或 JS 壳/404 语义)")
        pages_entry = {
            "url": url,
            "engines": used,
            "sufficient": not problems,
            "problems": problems,
        }

    rec(url, result, kind)
    return {
        "kind": kind,
        "url": url,
        "result": result,
        "pages": pages_entry,
        "records": records,
        "failures": failures,
    }


def fetch_competitor(
    name: str, out_dir: Path, budget_s: Optional[float] = None, topic: str = ""
) -> Dict:
    """采集单个竞品全部证据页。页面级并行;预算为整竞品共享 deadline。"""
    budget_s = sufficiency.COMPETITOR_BUDGET_SECONDS if budget_s is None else budget_s
    t0 = time.monotonic()
    deadline = t0 + budget_s
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

    # robots.txt:整站 disallow → 诚实记失败(合规底线),不硬闯
    if not _robots_allowed(base):
        return {
            "name": resolved.get("name") or name,
            "url": base,
            "pages": {},
            "failures": [
                {
                    "competitor": name,
                    "url": base,
                    "kind": "robots",
                    "error": "robots.txt disallow",
                }
            ],
        }

    with _MANIFEST_LOCK:
        cname = re.sub(r"[^\w\-]", "_", (resolved.get("canonical_name") or name))
        raw_dir = out_dir / "02-raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        # 同域多竞品(www 前缀/子路径差异)可能解析出同一 cname → 落盘文件
        # 互相覆盖。已存在的引擎原文不属于本竞品(base URL 不在其中)时,
        # 追加短 hash 后缀。
        eng_file = raw_dir / f"{cname}.engines.json"
        if eng_file.exists():
            try:
                existing = json.loads(eng_file.read_text(encoding="utf-8"))
            except Exception:
                existing = {}
            if base not in (existing or {}):
                cname = (
                    f"{cname}_{hashlib.sha256(base.encode('utf-8')).hexdigest()[:6]}"
                )

    fetched: Dict[str, Dict] = {}
    failures: List[Dict] = []
    pages: Dict[str, Dict] = {}
    engines_doc: Dict[str, Dict[str, str]] = {}
    raw_sections: List[str] = []

    def _record(url: str, result: Dict, kind: str):
        engines_md, engines_meta = {}, {}
        for x in result.get("all_results") or []:
            if x.get("scraper") and x.get("success") and x.get("markdown"):
                # 头尾截断:头部截断的历史事故 —— playwright 单行 CSS 垃圾
                # 占满前 50K,正文 100% 被砍,证据库失去 grep 能力
                engines_md[x["scraper"]] = truncate_md(x["markdown"], 50000)
                engines_meta[x["scraper"]] = {
                    "ok": True,
                    "chars": len(x["markdown"]),
                    "content_hash": _content_hash(x["markdown"]),
                }
        engines_doc[url] = engines_md
        ok = _merged_ok(result)
        entry = {
            "status": "ok" if ok else "failed",
            "kind": kind,
            "kinds": [kind],
            "engines": engines_meta,
            "fetched_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        }
        if url in fetched:
            # 同 URL 多 kind(如 home_as_pricing 的首页):kind 多值化保留
            # 两个语义(历史 bug:后者覆盖前者 → audit 误判 homepage 缺失)
            ent = fetched[url]
            if kind not in ent["kinds"]:
                ent["kinds"].append(kind)
            if ok and ent.get("status") != "ok":
                ent["status"] = "ok"
                ent["engines"] = engines_meta
                ent["fetched_at"] = entry["fetched_at"]
                engines_doc[url] = engines_md
            else:
                for eng, meta in engines_meta.items():
                    ent.setdefault("engines", {})[eng] = meta
        else:
            fetched[url] = entry
        if not ok:
            failures.append(
                {
                    "competitor": cname,
                    "url": url,
                    "kind": kind,
                    "error": (result.get("error") or "all engines empty")[:200],
                }
            )
        if engines_md and ok:
            scrapers = ",".join(engines_md)
            raw_sections.append(
                f"# Kind: {'/'.join(fetched[url]['kinds'])}\n# Source: {url}\n"
                f"# Scrapers: {scrapers}\n"
                f"# Time: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())}\n\n"
                + (result.get("markdown") or "")
            )

    # ── 首页(先行:URL 发现依赖它) ──
    home = scrape_smart(base)
    _record(base, home, "homepage")
    home_md = home.get("markdown") or ""

    # ── URL 发现:resolver 猜路径优先,多引擎导航发现补缺/纠错 ──
    guessed = {
        "pricing": resolved.get("pricing_url"),
        "features": resolved.get("features_url"),
        "docs": resolved.get("docs_url"),
    }
    nav = discover_from_results(home.get("all_results") or [], base)
    candidates: Dict[str, Optional[str]] = {}
    for kind in _PAGE_ORDER:
        candidates[kind] = nav.get(kind) or guessed.get(kind)
    targets: Dict[str, str] = {k: v for k, v in candidates.items() if v and v != base}

    # robots 检查(页面级):disallow 的页面诚实跳过
    robots_skipped = []
    for kind, url in list(targets.items()):
        if not _robots_allowed(url):
            robots_skipped.append((kind, url))
            failures.append(
                {
                    "competitor": cname,
                    "url": url,
                    "kind": kind,
                    "error": "robots.txt disallow",
                }
            )
            del targets[kind]

    # ── 页面级并行(错峰启动;每页任务线程内跑 scrape_smart) ──
    async def _gather():
        async def runner(delay: float, kind: str, url: str):
            await asyncio.sleep(delay)
            return await asyncio.to_thread(
                _fetch_page,
                kind,
                url,
                base=base,
                cname=cname,
                home=home,
                home_md=home_md,
                deadline=deadline,
            )

        tasks = [
            runner(i * _PAGE_STAGGER_S, kind, url)
            for i, (kind, url) in enumerate(targets.items())
        ]
        return await asyncio.gather(*tasks)

    page_results = list(asyncio.run(_gather())) if targets else []

    # ── 顺序组装(确定性落盘顺序,与旧串行版一致) ──
    by_kind = {t["kind"]: t for t in page_results}
    for kind in _PAGE_ORDER:
        task = by_kind.get(kind)
        if not task:
            continue
        pages[kind] = task["pages"]
        failures.extend(task["failures"])
        for u, r, k in task["records"]:
            _record(u, r, k)

    # ── 落盘 ──
    with _MANIFEST_LOCK:
        (raw_dir / f"{cname}.engines.json").write_text(
            json.dumps(engines_doc, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        (raw_dir / f"{cname}.md").write_text(
            "\n\n---\n\n".join(raw_sections), encoding="utf-8"
        )
        manifest_path = out_dir / "claims-manifest.json"
        manifest = {
            "run": {
                "topic": topic,
                "started_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                "pipeline_version": "3.1",
            },
            "fetched": fetched,
            "claims": [],
            "failures": failures,
        }
        if manifest_path.exists():
            try:
                old = json.loads(manifest_path.read_text(encoding="utf-8"))
                for url, ent in fetched.items():
                    if url in old.get("fetched", {}):
                        oe = old["fetched"][url]
                        for k in ent.get("kinds", []):
                            if k not in oe.setdefault("kinds", [oe.get("kind")]):
                                oe["kinds"].append(k)
                        if ent.get("status") == "ok":
                            oe["status"] = "ok"
                        for eng, meta in (ent.get("engines") or {}).items():
                            oe.setdefault("engines", {})[eng] = meta
                    else:
                        old.setdefault("fetched", {})[url] = ent
                old.setdefault("failures", []).extend(failures)
                manifest = old
            except Exception:
                pass
        if topic:
            manifest.setdefault("run", {})["topic"] = topic
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
    ap.add_argument("--topic", default="", help="分析主题(写入台账 run.topic)")
    args = ap.parse_args()
    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)
    names = [x.strip() for x in args.competitors.split(",") if x.strip()]
    results: List[tuple] = []

    # 竞品级并行:不同域互不干扰;jina 限流/页面错峰在引擎与页面层控制
    with ThreadPoolExecutor(max_workers=min(_COMPETITOR_CONCURRENCY, len(names))) as ex:
        futs = {
            ex.submit(fetch_competitor, n, out_dir, args.budget, args.topic): n
            for n in names
        }
        for fut in futs:
            t0 = time.time()
            r = fut.result()
            elapsed = time.time() - t0
            results.append((r, elapsed))

    ok = True
    for r, elapsed in results:
        insuff = [k for k, v in r["pages"].items() if not v["sufficient"]]
        print(
            f"[{'✓' if r['pages'] else '✗'} {r['name']}] {elapsed:5.1f}s | "
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
