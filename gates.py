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
