#!/usr/bin/env python3
"""verify 的离线门禁集合(G1-G6)。每个 gate 函数签名:
    def gX(analysis: dict, manifest: dict, engine_index: dict, rep: Report) -> None
"""

import re

from verify import Report, norm_ws

_GATES = []


def register(fn):
    _GATES.append(fn)
    return fn


def run_all(analysis, manifest, engine_index, rep: Report):
    for g in _GATES:
        g(analysis, manifest, engine_index, rep)


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


# ── G3 定价完整性 ──

import calendar  # noqa: E402
import time as _time  # noqa: E402

from verify import TTL_DAYS  # noqa: E402

_TS_FMT = "%Y-%m-%d %H:%M UTC"


def _ts_age_days(ts: str) -> float:
    """UTC 时间戳距今天数;解析失败返回 inf(视为最陈旧)。

    用 calendar.timegm 而非 time.mktime —— 后者按本地时区解析,
    会把 UTC 时间戳算偏数小时(TTL=14 天粒度下不致命,但没理由错)。
    """
    try:
        t = calendar.timegm(_time.strptime(ts or "", _TS_FMT))
    except (ValueError, OverflowError):
        return float("inf")
    return (_time.time() - t) / 86400.0


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
