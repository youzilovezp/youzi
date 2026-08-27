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
        "pricing_source",
        "tagline_source",
        "founded_source",
        "headquarters_source",
        "team_size_source",
    )
    for k in singles:
        u = (competitor.get(k) or "").strip()
        if u:
            yield f"competitors[{name}].{k}", u
    for i, t in enumerate(competitor.get("pricing_tiers") or []):
        u = (t.get("source_url") or "").strip()
        if u:
            yield f"competitors[{name}].pricing_tiers[{i}].source_url", u
    for key in ("strengths", "weaknesses"):
        for i, s in enumerate(competitor.get(key) or []):
            u = (
                (s.get("source") or s.get("source_url") or "")
                if isinstance(s, dict)
                else ""
            ).strip()
            if u:
                yield f"competitors[{name}].{key}[{i}].source", u
    for key in ("gtm_evidence", "moat_evidence"):
        for i, ev in enumerate(competitor.get(key) or []):
            u = (ev.get("source") or "").strip() if isinstance(ev, dict) else ""
            if u:
                yield f"competitors[{name}].{key}[{i}].source", u
    for i, t in enumerate(competitor.get("tech_signals") or []):
        if isinstance(t, dict):
            u = (t.get("source") or t.get("source_url") or "").strip()
        else:  # 兼容 "name|url" / 纯文本形态
            u = ""
            m = re.search(r"(https?://\S+)", str(t))
            u = m.group(1).rstrip(").,]") if m else ""
        if u:
            yield f"competitors[{name}].tech_signals[{i}].source", u
    # differentiators:结构化(dict 带 source_url)才溯源;纯字符串形态
    # 由分析框架 Step 3 约束(见 G7 docstring),门禁只查能查的
    for i, d in enumerate(competitor.get("differentiators") or []):
        if isinstance(d, dict):
            u = (d.get("source_url") or d.get("source") or "").strip()
            if u:
                yield f"competitors[{name}].differentiators[{i}].source_url", u
    for i, fb in enumerate(competitor.get("user_feedback") or []):
        if isinstance(fb, dict):
            u = (fb.get("source") or fb.get("source_url") or "").strip()
            if u:
                yield f"competitors[{name}].user_feedback[{i}].source", u
    # feature_catalog:仅检查非空 source(空 = 未定位出处,允许)
    fc = competitor.get("feature_catalog") or {}
    for cname, feats in fc.items():
        for i, f in enumerate(feats or []):
            u = (f.get("source") or "").strip() if isinstance(f, dict) else ""
            if u:
                yield f"competitors[{name}].feature_catalog[{cname}][{i}].source", u
    # product_momentum(§6 数据增强):date/text 必须锚定真实抓到的页面
    for i, pm in enumerate(competitor.get("product_momentum") or []):
        u = (
            (pm.get("source") or pm.get("source_url") or "").strip()
            if isinstance(pm, dict)
            else ""
        )
        if u:
            yield f"competitors[{name}].product_momentum[{i}].source", u


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
                    "G1",
                    field,
                    url,
                    "source_url 不在本轮抓取记录中(未访问过的 URL 不得充当来源)",
                    "删除该字段,或重爬该 URL;绝不允许引用未抓取的地址",
                )
            elif ent.get("status") != "ok":
                rep.hard(
                    "G1",
                    field,
                    url,
                    f"source_url 本轮抓取失败(status={ent.get('status')})",
                    "失败页面的 URL 不能当来源;改为「未验证」并记录 failure",
                )


_STRENGTH_QUOTE_RX = re.compile(r'官网原文:\s*"(.+?)"\s*"?$')

# 与 V1 _find_evidence_lines 同款 markdown 清洗 —— 引文展示侧
# 已把 ![alt](url)→alt / [text](url)→text,回查侧必须同样归一化,否则
# 清洗后的干净引文永远 grep 不到含原始语法的引擎原文(G2 闭环自抓)
import functools  # noqa: E402

_MD_IMG_RX = re.compile(r"!\[([^\]]*)\]\([^)]+\)")
_MD_LNK_RX = re.compile(r"\[([^\]]+)\]\([^)]+\)")


@functools.lru_cache(maxsize=64)
def _norm_md_stripped(md: str) -> str:
    s = _MD_IMG_RX.sub(r"\1", md)
    s = _MD_LNK_RX.sub(r"\1", s)
    return norm_ws(s)


def _quote_grep(quote: str, url: str, engine_index: dict) -> bool:
    """quote 在该 URL 任一引擎原文中归一化命中。

    两级归一化:空白折叠(折行/多空格)+ markdown 链接语法剥离
    (与证据侧 _find_evidence_lines 的清洗对称)。
    """
    q = norm_ws(quote)
    if not q:
        return True  # 空 quote 由 G4 管
    for md in (engine_index.get(url) or {}).values():
        if q in norm_ws(md) or q in _norm_md_stripped(md):
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
                    checks.append(
                        (
                            f"competitors[{name}].strengths[{i}].evidence",
                            (s.get("source") or "").strip(),
                            m.group(1),
                        )
                    )
        for key in ("gtm_evidence", "moat_evidence"):
            for i, ev in enumerate(competitor.get(key) or []):
                if isinstance(ev, dict) and ev.get("quote"):
                    checks.append(
                        (
                            f"competitors[{name}].{key}[{i}].quote",
                            (ev.get("source") or "").strip(),
                            ev["quote"],
                        )
                    )
        # differentiators 结构化(dict {point, quote, source_url})后,
        # 引文与 strengths 同等对待 —— 5.2.3 独家卖点不允许只挂 URL 不对原文
        for i, d in enumerate(competitor.get("differentiators") or []):
            if isinstance(d, dict) and d.get("quote"):
                checks.append(
                    (
                        f"competitors[{name}].differentiators[{i}].quote",
                        (d.get("source_url") or d.get("source") or "").strip(),
                        d["quote"],
                    )
                )
        # product_momentum:title 必须在该 source 页任一引擎原文中逐字命中
        # (§6 时间线的数据增强与 5.2.3 独家功能同一条 quote 铁律)
        for i, pm in enumerate(competitor.get("product_momentum") or []):
            if isinstance(pm, dict) and pm.get("title"):
                checks.append(
                    (
                        f"competitors[{name}].product_momentum[{i}].title",
                        (pm.get("source") or pm.get("source_url") or "").strip(),
                        pm["title"],
                    )
                )
        # 定价来自缓存回退(反爬 starved)时,vote 行是上一轮成功运行的证据,
        # 本轮引擎原文天然不包含 —— 无法也不应 grep(新鲜度由 G3 TTL 保证,
        # 报告端有 pricing_crawl_note 如实标注)
        if competitor.get("pricing_from_cache"):
            continue
        for i, v in enumerate(competitor.get("pricing_vote_detail") or []):
            if isinstance(v, dict) and (v.get("raw_line") or v.get("line")):
                checks.append(
                    (
                        f"competitors[{name}].pricing_vote_detail[{i}].line",
                        (competitor.get("pricing_source") or "").strip(),
                        # raw_line = 引擎逐字原文;line 可能是套餐名前缀融合的
                        # 合成串(不可 grep)—— 优先 raw_line,旧数据回退 line
                        v.get("raw_line") or v.get("line"),
                    )
                )
        for field, url, quote in checks:
            if not url:
                continue  # 无来源的引文归 G1/G4
            if not _quote_grep(quote, url, engine_index):
                rep.hard(
                    "G2",
                    field,
                    url,
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
                "G3",
                f"{field}.pricing_tiers",
                src,
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
                "G3",
                f"{field}.pricing_engines",
                src,
                f"verified 定价的内容独立引擎不足({len(hashes)} 个不同哈希,"
                f"engines={engines})—— 同一变体页被多引擎抓到不算交叉验证",
                "重爬(换网络环境/等反爬窗口),或降级 pricing_verified=false",
            )
        age = _ts_age_days(competitor.get("pricing_scraped_at"))
        if age > TTL_DAYS:
            rep.hard(
                "G3",
                f"{field}.pricing_scraped_at",
                src,
                f"定价证据已陈旧({age:.0f} 天前,TTL={TTL_DAYS} 天)",
                "重爬定价页刷新证据",
            )


# ── G4/G5/G6 ──

from urllib.parse import urlparse  # noqa: E402

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
                "G4",
                "manifest.failures",
                url,
                "抓取失败的 URL 没有进 failures 清单(静默吞掉)",
                "把该失败写入 manifest.failures {competitor,url,kind,error}",
            )

    # b) 缺失字段不得断言来源
    for competitor in analysis.get("competitors") or []:
        name = competitor.get("name", "?")
        for field in ("founded", "headquarters", "team_size", "tagline"):
            if (
                _is_missing(competitor.get(field))
                and (competitor.get(f"{field}_source") or "").strip()
            ):
                rep.hard(
                    "G4",
                    f"competitors[{name}].{field}_source",
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
                rep.hard(
                    "G5",
                    path,
                    "",
                    f"命中历史伪造引文黑名单: “{obj[:60]}…”",
                    "删除或替换为 02-raw 可 grep 的真实引文",
                )
            elif _REPR_LEAK_RX.search(obj):
                rep.hard(
                    "G5",
                    path,
                    "",
                    f"Python repr 泄漏: {obj[:60]}",
                    "数据应为字符串/数组,不是 str(list/dict) 产物",
                )
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
            blob = (
                " ".join(str(v) for v in (item or {}).values())
                if isinstance(item, dict)
                else str(item)
            )
            if "待补充" in blob:
                rep.hard(
                    "G5",
                    f"{key}[{i}]",
                    "",
                    "派生板块出现「待补充」占位符",
                    f"{key} 要么有证据支撑,要么整条删除",
                )


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
        own = _registrable_domain(urlparse(competitor.get("url") or "").hostname or "")
        for field, url in _evidence_fields(competitor):
            p = urlparse(url)
            if p.scheme not in ("http", "https") or not p.netloc:
                rep.hard(
                    "G6",
                    field,
                    url,
                    "URL 格式非法(须为绝对 http(s) 地址)",
                    "修正为合法 URL 或清空",
                )
                continue
            src = _registrable_domain(p.hostname or "")
            if own and src and src != own:
                rep.warn(
                    "G6",
                    field,
                    f"证据域名 {src} 与竞品主域 {own} 不同"
                    "(第三方来源需在 Step 3 确认已实际抓取)",
                )


# ── G7 溯源权威性 ──

# 明显定价语义:货币符号 + 价格数字(如 $24 / ₹999 / US$12 / $.012),
# 或中文价格语境词(免费/定价/价格/计费 —— R2-C 语义级弱锚判定)
_PRICING_SEMANTICS_RX = re.compile(
    r"(?:US\$|S\$|Rs\.?|[$€£₹¥])\s?(?:\d[\d,\.]*|\.\d+)"
    r"|免费|定价|价格|计费"
)


def _is_pricing_semantic(text: str) -> bool:
    return bool(_PRICING_SEMANTICS_RX.search(text or ""))


def _weak_anchor(url: str) -> str:
    """锚点强度:'pricing' = 定价页路径;'root' = 域名根(无路径);'' = 具体子页。

    ponytail: 只认末段 segment 精确等于 pricing —— /en/pricing、/pricing/
    都命中;博客 slug 里含 pricing 的长段不误伤。
    """
    p = urlparse(url or "")
    segs = [s for s in (p.path or "").split("/") if s]
    if segs and segs[-1].lower() == "pricing":
        return "pricing"
    if p.netloc and not segs:
        return "root"
    return ""


@register
def g7_source_authority(analysis, manifest, engine_index, rep: Report):
    """G7: 功能/技术/差异化类证据的溯源权威性。

    历史缺陷:5.2.3 独家功能与 5.4 技术信号的「原文」链接几乎全是
    /pricing 与官网首页 —— 读者点开找不到论断内容,溯源形同虚设。

    规则(analysis-framework.md §溯源优先级):
      - tech_signals / differentiators / feature_catalog 的来源锚定在
        定价页路径或域名根 → hard fail;唯一豁免:quote 本身是定价陈述
        (货币符号+价格数字 / 价格语境词)
      - strengths / weaknesses(R2-C 扩展):条目文本(point+quote)无价格
        语义却锚定定价页/域名根 → hard fail —— 定价陈述锚 pricing 合理
        保留,功能/技术/口碑陈述锚 pricing/首页是语义错位
      - user_feedback 锚定定价页 → hard fail;域名根 → 仅警告
        (官网首页确实承载用户声音,但应优先 customers/testimonials 页)
      - differentiators 纯字符串形态不查(结构由 Step 3 框架强制 dict 化)
    """
    for competitor in analysis.get("competitors") or []:
        name = competitor.get("name", "?")
        checks = []  # (field, url, quote, hard)
        for key in ("strengths", "weaknesses"):
            for i, s in enumerate(competitor.get(key) or []):
                if not isinstance(s, dict):
                    continue
                u = (s.get("source") or s.get("source_url") or "").strip()
                if not u:
                    continue  # 未断言来源的诚实留空,G1/G4 语义之外
                text = " ".join(
                    x
                    for x in (
                        s.get("point") or "",
                        s.get("evidence") or "",
                        s.get("quote") or "",
                    )
                    if x
                )
                checks.append((f"competitors[{name}].{key}[{i}]", u, text, True))
        for i, d in enumerate(competitor.get("differentiators") or []):
            if isinstance(d, dict):
                u = (d.get("source_url") or d.get("source") or "").strip()
                if u:
                    checks.append(
                        (
                            f"competitors[{name}].differentiators[{i}]",
                            u,
                            d.get("quote") or "",
                            True,
                        )
                    )
        for i, t in enumerate(competitor.get("tech_signals") or []):
            if isinstance(t, dict):
                u = (t.get("source") or t.get("source_url") or "").strip()
                q = t.get("quote") or ""
            else:
                m = re.search(r"(https?://\S+)", str(t))
                u, q = (m.group(1).rstrip(").,]") if m else ""), ""
            if u:
                checks.append((f"competitors[{name}].tech_signals[{i}]", u, q, True))
        for i, fb in enumerate(competitor.get("user_feedback") or []):
            if isinstance(fb, dict):
                u = (fb.get("source") or fb.get("source_url") or "").strip()
                if u:
                    checks.append(
                        (
                            f"competitors[{name}].user_feedback[{i}]",
                            u,
                            fb.get("quote") or "",
                            False,
                        )
                    )
        fc = competitor.get("feature_catalog") or {}
        for cname, feats in fc.items():
            for i, f in enumerate(feats or []):
                if isinstance(f, dict):
                    u = (f.get("source") or f.get("source_url") or "").strip()
                    if u:
                        checks.append(
                            (
                                f"competitors[{name}].feature_catalog[{cname}][{i}]",
                                u,
                                f.get("quote") or f.get("text_orig") or "",
                                True,
                            )
                        )
        for field, url, quote, hard in checks:
            weak = _weak_anchor(url)
            if not weak or _is_pricing_semantic(quote):
                continue
            what = "定价页路径" if weak == "pricing" else "域名根(首页/栏目 landing)"
            if hard:
                rep.hard(
                    "G7",
                    field,
                    url,
                    f"功能/技术/口碑类证据锚定在{what} —— 该页面不承载论断原文",
                    "改锚 docs/features 具体子页(quote 逐字取自该页),或删除该条;"
                    "仅当条目本身为定价陈述(货币+数字/免费/定价/价格/计费)才允许 pricing 锚点",
                )
            else:
                rep.warn(
                    "G7",
                    field,
                    f"用户反馈锚定在域名根 —— 优先改锚 customers/testimonials 具体页: {url}",
                )
