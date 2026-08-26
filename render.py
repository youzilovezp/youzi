#!/usr/bin/env python3
"""
youzi · 零依赖 HTML 报告渲染器

读取 analysis JSON + report.html 模板，输出精美 HTML 报告。
无任何第三方依赖（不用 jinja2 / 不用 npm）。

Usage:
    python3 render.py --input 03-analysis.json --output report.html
    python3 render.py --input 03-analysis.json --output report.html --template templates/report.html
"""

import argparse
import json
import re
import sys
import html
import logging
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_TEMPLATE = HERE / "templates" / "report.html"

# 生产级日志:默认 INFO, --verbose 提升到 DEBUG
logger = logging.getLogger("youzi.render")
if not logger.handlers:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# ============================================================
# 评分维度（6 维）—— 用于校准每个竞品 scores 字段
# ============================================================
SCORE_DIMS = [
    ("feature_richness", "功能丰富度"),
    ("ux", "用户体验"),
    ("pricing_value", "定价性价比"),
    ("integration", "生态集成"),
    ("ai_capability", "AI 能力"),
    ("momentum", "增长势头"),
]


# ============================================================
# 模板引擎：手写递归下降解析器
# 支持：
#   {{ var.path }}       - 转义变量
#   {{! var.path }}      - 原始 HTML（不转义）
#   {% for x in path %}...{% endfor %}  - 循环
#   {% if expr %}...{% endif %}         - 条件
#   {{ loop.index }}     - 当前循环索引（1-based）
#   {% if x %}body{% endif %}
# ============================================================
class Template:
    """Jinja2 渲染器。

    - ChainableUndefined: 缺失字段退化为 ''（避免模板里写大量 {% if %}）
    - autoescape: HTML/XML/XHTML 文件自动转义，防 XSS
    """

    def __init__(self, source: str):
        from jinja2 import Environment, ChainableUndefined, select_autoescape

        self._env = Environment(
            undefined=ChainableUndefined,
            autoescape=select_autoescape(("html", "htm", "xml", "xhtml")),
        )
        self._template = self._env.from_string(source)

    def render(self, ctx: dict) -> str:
        return self._template.render(**ctx)


def smart_icon(name: str) -> str:
    """从名称提取简洁的 logo 文字（1-2 个字符）。

    优先级：
    1. 英文部分（如 "IDE-Agent" → "ID"）
    2. 首个汉字（如 "通义灵码" → "通义"）
    3. 前 2 字 fallback
    """
    if not name:
        return "?"
    # 找首个英文/数字
    m = re.search(r"[A-Za-z0-9]+", name)
    if m:
        word = m.group(0)
        # 大写首字母组合（最多 2）
        return word[:2].upper()
    # 否则取前 2 个汉字
    han = re.findall(r"[一-鿿]", name)
    if len(han) >= 2:
        return han[0] + han[1]
    return name[:2]


def slugify(name: str) -> str:
    s = re.sub(r"[^\w一-鿿]+", "-", name).strip("-").lower()
    return s or "x"


def _truncate_with_ellipsis(text: str, max_len: int) -> str:
    """超长文本截断到 max_len,末尾追加 '…'。

    None/空 文本会得到 '—'。截断长度 ≤ max_len 时不追加省略号。
    """
    safe = text if text else "—"
    if len(safe) <= max_len:
        return safe
    return safe[:max_len] + "…"


# ============================================================
# 派生：从 13 字段 JSON 推出飞书模板所需的所有结构
# ============================================================
_ANGLE_KEYWORDS = {
    "产品": ["产品", "UX", "界面", "体验", "设计", "编辑器", "协作", "功能"],
    "技术": ["技术", "架构", "AI", "模型", "引擎", "API", "性能", "算法"],
    "市场": ["市场", "增长", "用户", "客户", "份额", "营销", "GTM", "出海"],
    "商业": ["商业", "定价", "盈利", "营收", "订阅", "付费", "转化", "毛利"],
    "生态": ["生态", "集成", "合作伙伴", "API", "开放", "开发者", "插件"],
    "团队": ["团队", "组织", "工程", "文化", "招聘", "远程"],
}


def _classify_angle(text: str) -> str:
    """根据关键词把一段竞品优点/缺点归类到 6 个角度之一。"""
    if not text:
        return "产品"
    best = ("产品", 0)
    for angle, kws in _ANGLE_KEYWORDS.items():
        score = sum(1 for kw in kws if kw in text)
        if score > best[1]:
            best = (angle, score)
    return best[0]


def _derive_inspiration_points(competitors):
    """从 strengths 派生 inspiration_points: {angle: [{competitor, good, inspiration, evidence, _ref}]}"""
    result: dict = {}
    for c in competitors:
        for s in c.get("strengths", []):
            if _is_placeholder_swot(s):
                continue  # 占位符不是真实优点,派生成"可借鉴实践"就是伪造
            point = s.get("point", "")
            if not point:
                continue
            angle = _classify_angle(point)
            result.setdefault(angle, []).append(
                {
                    "competitor": c["name"],
                    "good": point,
                    "evidence": s.get("evidence", ""),
                    "inspiration": f"可借鉴 {c['name']} 的实践：{point[:30]}",
                    "_ref": s.get("_ref", 0),
                }
            )
    return result


def _derive_opportunity_points(competitors):
    """从 weaknesses 派生 opportunity_points: {angle: [{competitor, weakness, opportunity, evidence, _ref}]}"""
    result: dict = {}
    for c in competitors:
        for w in c.get("weaknesses", []):
            if _is_placeholder_swot(w):
                continue  # 占位符不能变成"差异化机会"
            point = w.get("point", "")
            if not point:
                continue
            angle = _classify_angle(point)
            result.setdefault(angle, []).append(
                {
                    "competitor": c["name"],
                    "weakness": point,
                    "evidence": w.get("evidence", ""),
                    "opportunity": f"差异化机会：解决 {c['name']} 没做好的「{point[:20]}」",
                    "_ref": w.get("_ref", 0),
                }
            )
    return result


def _group_inspiration_by_competitor(inspiration_points, competitors):
    """按竞品重排 inspiration_points。

    输出: {comp_name: [{angle, good, evidence, inspiration, _ref}, ...]}
    —— 让 §2.4 可以按厂商查阅,不混在角度分类里。
    """
    result: dict = {c["name"]: [] for c in competitors}
    for angle, items in inspiration_points.items():
        for it in items:
            comp = it.get("competitor")
            if comp in result:
                result[comp].append(
                    {
                        "angle": angle,
                        "good": it["good"],
                        "evidence": it.get("evidence", ""),
                        "inspiration": it["inspiration"],
                        "_ref": it.get("_ref", 0),
                    }
                )
    return result


def _group_opportunity_by_competitor(opportunity_points, competitors):
    """按竞品重排 opportunity_points。"""
    result: dict = {c["name"]: [] for c in competitors}
    for angle, items in opportunity_points.items():
        for it in items:
            comp = it.get("competitor")
            if comp in result:
                result[comp].append(
                    {
                        "angle": angle,
                        "weakness": it["weakness"],
                        "evidence": it.get("evidence", ""),
                        "opportunity": it["opportunity"],
                        "_ref": it.get("_ref", 0),
                    }
                )
    return result


_GLOSSARY_GENERIC = [
    ("SaaS", "Software as a Service,按月付费的在线软件,不用自己装服务器。"),
    ("CDP", "Customer Data Platform,客户数据平台。打通用户在多个渠道的数据,知道同一客户在不同渠道的所有行为。"),
    ("momentum", "增长势头。综合了产品迭代速度、客户增长、媒体声量。10 分 = 爆炸增长,3 分 = 停滞。"),
    ("API", "Application Programming Interface,程序之间的\"数据服务员\"。可以问它要数据或让它干活。"),
    ("Webhook", "\"反向 API\"。当某事件发生时,服务器主动推送通知给你。比轮询省资源。"),
    ("SLA", "Service Level Agreement,服务等级协议。99.9% SLA 表示一年最多 8 小时停机。"),
    ("SOC2 / HIPAA", "国际合规认证。SOC2 = 企业数据安全;HIPAA = 美国医疗数据合规。大客户采购必备。"),
]
_GLOSSARY_WHATSAPP = [
    ("BSP", "Business Solution Provider,WhatsApp 官方授权的中间商。中小公司要找他们才能拿到 WhatsApp API(就像旅行社代理机票)。"),
    ("CTWA 广告", "Click-to-WhatsApp Ads,Meta 出的新型广告。用户在 Instagram/Facebook 点广告,直接跳到 WhatsApp 对话。"),
    ("Shopify 集成", "和 Shopify 电商平台无缝对接。可以同步订单、库存、客户,实现 WhatsApp 订单通知。"),
]


def _build_glossary(topic: str) -> list:
    """按主题生成术语表 —— WhatsApp 专属词条只在 WhatsApp 主题出现。
    历史缺陷:硬编码的 WhatsApp 术语表(BSP/CTWA)对 AI 编程助手等任何主题都显示。"""
    items = list(_GLOSSARY_GENERIC)
    if "whatsapp" in (topic or "").lower():
        items = _GLOSSARY_WHATSAPP + items
    return [{"term": t, "explain": e} for t, e in items]


def _derive_user_positioning(c):
    """从 target_users + stage + founded 派生 user_positioning[name]。"""
    users = c.get("target_users", [])
    stage = c.get("stage", "")
    founded = c.get("founded", "—")
    return {
        "target_segment": "、".join(users) if users else "—",
        # 历史缺陷:这里曾硬编码 region="全球" —— 数据里没有就如实写未采集,不猜
        "region": c.get("region") or "未采集",
        # scale 由 stage 推断;stage 缺失("—"/空)时不再兜底"中大型企业"
        # (真实事故:三家全是占位 stage,§3.2 规模列全是"中大型企业" = 伪事实)
        "scale": (
            "中小企业" if stage in ("早期", "成长期")
            else "中大型企业" if stage in ("成熟期", "巨头")
            else "—"
        ),
        "key_market": stage if stage and stage != "—" else "—",
        "founded": founded,  # 加 founded 字段,§3.2 显示
    }


def _derive_commercial_strategies(c):
    """从 pricing_tiers + differentiators 派生商业策略。

    优先使用结构化的 c['pricing_tiers'] (list[{name, price, billing_period, features, source_url}])
    fallback 到老的 c['pricing'] 字符串切分。

    differentiators 可能是 list[str] (原始) 或 list[{name, source, _ref}] (Phase 1A 后),
    这里统一取 .name 兼容两种格式。
    """
    pricing_tiers_raw = c.get("pricing_tiers", [])
    pricing_str = c.get("pricing", "—")
    differentiators = c.get("differentiators", [])

    def _name(it):
        return it["name"] if isinstance(it, dict) else it

    diff_names = [_name(d) for d in differentiators]

    # 1) 优先使用结构化 tiers
    structured_tiers = []
    if isinstance(pricing_tiers_raw, list) and pricing_tiers_raw and isinstance(pricing_tiers_raw[0], dict):
        for t in pricing_tiers_raw:
            structured_tiers.append({
                "name": t.get("name", "—"),
                "price": t.get("price", "—"),
                "billing_period": t.get("billing_period", "—"),
                "features": t.get("features", []),
                "source_url": t.get("source_url", ""),
            })

    # 2) fallback: 从 pricing 字符串切分。
    #    兼容两种格式:老格式 "+、；;" 分隔;新格式 " / " 分隔(跨引擎投票输出,
    #    含 "Growth · $59 (month billed annually)" 三元组)。
    if not structured_tiers and pricing_str and pricing_str != "—":
        segments = re.split(r"\s/\s|[+、；;]", pricing_str)
        for t in segments:
            t = t.strip().rstrip("….。").strip()  # 去截断残留省略号
            if not t or t.startswith("…") or set(t) <= set("…(见官网)"):
                continue
            price_m = re.search(r"(?:US\$|\$|€|£|S\$|₹|Rs\.?)\s?\d[\d,\.]*|免费|Free|联系销售|Contact Sales", t, re.I)
            bill_m = re.search(r"billed\s+(annually|monthly)|per\s+(?:month|year)|/mo(?:nth)?|/yr|/年|/月", t, re.I)
            # 名称 = 去掉价格/计费括号后的剩余;套餐名词命中才可信,
            # 整句营销文案("Use the ₹999 credits for sending...")不是套餐名
            name = re.sub(r"\s*\([^)]{0,60}\)\s*$", "", t)
            if price_m:
                name = name.replace(price_m.group(0), "").strip(" ·-—")
            name = name[:40]
            _plan_rx = re.compile(
                r"starter|growth|\bpro\b|plus|business|enterprise|team|basic|free\b"
                r"|solo|scale|advanced|essential|standard|premium|lite", re.I,
            )
            if not _plan_rx.search(name):
                # 没有套餐名词:短而干净(<25 字)可当名称,长句营销文案不行
                name = name if 0 < len(name) <= 25 and len(name.split()) <= 4 else "—"
            structured_tiers.append({
                "name": name or "—",
                "price": price_m.group(0) if price_m else "—",
                "billing_period": (bill_m.group(1) if bill_m.lastindex else bill_m.group(0)) if bill_m else "—",
                "features": [],
                "source_url": "",
            })
        structured_tiers = structured_tiers[:6]

    # 3) 推导 model(基于结构化 tiers 的价格模式)—— 看 tier 全文(名称+价格),
    #    拆分后 price 字段只有裸数字,周期信息在 name/billing_period 里
    if structured_tiers:
        blob = " ".join(f"{t['name']} {t['price']} {t['billing_period']}" for t in structured_tiers).lower()
        has_monthly = any(k in blob for k in ("/月", "/mo", "month", "monthly"))
        has_yearly = any(k in blob for k in ("/年", "/yr", "year", "annual"))
        has_free = any(k in blob for k in ("免费", "free", "trial"))
        has_contact = any(k in blob for k in ("联系销售", "contact sales", "enterprise", "custom"))
        if has_free and (has_monthly or has_yearly):
            model = "免费增值(Freemium + 订阅)"
        elif has_contact and not has_monthly and not has_yearly:
            model = "企业定制(Enterprise Only)"
        elif has_monthly or has_yearly:
            model = "SaaS 订阅(月付/年付)"
        else:
            model = "订阅制(细节见定价)"
    else:
        model = "未公开"

    # 展示串:模板直接用,避免对 dict 列表 join 出 Python repr(历史 bug)。
    # 去重("未公开 / 未公开 / 未公开" 只显示一次);per-user 计价时标注单位。
    # 套餐名保留并分组:同套餐的月付/年付合并 "Growth $39·/mo|$468·/yr",
    # 无名的裸价格串曾是 "$0/yr / $39/mo / $468/yr…" 交错乱码
    unit = (c.get("pricing_unit") or "").strip()
    groups: list = []
    for t in structured_tiers:
        if t["price"] != "—":
            seg = f"{t['price']}" + (
                f"·{t['billing_period']}" if t["billing_period"] not in ("—", "") else ""
            )
        else:
            seg = t["name"][:40]
        name = (t.get("name") or "").strip()
        if name and name != "—" and groups and groups[-1][0] == name:
            groups[-1][1].append(seg)
        else:
            groups.append((name if name and name != "—" else "", [seg]))
    display_parts, seen_disp = [], set()
    seen_prices = set()  # "未公开"类无名价格只显示一次(历史去重语义)
    for name, segs in groups:
        seg = (" ".join([name] if name else []) + " " + "|".join(segs)).strip() \
            if len(segs) > 1 else ((f"{name} " if name else "") + segs[0]).strip()
        if seg in seen_disp:
            continue
        if (
            not any(re.search(r"\d", p) for p in segs)
            and all(p in seen_prices for p in segs)
        ):
            continue  # 无数字价格(未公开/—)重复段:跳过
        seen_disp.add(seg)
        seen_prices.update(segs)
        display_parts.append(seg)
    pricing_display = " / ".join(display_parts) or "—"
    if unit and pricing_display != "—":
        pricing_display = f"{pricing_display}（{unit}）"

    return {
        "model": model,
        "pricing_tiers": structured_tiers,
        "pricing_display": pricing_display,
        # GTM: 走 Go-to-Market 角度
        "gtm": _derive_gtm(c, diff_names),
        # 护城河:从 strengths 推断
        "moat": _derive_moat(c, diff_names),
    }


def _derive_gtm(c, diff_names):
    """GTM:优先用爬取的证据推导(官网 CTA/定位原文,每条带 quote+source);
    其次 differentiators;无证据时如实标待补(绝不编造)。
    """
    ev = c.get("gtm_evidence") or []
    if ev:
        return "；".join(e["name"] for e in ev[:2])[:120]
    if diff_names:
        return diff_names[0][:80]
    return "—（待 Step 3 基于官网定位/博客证据分析）"


def _derive_moat(c, diff_names):
    """护城河:优先用爬取的客观资产证据(客户数/认证/官方身份,带 quote);
    其次 differentiators;无证据时如实标待补。
    """
    ev = c.get("moat_evidence") or []
    if ev:
        return "；".join(e["name"] for e in ev[:2])[:120]
    if len(diff_names) > 1:
        return diff_names[1][:80]
    if diff_names:
        return diff_names[0][:80]
    return "—（待 Step 3 基于证据分析）"


def _derive_product_overview(c):
    """基于 features + tech_signals 推导产品端覆盖(4 端)。

    设计原则:
      - 每个端 = '支持' / '未明确' / '—'
      - '支持' 必须有证据(在 features 或 tech_signals 中找到关键词)
      - '未明确' 标记: 此能力在公开材料中无明示,但作为现代 SaaS 高度可能
      - '—' 标记: 此能力对赛道不重要(如桌面客户端对纯云端 SaaS)
    """
    feats = c.get("feature_catalog", {}).get(c["name"], [])
    feat_str = " ".join(f.get("name", "") for f in feats)
    feat_str_lower = feat_str.lower()
    tech_signals = c.get("tech_signals", []) or []
    tech_str = " ".join(
        t.get("name", "") if isinstance(t, dict) else str(t) for t in tech_signals
    )
    tech_str_lower = tech_str.lower()

    # Web 控制台 —— 所有 SaaS 都有,但仅在 feature 提及时记 ✓
    web_has_evidence = any(
        kw in feat_str_lower or kw in tech_str_lower
        for kw in ["dashboard", "console", "portal", "admin", "inbox"]
    )
    web = "✓ 支持" if web_has_evidence else "未明确"

    # Desktop 客户端 —— SaaS 较少原生桌面端
    desktop_has_evidence = any(
        kw in feat_str_lower or kw in tech_str_lower
        for kw in ["desktop", "electron", "native app", "windows app", "mac app"]
    )
    desktop = "✓ 支持" if desktop_has_evidence else "— 纯云端"

    # Mobile —— 现代 SaaS 必备,但移动原生 App 较少
    mobile_app_kw = ["ios", "android", "mobile app", "native mobile"]
    mobile_has_evidence = any(kw in tech_str_lower for kw in mobile_app_kw)
    mobile = "✓ 原生 App" if mobile_has_evidence else "✓ 移动端 Web"

    # Other —— API/SDK/集成
    api_has_evidence = any(
        kw in feat_str_lower or kw in tech_str_lower
        for kw in ["api", "sdk", "webhook", "rest", "graphql"]
    )
    other = "✓ API/SDK/Webhook" if api_has_evidence else "未明确"

    return {
        "web": web,
        "desktop": desktop,
        "mobile": mobile,
        "other": other,
        "_evidence": {
            "web": web_has_evidence,
            "desktop": desktop_has_evidence,
            "mobile": mobile_has_evidence,
            "other": api_has_evidence,
        },
    }


def _derive_visual_signals(c):
    """基于 tagline + 核心功能 + 技术栈 推断 UI/UX 风格。

    返回结构化字段(visual_style / interaction_pattern / key_ui_element),
    供 §5.3 视觉卡片直接渲染 —— 比旧版「拼接 tagline + tech 字符串」更可读。
    """
    fc = c.get("feature_catalog", {}).get(c["name"], [])
    fc_names = {f.get("name", "") for f in fc}
    tagline = c.get("tagline", "") or ""

    # 视觉风格推断
    if "AI" in tagline or any("AI" in n for n in fc_names):
        style = "现代 AI 驱动界面,深色主调 + 强调色"
    elif "WhatsApp" in tagline or "WhatsApp" in str(fc_names):
        style = "WhatsApp Web 风格对话列表,左导航 + 中对话区"
    else:
        style = "经典 SaaS 控制台风格"

    # 交互模式推断
    interactions = []
    if any("收件箱" in n or "Inbox" in n for n in fc_names):
        interactions.append("多坐席协作收件箱")
    if any("AI" in n for n in fc_names):
        interactions.append("AI 自动回复")
    if any("工作流" in n or "Workflow" in n for n in fc_names):
        interactions.append("拖拽式工作流")
    if any("模板" in n for n in fc_names):
        interactions.append("模板市场")

    return {
        "visual_style": style,
        "interaction_pattern": " + ".join(interactions)
        if interactions
        else "标准表单式",
        "feature_count": len(fc),
        "tagline": tagline,
    }


def _infer_strengths_weaknesses(c):
    """证据不足时的 SWOT 占位 —— 绝不伪造。

    历史教训:这里曾按关键词模板编造带假 G2 引文的 strengths/weaknesses
    (如 "G2: 'Pricing gets expensive at scale'" —— 从未真的读过 G2),
    且为空时兜底塞 "BSP 接入 WhatsApp Business API" 等与主题无关的话术。
    现在:爬取证据里没有的,如实标「待 Step 3 基于证据补全」。
    """
    placeholder = {
        "point": "待补充：爬取文本中未提取到足够证据（Step 3 由 LLM 基于 02-raw 证据填写）",
        "evidence": "",
        "score": 0,
        "source": "",
    }
    return [dict(placeholder)], [dict(placeholder)]


def _source_label_for_url(url: str) -> str:
    """从 URL 域名推来源标签 —— 标签必须诚实反映链接指向(历史 bug:
    链接是官网却标 'G2 / 评测 / 官方',读者点进去发现根本不是 G2)。"""
    if not url:
        return "来源"
    u = url.lower()
    if "g2.com" in u:
        return "G2 评测"
    if "capterra" in u:
        return "Capterra 评测"
    if "reddit.com" in u:
        return "Reddit 讨论"
    if "trustpilot" in u:
        return "Trustpilot"
    if "crunchbase" in u:
        return "Crunchbase"
    if "36kr.com" in u or "huxiu.com" in u or "sspai.com" in u:
        return "中文媒体报道"
    return "官网页面"


def _is_placeholder_swot(item: dict) -> bool:
    """识别占位 SWOT 条目(爬取证据不足时写入的'待补充'行)。

    这些不是真实反馈,渲染成用户反馈就是伪造 —— 用户反馈 section
    直接跳过(真实事故:每家都显示同一句占位文本 + 假 G2 标签)。
    """
    text = (item.get("point") or item.get("text") or "").strip()
    return (
        not text
        or text.startswith("待补充")
        or text.startswith("待 Step 3")
        or item.get("score") in (0, "0", None) and not item.get("evidence")
    )


def _derive_user_feedback(c):
    """从真实的 strengths/weaknesses 派生用户反馈小结(带诚实来源标签)。

    - 只收有真实证据(有 text 且非占位)的条目
    - source_label 从 URL 域名推导,绝不硬编码 "G2 / 评测 / 官方"
    - strengths/weaknesses 的 source 就是该论断的出处;没有 source 的不渲染链接
    """
    pos = [
        {
            "text": s.get("point", ""),
            "source": s.get("source", ""),
            "source_label": _source_label_for_url(s.get("source", "")),
            "count": s.get("score", "—"),
        }
        for s in c.get("strengths", [])[:3]
        if not _is_placeholder_swot(s)
    ]
    neg = [
        {
            "text": w.get("point", ""),
            "source": w.get("source", ""),
            "source_label": _source_label_for_url(w.get("source", "")),
            "count": w.get("score", "—"),
        }
        for w in c.get("weaknesses", [])[:3]
        if not _is_placeholder_swot(w)
    ]
    summary_parts = []
    if pos:
        summary_parts.append(f"正面：{pos[0].get('text', '')[:30]}")
    if neg:
        summary_parts.append(f"负面：{neg[0].get('text', '')[:30]}")
    summary = "。".join(summary_parts) if summary_parts else "—"
    return {"summary": summary, "positive": pos, "negative": neg}


def _group_competitors_by_segment(competitors, market_segments):
    """按市场细分把竞品聚类,同类放一起(用于 § 2 结论建议)。

    聚类逻辑:
      - 优先按 market_segments[].players 显式分组
      - 同一竞品如果属多个 segment,只放到最匹配的 segment
    """
    if not market_segments:
        # 没有 market_segments 时,按 stage 粗分
        groups = {}
        for c in competitors:
            groups.setdefault(c.get("stage", "未知"), []).append(c)
        return [
            {"segment": k, "segment_desc": "", "competitors": v}
            for k, v in groups.items()
        ]

    groups = []
    assigned = set()
    for seg in market_segments:
        seg_players = set(seg.get("players", []))
        comps = [
            c
            for c in competitors
            if c["name"] in seg_players and c["name"] not in assigned
        ]
        if comps:
            groups.append(
                {
                    "segment": seg.get("label", "其他"),
                    "segment_desc": seg.get("desc", ""),
                    "segment_source": seg.get("_ref", 0),
                    "competitors": comps,
                }
            )
            for c in comps:
                assigned.add(c["name"])
    # 未分配的放 "其他"
    remaining = [c for c in competitors if c["name"] not in assigned]
    if remaining:
        groups.append(
            {
                "segment": "其他",
                "segment_desc": "未归入主分类",
                "segment_source": 0,
                "competitors": remaining,
            }
        )
    return groups


def _group_competitors_by_stage(competitors):
    """按阶段(巨头/成长期/早期)分组。"""
    groups = {}
    for c in competitors:
        s = c.get("stage", "未知")
        groups.setdefault(s, []).append(c)
    return [{"stage": k, "competitors": v} for k, v in groups.items()]


# ─────────────────────────────────────────────────────────
# Canonical 功能集(行业标准) — 用于 § 5.2.1/5.2.2 权威矩阵
#
# 区别于 _DEFAULT_FEATURE_ALIASES(把同义功能合并),
# canonical 是「这家赛道应该具备的能力清单」,每行代表一个标准能力,
# 每个能力有: 中文名 / 英文名 / 类别 / 中文解释 / 行业意义 / 同义别名。
# 渲染时,每个能力对每家厂商判定「支持 ✓ / 未找到公开材料 ?」,
# 缺失时给出「为何缺失」的明确说明(避免出现『有的有、有的没』的空洞矩阵)。
# ─────────────────────────────────────────────────────────
_CANONICAL_FEATURES_WHATSAPP = [
    # ──────────── 消息渠道 (Messaging Channels) ────────────
    {
        "id": "whatsapp_business_api",
        "name_cn": "WhatsApp Business API",
        "name_en": "WhatsApp Business API",
        "category": "消息渠道",
        "importance": "P0 · 基础设施",
        "desc": "Meta 官方的 WhatsApp Business API 接入能力,是 WhatsApp 营销 SaaS 的基础;所有合规平台都必须具备。",
        "why": "无此能力则无法在 WhatsApp 上发送任何消息,平台意义不存在。",
        "aliases": [
            "whatsapp business api",
            "whatsapp api",
            "whatsapp business platform",
            "whatsapp business api account",
            "whatsapp",
        
            "whatsapp business app coexistence",
            "whatsapp blue tick",
            "whatsapp message charges",],
    },
    {
        "id": "whatsapp_calling_api",
        "name_cn": "WhatsApp Calling API",
        "name_en": "WhatsApp Business Calling API",
        "category": "消息渠道",
        "importance": "P1 · 增值能力",
        "desc": "在 WhatsApp 内发起语音通话,用于实时客户服务,提升沟通效率。",
        "why": "WhatsApp 2024 年推出的 Calling 能力,目前支持平台较少,具备此能力是差异化加分项。",
        "aliases": [
            "whatsapp calling",
            "whatsapp business calling api",
            "whatsapp calling api",
            "whatsappcall",
        ],
    },
    {
        "id": "whatsapp_link_generator",
        "name_cn": "WhatsApp 短链/QR 码",
        "name_en": "WhatsApp Link / QR Generator",
        "category": "消息渠道",
        "importance": "P2 · 客户引流",
        "desc": "生成 wa.me 短链/QR 码,客户扫码即可打开与企业的 WhatsApp 对话。",
        "why": "线下门店、广告投放、KOL 引流的核心入口工具。",
        "aliases": [
            "whatsapp link generator",
            "whatsapp qr generator",
            "ctwa",
            "click to whatsapp ad",
            "wa.me link",
        ],
    },
    {
        "id": "whatsapp_shop",
        "name_cn": "WhatsApp Shop 电商",
        "name_en": "WhatsApp Shop / Catalog",
        "category": "消息渠道",
        "importance": "P2 · 电商场景",
        "desc": "在 WhatsApp 内展示产品目录、购物车和结账,实现交易闭环。",
        "why": "Meta 推 WhatsApp Shops 的核心场景,东南亚/中东品牌电商必备。",
        "aliases": [
            "whatsapp shop",
            "whatsapp catalog",
            "whatsapp stores",
            "product catalog",
        ],
    },
    {
        "id": "omnichannel_inbox",
        "name_cn": "多渠道统一收件箱",
        "name_en": "Omnichannel Unified Inbox",
        "category": "消息渠道",
        "importance": "P0 · 基础设施",
        "desc": "WhatsApp + Email + SMS + Live Chat 等多渠道对话统一接入同一个收件箱。",
        "why": "客户跨渠道咨询是常态;统一收件箱避免坐席在不同工具切换。",
        "aliases": [
            "omnichannel",
            "multi-channel",
            "multichannel",
            "live chat",
            "livechat",
            "wechat",
            "instagram",
            "facebook messenger",
            "telegram",
            "sms",
            "email",
            "voice",
        
            "channels and integrations",
            "custom channels",
            "other channels",
            "chats, calls and emails in one thread",
            "multiple channels",],
    },
    # ──────────── 营销自动化 (Marketing Automation) ────────────
    {
        "id": "broadcast_campaign",
        "name_cn": "群发营销 Broadcast",
        "name_en": "Broadcast Campaign",
        "category": "营销自动化",
        "importance": "P0 · 核心变现",
        "desc": "向分群后的客户批量发送 WhatsApp 模板消息(Marketing / Utility / Authentication),是 WhatsApp 营销 SaaS 的核心变现能力。",
        "why": "WhatsApp 收费按对话计费,群发是营收主力;无此能力则无法做营销。",
        "aliases": [
            "broadcast campaign",
            "broadcast",
            "campaign",
            "campaigns",
            "send broadcasts via whatsapp to targeted customers",
            "send promotions and newsletters",
            "promotions",
            "newsletters",
            "bulk message",
        ],
    },
    {
        "id": "journey_builder",
        "name_cn": "客户旅程编排",
        "name_en": "Journey / Flow Builder",
        "category": "营销自动化",
        "importance": "P1 · 自动化进阶",
        "desc": "拖拉拽配置多步骤自动化营销流程(触发 → 条件分支 → 动作),无需代码。",
        "why": "进阶营销自动化,从单次群发升级到完整客户生命周期管理。",
        "aliases": [
            "journey",
            "journey builder",
            "flow builder",
            "automation",
            "visual flow builder",
            "drag-and-drop workflow",
            "workflow automation",
        ],
    },
    {
        "id": "segmentation",
        "name_cn": "客户分群",
        "name_en": "Segmentation",
        "category": "营销自动化",
        "importance": "P0 · 营销前置",
        "desc": "按标签/属性/行为把客户分组,实现精准营销;WhatsApp 对未分群营销有严格限制。",
        "why": "无分群则群发 = 骚扰,封号风险高;Meta 政策强制要求基于用户授权。",
        "aliases": [
            "create custom segment",
            "segment",
            "segments",
            "segmentation",
            "audience",
            "custom segment",
        ],
    },
    {
        "id": "growth_tool",
        "name_cn": "客户增长 / 名单收集",
        "name_en": "Contact Growth Tool",
        "category": "营销自动化",
        "importance": "P1 · 拉新工具",
        "desc": "提供落地页/表单/广告集成等工具,持续获取 WhatsApp 客户名单。",
        "why": "WhatsApp 营销的天花板由名单规模决定,拉新工具是平台重要加分项。",
        "aliases": [
            "growth tool",
            "lead form",
            "lead ads",
            "click to whatsapp",
            "ctwa",
            "form builder",
            "landing page",
        
            "growth tools",
            "lead generation",
            "growth widgets",
            "capture",
            "capture from ads",
            "capture from website",
            "capture from social",
            "capture from offline",
            "qualify leads",
            "route leads",
            "convert",
            "retain",
            "acquire and engage leads",],
    },
    # ──────────── AI 客服 (AI / Chatbot) ────────────
    {
        "id": "ai_chatbot",
        "name_cn": "AI 自动回复 Chatbot",
        "name_en": "AI Chatbot / Agent",
        "category": "AI 客服",
        "importance": "P1 · 降本",
        "desc": "基于 LLM 或 NLP 的自动回复机器人,处理常见咨询,降低人工成本。",
        "why": "降本增效;WhatsApp 客服 70%+ 流量是 FAQ 类问题,理论上都可由 AI 处理。",
        "aliases": [
            "ai agent",
            "chatbots",
            "ai built on a trusted foundation",
            "ai assistant",
            "ai chatbot",
            "chatbot",
            "nlp",
        
            "ai-powered query resolution",
            "conversational ai",
            "no code chatbots",
            "chatbot builder",],
    },
    {
        "id": "ai_human_handoff",
        "name_cn": "AI 转人工升级",
        "name_en": "AI → Human Handoff",
        "category": "AI 客服",
        "importance": "P1 · 体验兜底",
        "desc": "AI 检测到复杂/敏感/用户主动请求时自动升级到人工客服。",
        "why": "避免 AI 误答导致客户流失;是 AI 客服系统的基础兜底机制。",
        "aliases": [
            "human handoff",
            "agent handoff",
            "escalation to human",
            "transfer to human",
            "fallback to agent",
        ],
    },
    {
        "id": "ai_knowledge_training",
        "name_cn": "AI 知识库训练",
        "name_en": "AI Knowledge Base Training",
        "category": "AI 客服",
        "importance": "P2 · 模型效果",
        "desc": "上传文档/FAQ/产品手册让 AI 学习,提升回答准确率。",
        "why": "AI 效果 = 知识库质量;此能力决定 AI 客服实际可用度。",
        "aliases": [
            "knowledge base",
            "knowledge base training",
            "faq training",
            "document training",
            "ai training",
            "lyro",
        ],
    },
    # ──────────── 收件箱 / 协作 (Inbox & Collaboration) ────────────
    {
        "id": "team_inbox",
        "name_cn": "团队共享收件箱",
        "name_en": "Team / Unified Inbox",
        "category": "收件箱协作",
        "importance": "P0 · 客服基础",
        "desc": "客服团队共用一个会话视图,支持多人协作处理同一客户对话。",
        "why": "无团队收件箱 = 单人小作坊,无法支撑企业级客服;行业标配。",
        "aliases": [
            "inbox",
            "team inbox",
            "unified inbox",
            "shared inbox",
            "chats, calls and emails in one thread",
            "manage concurrent customer messages with a unified inbox",
            "agent inbox",
            "multi-agent inbox",
        
            "shared team inbox",
            "team & custom inboxes",
            "caixa de entrada compartilhada",],
    },
    {
        "id": "chat_assignment",
        "name_cn": "对话分配 / 路由",
        "name_en": "Chat Assignment / Routing",
        "category": "收件箱协作",
        "importance": "P1 · 团队协作",
        "desc": "把进入的对话按规则分配到具体坐席(按团队/优先级/语言/客户标签)。",
        "why": "无分配规则 = 抢对话/重复回复,客户体验崩坏。",
        "aliases": [
            "chat assignment",
            "conversation routing",
            "conversation assignment",
            "routing rules",
            "round robin",
            "auto-assign",
        ],
    },
    {
        "id": "internal_notes",
        "name_cn": "内部备注 / 协作",
        "name_en": "Internal Notes / @mention",
        "category": "收件箱协作",
        "importance": "P2 · 团队协作",
        "desc": "客服内部 @同事 / 留言 / 备注,客户不可见。",
        "why": "团队协作必备,客户对话上下文传递。",
        "aliases": [
            "internal notes",
            "internal chat",
            "@mention",
            "team chat",
            "备注",
            "内部备注",
        ],
    },
    # ──────────── CRM / 客户数据 ────────────
    {
        "id": "social_crm",
        "name_cn": "Social CRM 客户画像",
        "name_en": "Social CRM",
        "category": "CRM / 客户数据",
        "importance": "P2 · 差异化",
        "desc": "整合客户在 WhatsApp 的画像(标签/属性/历史对话),建立 360° 客户视图。",
        "why": "WhatsApp 上的客户对话是非结构化金矿,Social CRM 是变现关键。",
        "aliases": [
            "social crm",
            "crm",
            "customer profile",
            "contact 360",
            "contact management",
        ],
    },
    {
        "id": "tags_custom_attrs",
        "name_cn": "客户标签 / 自定义属性",
        "name_en": "Tags & Custom Attributes",
        "category": "CRM / 客户数据",
        "importance": "P1 · 基础能力",
        "desc": "为客户打标签 / 加自定义属性,支持精准分群和个性化沟通。",
        "why": "无标签 = 无法分群;WhatsApp 营销前提是合规分群。",
        "aliases": [
            "tags",
            "tag",
            "custom attribute",
            "custom attributes",
            "custom field",
            "标签",
        ],
    },
    # ──────────── 电商 / Commerce ────────────
    {
        "id": "payment_links",
        "name_cn": "支付链接 Payment Links",
        "name_en": "Payment Links",
        "category": "电商 / Commerce",
        "importance": "P2 · 交易闭环",
        "desc": "在对话中发送支付链接(Stripe / 微信 / 支付宝),客户点击即可付款。",
        "why": "客服对话中直接成交的关键工具,提升转化率。",
        "aliases": [
            "payment links",
            "payment link",
            "stripe",
            "checkout",
            "支付链接",
            "购物车恢复",
            "cart recovery",
        ],
    },
    # ──────────── 集成 (Integration) ────────────
    {
        "id": "shopify_integration",
        "name_cn": "Shopify 电商集成",
        "name_en": "Shopify Integration",
        "category": "集成 / Integration",
        "importance": "P1 · 跨境标配",
        "desc": "对接 Shopify,同步订单/库存/客户,支持购物车恢复和发货通知。",
        "why": "Shopify 是独立站首选,WhatsApp 客服 + Shopify 是 DTC 标配。",
        "aliases": [
            "shopify",
            "shopify 集成",
            "shopify inbox",
            "shopify integration",
            "woocommerce",
            "magento",
            "e-commerce",
        
            "native integrations",],
    },
    {
        "id": "hubspot_crm_integration",
        "name_cn": "HubSpot / Salesforce CRM 集成",
        "name_en": "HubSpot / Salesforce CRM",
        "category": "集成 / Integration",
        "importance": "P1 · 企业级",
        "desc": "对接 HubSpot / Salesforce 等主流 CRM,同步 contacts / deals / 通讯记录。",
        "why": "WhatsApp 对话进入 CRM 是销售跟进的关键。",
        "aliases": [
            "hubspot",
            "hubspot crm integration",
            "salesforce",
            "zoho",
            "crm 集成",
            "crm integration",
        ],
    },
    {
        "id": "zapier_make",
        "name_cn": "Zapier / Make 自动化集成",
        "name_en": "Zapier / Make Integration",
        "category": "集成 / Integration",
        "importance": "P2 · 扩展性",
        "desc": "通过 Zapier / Make.com 连接 5000+ SaaS 工具做无代码自动化。",
        "why": "无原生集成的工具,可通过 Zapier 桥接;扩展平台适用场景。",
        "aliases": [
            "zapier",
            "make.com",
            "zapier integration",
            "make integration",
            "automation",
        
            "integrations",
            "integration",],
    },
    {
        "id": "rest_api",
        "name_cn": "REST API 开放接口",
        "name_en": "REST API",
        "category": "集成 / Integration",
        "importance": "P1 · 开发者",
        "desc": "公开 REST API 供开发者把 WhatsApp 集成到自有系统。",
        "why": "企业级客户必须能 API 集成,否则无法嵌入业务流程。",
        "aliases": [
            "rest api",
            "api",
            "open api",
            "restful api",
            "public api",
        
            "developer api",
            "developer hub",
            "http requests in workflows",
            "api management",],
    },
    {
        "id": "webhooks",
        "name_cn": "Webhook 事件推送",
        "name_en": "Webhooks",
        "category": "集成 / Integration",
        "importance": "P1 · 实时",
        "desc": "新消息/状态变更等事件实时推送到开发者服务器,降低轮询成本。",
        "why": "WhatsApp 对话状态变化(已读/已送达/客户回复)需要 Webhook 实时同步。",
        "aliases": [
            "webhook",
            "webhooks",
            "webhook events",
            "events api",
            "callback",
        ],
    },
    # ──────────── 分析 (Analytics) ────────────
    {
        "id": "analytics",
        "name_cn": "营销分析 / 数据洞察",
        "name_en": "Marketing Analytics",
        "category": "分析 / Analytics",
        "importance": "P1 · 决策支撑",
        "desc": "营销活动分析(送达率/打开率/回复率/转化漏斗),支撑 ROI 决策。",
        "why": "WhatsApp 按对话计费,数据驱动优化是降本关键。",
        "aliases": [
            "analytics",
            "data-driven insights",
            "insights",
            "数据洞察",
            "get data-driven insights",
            "campaign analytics",
            "sales analytics",
            "marketing analytics",
            "reports",
        
            "reports",
            "advanced reports",
            "basic reports",],
    },
    {
        "id": "data_export",
        "name_cn": "数据导出",
        "name_en": "Data Export",
        "category": "分析 / Analytics",
        "importance": "P1 · 合规",
        "desc": "联系人 / 对话 / 分析数据批量导出(CSV/Excel/API)。",
        "why": "GDPR 合规与数据迁移必备;企业采购硬性要求。",
        "aliases": [
            "data export",
            "export",
            "csv export",
            "excel export",
            "数据导出",
            "导出",
        ],
    },
    # ──────────── 数据安全 (Security & Compliance) ────────────
    {
        "id": "rbac",
        "name_cn": "角色权限管理 RBAC",
        "name_en": "Role-Based Access (RBAC)",
        "category": "数据安全",
        "importance": "P1 · 企业级",
        "desc": "多角色多坐席权限管理,支持细粒度访问控制。",
        "why": "企业客户必备;无 RBAC = 无法管理客服团队数据权限。",
        "aliases": [
            "rbac",
            "role-based access",
            "团队成员角色",
            "角色权限",
            "团队协作",
            "team permissions",
            "workspace",
            "sub-account",
            "iam",
        
            "system and customized roles",
            "system roles only",
            "unlimited users",
            "team management",],
    },
    {
        "id": "data_security",
        "name_cn": "数据安全 / 加密",
        "name_en": "Data Security / Encryption",
        "category": "数据安全",
        "importance": "P1 · 合规",
        "desc": "数据传输加密 / 静态加密 / SOC2 / GDPR / ISO27001 等合规认证。",
        "why": "金融 / 医疗 / 跨境客户硬性要求;WhatsApp 涉及个人数据必须合规。",
        "aliases": [
            "data security",
            "encryption",
            "gdpr",
            "soc2",
            "iso 27001",
            "compliance",
            "数据安全",
            "security",
        ],
    },
    # ──────────── 开发者 (Developer) ────────────
    {
        "id": "sandbox",
        "name_cn": "沙箱 / 测试环境",
        "name_en": "Sandbox / Test Environment",
        "category": "开发者",
        "importance": "P2 · 体验加分",
        "desc": "提供测试环境 / Mock 账号 / 测试号码供开发者集成验证。",
        "why": "企业集成需要沙箱联调,无沙箱 = 集成风险高。",
        "aliases": [
            "sandbox",
            "test environment",
            "test mode",
            "staging",
            "demo",
            "沙箱",
        ],
    },
    {
        "id": "csat_survey",
        "name_cn": "满意度调研 CSAT/NPS",
        "name_en": "CSAT / NPS Surveys",
        "category": "分析 / Analytics",
        "importance": "P2 · 体验度量",
        "desc": "对话结束自动发送满意度调研,收集 CSAT/NPS 反馈。",
        "why": "客服质量可量化的唯一手段;影响 SLA 考核与服务改进。",
        "aliases": ["csat", "nps", "csat or nps surveys", "satisfaction survey",
                     "满意度调研", "满意度调查"],
    },
    {
        "id": "appointments",
        "name_cn": "预约管理",
        "name_en": "Book Appointments",
        "category": "业务自动化",
        "importance": "P2 · 场景增值",
        "desc": "在对话中完成预约创建/提醒/改期(服务/医疗/教育场景刚需)。",
        "why": "预约类业务(诊所/美容/教育)的核心闭环;减少 no-show。",
        "aliases": ["book appointments", "appointment", "appointments",
                     "appointment scheduling", "预约管理", "预约",
                     "renewal reminders"],
    },
    {
        "id": "mobile_app",
        "name_cn": "移动端 App",
        "name_en": "Mobile App",
        "category": "收件箱协作",
        "importance": "P1 · 随时响应",
        "desc": "iOS/Android 原生 App,坐席随时随地处理会话。",
        "why": "客服主管/老板外出时处理紧急会话的刚需;竞品覆盖差异点。",
        "aliases": ["mobile app", "ios app", "android app", "移动端",
                     "移动应用", "手机app"],
    },
    {
        "id": "success_services",
        "name_cn": "客户成功服务",
        "name_en": "Professional / Success Services",
        "category": "收件箱协作",
        "importance": "P2 · 企业服务",
        "desc": "专属客户成功经理/入驻协助/优先工单等人工服务。",
        "why": "中大客户续费的关键;判断厂商目标客群(企业级 vs SMB)。",
        "aliases": ["professional services", "dedicated account manager",
                     "customer success", "onboarding service",
                     "self-onboarding", "self-serve onboarding",
                     "customer service", "customer support"],
    },
]


# 中文别名注入:18/28 个 canonical 原本只有英文别名,中文竞品(Meetbot)
# 的功能名("精准营销"/"人群打标圈选")永远配不上 → 矩阵整列 ?
# (真实事故:Meetbot 28 行只命中 1 行)
_CANONICAL_ZH_ALIASES = {
    "whatsapp_business_api": ["官方API", "API接入", "商业API", "接口对接"],
    "whatsapp_calling_api": ["语音通话", "通话", "语音API", "语音渠道"],
    "whatsapp_link_generator": ["短链", "短链接", "二维码", "链接生成"],
    "whatsapp_shop": ["商品目录", "店铺", "电商", "商城", "商品展示"],
    "omnichannel_inbox": ["统一收件箱", "多渠道", "全渠道", "聚合聊天", "多平台接入"],
    "broadcast_campaign": ["群发", "广播", "批量发送", "群播", "营销群发"],
    "journey_builder": ["客户旅程", "自动化流程", "工作流", "流程编排", "自动化"],
    "segmentation": ["分群", "圈选", "打标", "人群", "分层", "客户分群"],
    "growth_tool": ["名单收集", "获客", "引流", "增长工具", "私域增长", "裂变"],
    "ai_chatbot": ["智能回复", "自动回复", "机器人", "智能客服", "AI回复", "智能体"],
    "ai_human_handoff": ["转人工", "人工客服", "人工接管", "人机协作"],
    "ai_knowledge_training": ["知识库", "FAQ训练", "知识训练"],
    "team_inbox": ["共享收件箱", "团队收件箱", "多人协作", "坐席协作"],
    "chat_assignment": ["对话分配", "分配", "路由", "调度", "工单分配"],
    "internal_notes": ["备注", "内部协作", "协作备注"],
    "social_crm": ["客户画像", "CRM", "画像", "客户管理", "客户资料"],
    "tags_custom_attrs": ["自定义属性", "自定义字段", "标签管理", "用户标签"],
    "payment_links": ["支付链接", "收款链接", "支付"],
    "shopify_integration": ["电商对接", "独立站对接", "店铺同步"],
    "hubspot_crm_integration": ["CRM集成", "CRM对接"],
    "zapier_make": ["自动化集成", "第三方集成", "集成平台"],
    "rest_api": ["开放接口", "API接口", "接口"],
    "webhooks": ["事件推送", "回调", "webhook推送"],
    "analytics": ["数据分析", "数据看板", "报表", "分析", "统计", "数据洞察"],
    "data_export": ["导出", "数据导出", "批量导出"],
    "rbac": ["权限管理", "角色权限", "子账号", "多角色"],
    "data_security": ["数据安全", "加密", "安全认证", "合规"],
    "sandbox": ["测试环境", "沙箱环境", "试用环境"],
}
for _f in _CANONICAL_FEATURES_WHATSAPP:
    _zh = _CANONICAL_ZH_ALIASES.get(_f["id"], [])
    if _zh:
        _f["aliases"] = list(_f.get("aliases", [])) + _zh



# ─────────────────────────────────────────────────────────
# 内置默认别名库 — 覆盖常见集成/CDP/收件箱场景(零配置启用)
# 优先级最低,被用户配置和自动检测覆盖
# ─────────────────────────────────────────────────────────
_DEFAULT_FEATURE_ALIASES = {
    "团队收件箱 (Team Inbox)": {
        "aliases": [
            "团队收件箱",
            "团队共享收件箱",
            "Team Inbox",
            "Shared Inbox",
            "协作收件箱",
            "Agent Inbox",
            "Multi-Agent Inbox",
        ],
        "rationale": "本质都是「多坐席共享同一个对话列表」,各家产品命名不同。",
    },
    "对话分配 (Chat Assignment)": {
        "aliases": [
            "对话分配",
            "对话分配规则",
            "Chat Assignment",
            "Conversation Routing",
            "Conversation Assignment",
        ],
        "rationale": "把进入的对话按规则分配到坐席(团队/优先级/语言)。",
    },
    "REST API 开放接口": {
        "aliases": ["REST API", "API", "Open API", "RESTful API"],
        "rationale": "公开 REST 接口供开发者集成。",
    },
    "Webhook 事件推送": {
        "aliases": [
            "Webhook 双向",
            "Webhook 事件",
            "Webhook",
            "Webhook Events",
            "Webhook 事件回调",
            "Events API",
            "Webhook Callbacks",
            "新消息/状态变更推送",
        ],
        "rationale": "新消息/状态变更等事件实时推送到开发者服务器。",
    },
    "电商集成 (E-commerce)": {
        "aliases": [
            "Shopify 集成",
            "Shopify 一键连接",
            "Shopify Inbox",
            "Shopify 深度",
            "WooCommerce",
            "WooCommerce 连接",
            "WooCommerce 集成",
            "E-commerce Integration",
            "Magento Integration",
        ],
        "rationale": "对接电商平台同步订单/库存/客户。Shopify/WooCommerce/Magento 三大主流。",
    },
    "HubSpot CRM 集成": {
        "aliases": ["HubSpot 集成", "HubSpot 连接", "HubSpot CRM Integration"],
        "rationale": "对接 HubSpot 同步 contacts/deals/通讯记录。",
    },
    "Zapier 自动化集成": {
        "aliases": [
            "Zapier 集成",
            "Zapier 连接",
            "Zapier/Make",
            "Zapier Integration",
            "Make.com 集成",
            "Make Integration",
        ],
        "rationale": "通过 Zapier/Make.com 连接 5000+ SaaS 工具做自动化。",
    },
    "Mailchimp 集成": {
        "aliases": ["Mailchimp", "Mailchimp 集成", "Mailchimp Integration"],
        "rationale": "对接 Mailchimp 做邮件营销协同。",
    },
    "Instagram 多渠道接入": {
        "aliases": ["Instagram DM", "Instagram 集成", "Instagram Integration"],
        "rationale": "把 Instagram Direct Message 接入统一收件箱。",
    },
    "Facebook Messenger 接入": {
        "aliases": [
            "Facebook Messenger",
            "Messenger",
            "FB Messenger",
            "Facebook Messenger Integration",
        ],
        "rationale": "对接 Meta 的 Facebook Messenger 多渠道能力。",
    },
    "Telegram 接入": {
        "aliases": ["Telegram", "Telegram Bot", "Telegram Integration"],
        "rationale": "对接 Telegram 多渠道能力。",
    },
    "WhatsApp Business API": {
        "aliases": [
            "WhatsApp Business API",
            "WhatsApp Business Platform",
            "WhatsApp Business",
            "WhatsApp",
            "WhatsApp API",
        ],
        "rationale": "Meta 官方 WhatsApp Business API 直连。",
    },
    "Email API": {
        "aliases": [
            "Email API",
            "SendGrid Email",
            "Transactional Email",
            "SMTP API",
            "Email Service",
        ],
        "rationale": "事务性 + 营销邮件 API(SendGrid 等)。",
    },
    "SMS API": {
        "aliases": [
            "Programmable Messaging",
            "SMS/MMS/RCS",
            "SMS API",
            "Programmable SMS",
            "Messaging API",
        ],
        "rationale": "短信/MMS/RCS 统一消息 API。",
    },
    "CDP 客户数据平台": {
        "aliases": [
            "Segment CDP",
            "People CDP",
            "Personas 身份解析",
            "Identity Resolution",
            "CDP",
            "Customer Data Platform",
        ],
        "rationale": "跨渠道客户数据平台,统一用户身份与画像。",
    },
    "可视化拖拽流程编辑器": {
        "aliases": [
            "可视化拖拽流程",
            "拖拽流程编辑器",
            "Visual Flow Builder",
            "Drag-and-Drop Workflow",
            "Flow Builder",
        ],
        "rationale": "零代码拖拽式工作流编辑。",
    },
    "行业模板库 (Workflow Templates)": {
        "aliases": [
            "模板市场",
            "工作流模板库",
            "Workflow Templates",
            "Template Library",
            "Template Marketplace",
        ],
        "rationale": "预制行业流程模板让用户快速上手。",
    },
    "内部备注 (Internal Notes)": {
        "aliases": [
            "内部备注",
            "Internal Notes",
            "Internal Chat",
            "@mention",
            "Team Chat",
        ],
        "rationale": "团队成员私下沟通,客户不可见。",
    },
    "AI 转人工升级": {
        "aliases": [
            "AI 转人工",
            "AI 智能路由",
            "Human Handoff",
            "Agent Handoff",
            "Escalation to Human",
        ],
        "rationale": "AI 检测到复杂问题后自动升级到人工客服。",
    },
    "知识库训练 (Knowledge Base Training)": {
        "aliases": [
            "AI 知识库训练",
            "Lyro 知识库训练",
            "Knowledge Base",
            "FAQ Training",
            "Document Training",
        ],
        "rationale": "上传文档/FAQ 让 AI 自动学习。",
    },
    # 企业级基础能力 — 每家都应有
    "多渠道接入 (Omnichannel Aggregation)": {
        "aliases": [
            "多渠道接入",
            "8 渠道统一",
            "Omnichannel",
            "Multi-channel Inbox",
            "Channel API",
            "Channels API",
            "渠道聚合",
        ],
        "rationale": "统一接入 WhatsApp/SMS/Email/Voice/Messenger 等多渠道。",
    },
    "数据导出 (Data Export)": {
        "aliases": [
            "数据导出",
            "Data Export",
            "导出",
            "CSV/Excel 导出",
            "Insights Export",
            "Message Export",
            "Conversation Export",
            "对话记录导出",
            "分析数据导出",
        ],
        "rationale": "联系人/对话/分析数据批量导出 — 企业合规与迁移必备。",
    },
    "权限与团队管理 (RBAC)": {
        "aliases": [
            "权限与角色管理",
            "权限与团队管理",
            "RBAC",
            "Role-Based Access",
            "团队协作",
            "角色权限",
            "Team Permissions",
            "Workspace 隔离",
            "Sub-account",
            "IAM",
            "团队成员角色",
            "Operator 角色权限",
        ],
        "rationale": "多角色多坐席团队协作必备。RBAC + 工作空间隔离。",
    },
    "审计日志 (Audit Logs)": {
        "aliases": [
            "审计日志",
            "Audit Logs",
            "操作日志",
            "Audit Trail",
            "Compliance Logs",
            "API 调用记录",
        ],
        "rationale": "SOC2/HIPAA/ISO27001 等合规审计必需。",
    },
    }


def _auto_detect_aliases(competitors):
    """自动检测:同名功能出现在 ≥2 家厂商 → 自动合并到 canonical。

    Returns:
        dict: 同 feature_aliases 格式
    """
    from collections import defaultdict

    name_to_vendors: dict = defaultdict(set)
    for c in competitors:
        comp = c.get("name", "")
        for feat in c.get("feature_catalog", {}).get(comp, []):
            fname = feat.get("name", "").strip()
            if fname:
                name_to_vendors[fname].add(comp)

    auto: dict = {}
    for name, vendors in name_to_vendors.items():
        if len(vendors) >= 2:
            # 同名出现在 2+ 家 → 自动合并
            vlist = sorted(vendors)
            auto[name] = {
                "aliases": [name],
                "rationale": f'自动检测: "{name}" 在 {len(vendors)} 家厂商出现 — '
                f"{', '.join(vlist)}。",
                "_auto_detected": True,
            }
    return auto


def _merge_alias_layers(user_aliases, auto_aliases, default_aliases):
    """三层合并:用户 > 自动检测 > 默认库

    用户配置最高优先级,可覆盖其他两层。
    自动检测发现的别名被记录(_auto_detected=True)。
    """
    merged: dict = {}
    # 第 1 层:默认别名库
    for canonical, info in default_aliases.items():
        merged[canonical] = info
    # 第 1.5 层:预先索引 — 默认/已合并 canonical 的所有 aliases (lower) → canonical
    canonical_by_alias_lower: dict = {}
    for canonical, info in merged.items():
        if isinstance(info, dict):
            for a in info.get("aliases", []):
                canonical_by_alias_lower[a.strip().lower()] = canonical
            canonical_by_alias_lower[canonical.strip().lower()] = canonical
    # 第 2 层:自动检测
    for canonical, info in auto_aliases.items():
        # 如果 auto canonical 已经在某个默认 canonical 的 aliases 里 → 合并
        existing_canonical = canonical_by_alias_lower.get(canonical.strip().lower())
        if existing_canonical and existing_canonical != canonical:
            # auto canonical 其实是已有 canonical 的别名 → 把 auto 的 _comps 加到 existing
            if isinstance(merged[existing_canonical], dict):
                # 触发:把 auto_aliases 中的 _comps 信息合并 (这里 auto_aliases 只标记名)
                pass
            continue  # 不创建新条目
        if canonical not in merged:
            merged[canonical] = info
        else:
            # 同名 canonical — 合并 aliases
            existing_aliases = set(
                merged[canonical].get("aliases", [])
                if isinstance(merged[canonical], dict)
                else [canonical]
            )
            new_aliases = info.get("aliases", []) if isinstance(info, dict) else []
            for a in new_aliases:
                if a not in existing_aliases:
                    existing_aliases.add(a)
                    if isinstance(merged[canonical], dict):
                        merged[canonical].setdefault("aliases", []).append(a)
    # 第 3 层:用户配置 — 同 canonical 名合并别名,不覆盖别名清单
    # 如果用户在默认库已有同名 canonical,合并两边的 aliases
    for canonical, info in (user_aliases or {}).items():
        if (
            canonical in merged
            and isinstance(merged[canonical], dict)
            and isinstance(info, dict)
        ):
            # 合并 aliases:用户 + 默认(去重)
            user_aliases_list = info.get("aliases", [])
            default_aliases_list = merged[canonical].get("aliases", [])
            merged_aliases = list(
                dict.fromkeys(user_aliases_list + default_aliases_list)
            )
            merged[canonical] = {
                **merged[canonical],
                **info,
                "aliases": merged_aliases,
            }
        else:
            merged[canonical] = info
    return merged


def _build_feature_comparison_matrix(competitors, feature_aliases=None):
    """构造 § 5.2 的厂商对比矩阵。

    Args:
        competitors: 竞品列表(每个含 feature_catalog)
        feature_aliases: 同义别名映射,格式:
            {
                "canonical_name": {
                    "aliases": ["alias1", "alias2", ...],  # 含 canonical_name 自身
                    "rationale": "为什么这些是同一个功能",
                }
            }
            若提供,会把同名/近名功能合并到 canonical_name,并记录每家厂商的实际叫法。

    自动合并能力(三层优先级):
      1. **用户配置的 feature_aliases**(最高优先级)
      2. **自动检测**:同名功能在 ≥2 家厂商出现 → 自动合并
      3. **内置默认别名库**:覆盖常见场景(集成/CDP/收件箱 等)
    """

    # ── 自动检测阶段 ──
    auto_detected = _auto_detect_aliases(competitors)
    # ── 合并默认 + 自动 + 用户(用户最高优先级) ──
    effective_aliases = _merge_alias_layers(
        user_aliases=feature_aliases,
        auto_aliases=auto_detected,
        default_aliases=_DEFAULT_FEATURE_ALIASES,
    )
    feature_aliases = effective_aliases  # 后续逻辑直接使用合并后的
    # 解析 aliases → 反向索引: alias_text_lower → (canonical_name, rationale)
    alias_index: dict = {}
    if feature_aliases:
        for canonical, info in feature_aliases.items():
            rationale = info.get("rationale", "") if isinstance(info, dict) else ""
            for alias in (
                info.get("aliases", []) if isinstance(info, dict) else [canonical]
            ):
                key = alias.strip().lower()
                if key:
                    alias_index[key] = (canonical, rationale)

    # 收集所有 category 和 feature(只遍历一次,避免重复计数)
    cat_features = {}  # cat -> list of merged feature dict
    cat_coverage = {}  # cat -> {competitor_name: count}
    totals_per_competitor = {}

    for c in competitors:
        comp_name = c["name"]
        feats = c.get("feature_catalog", {}).get(comp_name, [])
        totals_per_competitor[comp_name] = len(feats)

        for feat in feats:
            cat = feat.get("category") or "其他"
            fname = feat.get("name", "")
            fdesc = feat.get("desc", "")
            fref = feat.get("_ref", 0)

            # 查 alias 表:用 canonical_name 作为合并键
            lookup = alias_index.get(fname.strip().lower())
            if lookup:
                canonical_name, rationale = lookup
            else:
                canonical_name, rationale = fname, ""

            cat_features.setdefault(cat, [])
            # 累计 coverage(每个厂商每个 canonical 功能只算 1 次)
            cat_coverage.setdefault(cat, {})
            already_in_canonical = any(
                ex["name"] == canonical_name and comp_name in ex["_comps"]
                for ex in cat_features[cat]
            )
            if not already_in_canonical:
                cat_coverage[cat][comp_name] = cat_coverage[cat].get(comp_name, 0) + 1

            # 合并:同 category 同 canonical_name 合并 comps
            existing = None
            for ex in cat_features[cat]:
                if ex["name"] == canonical_name:
                    existing = ex
                    break
            if existing is None:
                cat_features[cat].append(
                    {
                        "name": canonical_name,
                        "desc": fdesc,
                        "_comps": [comp_name],
                        "_display_names": (
                            {comp_name: fname} if fname != canonical_name else {}
                        ),
                        "_rationale": rationale,
                        "_ref": fref,
                        # 记录每家厂商自己的来源 URL (在 normalize 中转为 _ref)
                        "_vendor_sources": {comp_name: feat.get("source", "") or ""},
                        # 记录每家厂商自己的原始描述 —— 防误判:用户可对比确认同义
                        "_vendor_descs": {comp_name: feat.get("desc", "") or ""},
                    }
                )
            else:
                if comp_name not in existing["_comps"]:
                    existing["_comps"].append(comp_name)
                if fname != canonical_name:
                    existing.setdefault("_display_names", {})[comp_name] = fname
                if rationale and not existing.get("_rationale"):
                    existing["_rationale"] = rationale
                # 累计各家来源 URL (同名同 category 不同厂商可能用不同 source)
                if feat.get("source"):
                    existing.setdefault("_vendor_sources", {})[comp_name] = feat[
                        "source"
                    ]
                # 累计各家原始描述
                if feat.get("desc"):
                    existing.setdefault("_vendor_descs", {})[comp_name] = feat["desc"]

    categories = []
    for cat_name in sorted(cat_features.keys()):
        # 排序:独家优先 → 多家共有 → 名称
        feat_sorted = sorted(
            cat_features[cat_name],
            key=lambda x: (
                1 if len(x.get("_comps", [])) == 1 else 0,  # 独家的排后面(更稀有)
                -len(x.get("_comps", [])),
                x["name"],
            ),
        )
        categories.append(
            {
                "name": cat_name,
                "total_features": len(feat_sorted),
                "coverage": cat_coverage.get(cat_name, {}),
                "features": feat_sorted,
            }
        )

    totals_per_category = {c["name"]: c["total_features"] for c in categories}

    # ── 跨类别合并 ──
    # 当某功能被 alias 合并后,可能因为各家把功能归在不同的 category
    # (例如 WATI 把 AI 放在「AI 客服」,Respond.io 把同类放在「AI」),
    # 导致同一个 canonical 功能在矩阵里出现多行。这里按 canonical name 全局合并,
    # 保留首次出现的 category 作为合并后的归属。
    if feature_aliases:
        global_index: dict = {}  # canonical_name -> (category, feat_obj)
        # 先建立首次出现索引(同类别按列表顺序,跨类别按 categories 列表顺序)
        for cat in categories:
            for f in list(cat["features"]):  # snapshot 以避免修改迭代器
                canon = f["name"]
                if canon in global_index:
                    prev_cat, prev_feat = global_index[canon]
                    if prev_cat == cat["name"]:
                        continue  # 同类别已在前面循环处理过
                    # 跨类别:合并到首次出现的 category,标记为待删除
                    for comp in f["_comps"]:
                        if comp not in prev_feat["_comps"]:
                            prev_feat["_comps"].append(comp)
                    for comp, dn in (f.get("_display_names") or {}).items():
                        prev_feat.setdefault("_display_names", {})[comp] = dn
                    if f.get("_rationale") and not prev_feat.get("_rationale"):
                        prev_feat["_rationale"] = f["_rationale"]
                    # 合并各家来源
                    for comp, src in (f.get("_vendor_sources") or {}).items():
                        if src:
                            prev_feat.setdefault("_vendor_sources", {})[comp] = src
                    for comp, d in (f.get("_vendor_descs") or {}).items():
                        if d:
                            prev_feat.setdefault("_vendor_descs", {})[comp] = d
                    f["_to_remove"] = True
                else:
                    global_index[canon] = (cat["name"], f)
        # 移除标记待删除的项
        for cat in categories:
            cat["features"] = [f for f in cat["features"] if not f.get("_to_remove")]
        # 重新统计 total_features / coverage
        for cat in categories:
            cat["total_features"] = len(cat["features"])
            new_cov = {}
            for f in cat["features"]:
                for comp in f["_comps"]:
                    new_cov[comp] = new_cov.get(comp, 0) + 1
            cat["coverage"] = new_cov
        totals_per_category = {c["name"]: c["total_features"] for c in categories}

    return {
        "categories": categories,
        "totals_per_competitor": totals_per_competitor,
        "totals_per_category": totals_per_category,
        "competitor_names": [c["name"] for c in competitors],
    }


def _build_canonical_matrix(competitors, canonical_features):
    """构造 § 5.2.1/5.2.2 的「权威」功能对比矩阵。

    与 _build_feature_comparison_matrix 的关键区别:
      - **行 = canonical 功能(行业标准)**(不是厂商原始功能)
      - 每个 canonical 功能固定有: 中文名 / 英文名 / 类别 / 中文释义 / 行业意义 / 同义别名
      - 对每家厂商的每个 canonical 功能,判定:
          · supports=True + evidence  → ✓ (该厂商原始 feature_catalog 中有同义别名)
          · supports=False             → ? (公开材料未发现),并附「为何缺失」说明
      - **绝不会出现「空行」**;canonical 是赛道应有的能力清单,即使所有厂商都没有也保留行
      - 每个判定附带 confidence(高/中/低)和 source URL

    Args:
        competitors: 竞品列表,每个含 feature_catalog: {vendor_name: [feat, ...]}
        canonical_features: 标准功能清单(见 _CANONICAL_FEATURES_WHATSAPP 格式)

    Returns:
        dict:
          - categories: 按类别聚合后的功能列表(每个含完整 vendor 判定)
          - canonical_features: 全局 canonical 功能列表(扁平)
          - competitor_names: 列出的厂商顺序
          - totals_per_competitor: {vendor: 命中 canonical 数}
          - totals_per_category: {category: 该类别 canonical 数}
          - confidence_counts: {vendor: {high: N, med: N, low: N}}
    """
    comp_names = [c["name"] for c in competitors]
    # 1) 索引每个厂商的 (alias_lower, original_name) → feature dict
    #    来源含 feature_catalog + tech_signals(docs 页提取的真实技术能力,
    #    如 WATI docs 的 "REST API"/"Webhooks" —— 历史缺陷:矩阵只看
    #    feature_catalog,docs 页证据完全没参与判定)
    comp_feat_index: dict = {}
    for c in competitors:
        cname = c["name"]
        feats = list(c.get("feature_catalog", {}).get(cname, []))
        for t in c.get("tech_signals", []):
            tname = (t.get("name") if isinstance(t, dict) else t) or ""
            tsrc = (t.get("source") if isinstance(t, dict) else "") or ""
            for part in re.split(r"[（(·/]", tname):
                part = part.strip()
                if part and len(part) >= 3:
                    feats = feats + [{"name": part, "source": tsrc, "desc": ""}]
        idx: dict = {}
        for f in feats:
            fname = (f.get("name", "") or "").strip()
            if fname:
                idx[fname.lower()] = f
        comp_feat_index[cname] = idx

    # 2) 对每个 canonical 功能,对每个厂商做匹配
    enriched_features: list = []
    totals_per_competitor: dict = {cn: 0 for cn in comp_names}
    confidence_counts: dict = {
        cn: {"high": 0, "medium": 0, "low": 0} for cn in comp_names
    }

    for canon in canonical_features:
        aliases_lower = [a.strip().lower() for a in canon.get("aliases", [])]
        # canonical 自己也作为一个 alias 用于匹配(厂商直接叫 canonical_name)
        if canon.get("name_en"):
            aliases_lower.append(canon["name_en"].strip().lower())
        if canon.get("name_cn"):
            aliases_lower.append(canon["name_cn"].strip().lower())
        aliases_lower = list({a for a in aliases_lower if a})

        per_vendor: dict = {}
        support_count = 0
        for cname in comp_names:
            idx = comp_feat_index.get(cname, {})
            matched_feat = None
            matched_alias = None
            match_kind = ""  # exact=完全同名 / fuzzy=词边界模糊命中
            # 完全相等优先(高置信)
            for a in aliases_lower:
                if a in idx:
                    matched_feat = idx[a]
                    matched_alias = a
                    match_kind = "exact"
                    break
            if matched_feat is None:
                # 词边界匹配:厂商 feature_name 按整词(+可选复数后缀)包含 alias。
                # 历史缺陷1:裸子串匹配让 "email marketing" 命中 omnichannel_inbox
                # 的 "email" 别名 → 整行误判 ✓;修复时又引入缺陷2:严格边界把
                # "Webhooks"/"Inboxes"/"AI Agents" 挡在 "webhook"/"inbox"/
                # "ai agent" 之外(复数形式),矩阵 ✓ 率从 9% 卡死。
                # 现规则:alias 后允许恰好一个复数后缀 s/es,其余仍按整词。
                for vendor_fname_lower, feat in idx.items():
                    for a in aliases_lower:
                        # CJK 别名 2 字即整词("打标"/"圈选");英文别名 ≥4 防
                        # 短缩写误配。CJK 无复数后缀,直接整词匹配。
                        has_cjk = bool(re.search(r"[\u4e00-\u9fff]", a))
                        min_len = 2 if has_cjk else 4
                        if len(a) >= min_len and re.search(
                            rf"(?<![a-z0-9]){re.escape(a)}(?:s|es)?(?![a-z0-9])"
                            if not has_cjk
                            else re.escape(a),
                            vendor_fname_lower,
                        ):
                            matched_feat = feat
                            matched_alias = a
                            match_kind = "fuzzy"
                            break
                    if matched_feat is not None:
                        break

            if matched_feat is not None:
                support_count += 1
                totals_per_competitor[cname] += 1
                is_fuzzy = match_kind == "fuzzy"
                if is_fuzzy:
                    confidence_counts[cname]["medium"] += 1
                else:
                    confidence_counts[cname]["high"] += 1
                per_vendor[cname] = {
                    "status": "supports",
                    "evidence_feature_name": matched_feat.get("name", ""),
                    "evidence_source": matched_feat.get("source", "") or "",
                    "evidence_desc": matched_feat.get("desc", "") or "",
                    "matched_alias": matched_alias,
                    "confidence": "medium" if is_fuzzy else "high",
                    "note": "关键词模糊匹配,建议人工核对" if is_fuzzy else "",
                }
            else:
                # 未命中 → 标记 ? 并给出「为何缺失」候选解释
                per_vendor[cname] = {
                    "status": "unknown",
                    "evidence_feature_name": "",
                    "evidence_source": "",
                    "evidence_desc": "",
                    "matched_alias": "",
                    "confidence": "low",
                    "note": "公开材料未提及",
                }
                confidence_counts[cname]["low"] += 1

        enriched_features.append(
            {
                "id": canon["id"],
                "name_cn": canon["name_cn"],
                "name_en": canon["name_en"],
                "category": canon["category"],
                "importance": canon.get("importance", ""),
                "desc": canon["desc"],
                "why": canon.get("why", ""),
                "support_count": support_count,
                "vendor_total": len(comp_names),
                "vendors": per_vendor,
            }
        )

    # 3) 按类别聚合
    cat_groups: dict = {}
    cat_coverage: dict = {}
    for f in enriched_features:
        cat = f["category"]
        cat_groups.setdefault(cat, []).append(f)
        for cname, v in f["vendors"].items():
            if v["status"] == "supports":
                cat_coverage.setdefault(cat, {}).setdefault(cname, 0)
                cat_coverage[cat][cname] += 1

    categories = []
    for cat_name in sorted(cat_groups.keys()):
        feats_sorted = sorted(
            cat_groups[cat_name],
            key=lambda x: (
                -x["support_count"],  # 支持者多的优先
                x["name_cn"],
            ),
        )
        categories.append(
            {
                "name": cat_name,
                "total_features": len(feats_sorted),
                "features": feats_sorted,
                "coverage": cat_coverage.get(cat_name, {}),
            }
        )

    totals_per_category = {c["name"]: c["total_features"] for c in categories}

    return {
        "categories": categories,
        "canonical_features": enriched_features,
        "competitor_names": comp_names,
        "totals_per_competitor": totals_per_competitor,
        "totals_per_category": totals_per_category,
        "confidence_counts": confidence_counts,
        "canonical_total": len(enriched_features),
    }


def _apply_evidence_notes_overrides(canonical_features, evidence_notes):
    """应用用户在 JSON 中手动覆盖的 evidence_notes(可选)。

    evidence_notes 格式:
        {
            "<canonical_id>": {
                "<vendor_name>": {
                    "status": "supports" | "unknown",
                    "evidence_feature_name": "...",  # 可选,该厂商原页面的功能叫法
                    "evidence_source": "https://...",  # 可选,新证据 URL
                    "note": "..."  # 可选,自定义缺失/支持原因(会覆盖自动判定)
                }
            }
        }

    设计目的: 当自动匹配算法「猜错」(如把同一能力的两种实现误判为同一项),
    用户可以在 JSON 里直接覆盖,无需修改爬虫。
    """
    if not evidence_notes:
        return
    for canon in canonical_features:
        fid = canon.get("id")
        if not fid or fid not in evidence_notes:
            continue
        per_vendor = evidence_notes[fid]
        if not isinstance(per_vendor, dict):
            continue
        vendors_field = canon.setdefault("vendors", {})
        for cn, override in per_vendor.items():
            if not isinstance(override, dict):
                continue
            old = vendors_field.get(cn, {})
            new_status = override.get("status", old.get("status", "unknown"))
            old["status"] = new_status
            if "evidence_feature_name" in override:
                old["evidence_feature_name"] = override["evidence_feature_name"]
            if "evidence_source" in override:
                old["evidence_source"] = override["evidence_source"]
            if "note" in override:
                old["note"] = override["note"]
                # 用高置信标记:有 note 的 = 用户手工确认过
                old["confidence"] = "high" if new_status == "supports" else "medium"
            vendors_field[cn] = old
        # 重新计算 support_count
        canon["support_count"] = sum(
            1
            for v in vendors_field.values()
            if v.get("status") == "supports"
        )


def _find_unique_features(competitors, feature_aliases=None):
    """每家独有的功能(其他家都没有)。

    通过 feature_aliases 把同义功能先合并到 canonical_name,再判断独家性,
    避免「团队收件箱」与「团队共享收件箱」被误判为两家都独家。

    Returns:
        dict: 形如 {vendor_name: [unique_feature, ...]}。
              每个 unique_feature 含 name / category / desc / _ref / _owner / _source。
    """
    # 与 _build_feature_comparison_matrix 一致:三层合并
    auto_detected = _auto_detect_aliases(competitors)
    effective = _merge_alias_layers(
        user_aliases=feature_aliases,
        auto_aliases=auto_detected,
        default_aliases=_DEFAULT_FEATURE_ALIASES,
    )
    feature_aliases = effective

    alias_index: dict = {}
    for canonical, info in (feature_aliases or {}).items():
        for alias in info.get("aliases", []) if isinstance(info, dict) else [canonical]:
            key = alias.strip().lower()
            if key:
                alias_index[key] = canonical

    def _canon(name: str) -> str:
        return alias_index.get(name.strip().lower(), name)

    # 反向索引:每个 canonical(feature, category) → 提供者列表
    feat_to_comps: dict = {}
    for c in competitors:
        comp = c["name"]
        for feat in c.get("feature_catalog", {}).get(comp, []):
            canon = _canon(feat.get("name", ""))
            key = (canon.lower(), feat.get("category", ""))
            if comp not in feat_to_comps.setdefault(key, []):
                feat_to_comps[key].append(comp)

    result = {}
    for c in competitors:
        comp = c["name"]
        unique = []
        for feat in c.get("feature_catalog", {}).get(comp, []):
            canon = _canon(feat.get("name", ""))
            key = (canon.lower(), feat.get("category", ""))
            if len(feat_to_comps.get(key, [])) == 1:
                unique.append(
                    {
                        "name": canon,
                        "category": feat.get("category", ""),
                        "desc": feat.get("desc", ""),
                        "_ref": feat.get("_ref", 0),
                        "_owner": comp,  # 这条独家功能属于这家
                        "_source": feat.get("source", ""),  # 原始来源 URL
                    }
                )
        result[comp] = unique
    return result


def _derive_data_growth(competitors):
    """从 competitors.scores.momentum 派生数据增长。"""
    if not competitors:
        return {"overall": "—", "summary": "—", "key_growth_points": []}
    avg_mom = sum(c["scores"].get("momentum", 0) for c in competitors) / len(
        competitors
    )
    top3 = sorted(
        competitors, key=lambda c: c["scores"].get("momentum", 0), reverse=True
    )[:3]
    points = [
        {
            "signal": f"{c['name']} 增长势头评分 {c['scores'].get('momentum', 0)}/10",
            "value": c["scores"].get("momentum", 0),
            "source": c.get("url", ""),
        }
        for c in top3
    ]
    summary = f"行业平均 momentum = {avg_mom:.1f}/10。前 3 名：" + "、".join(
        c["name"] for c in top3
    )
    return {
        "overall": f"行业整体处于扩张期，平均 momentum {avg_mom:.1f}/10。",
        "summary": summary,
        "key_growth_points": points,
    }


def _render_sources_html(sources_by_kind):
    """Python 端预渲染 sources 区块 HTML（避免模板引擎 quadratic 性能）。"""
    kind_icon = {
        "narrative": "📰",
        "competitor_meta": "🏢",
        "strength": "💪",
        "weakness": "⚠",
        "feature": "⚙",
        "market_segment": "🧭",
        "gap": "🕳",
        "opportunity": "💡",
        "opportunity_validation": "📊",
        "other_competitor": "📦",
    }
    kind_label = {
        "narrative": "背景叙事",
        "competitor_meta": "竞品基础信息",
        "strength": "竞品优势",
        "weakness": "竞品弱点",
        "feature": "产品功能",
        "market_segment": "市场细分",
        "gap": "市场空白",
        "opportunity": "颠覆机会",
        "opportunity_validation": "机会验证",
        "other_competitor": "其他竞品",
    }
    parts = []
    for group in sources_by_kind:
        kind = group["kind"]
        label = kind_label.get(kind) or kind
        parts.append(
            f'<h3 class="sub-head">{html.escape(kind_icon.get(kind, "📎"))} '
            f"{html.escape(label)} "
            f'<span style="font-size:0.7rem; color:var(--fg-mute); font-weight:400; margin-left:0.5rem;">'
            f"{group['count']} 条</span></h3>"
        )
        parts.append('<div class="sources-list">')
        for s in group["items"]:
            comp_part = (
                f"[{html.escape(s.get('competitor', ''))}] "
                if s.get("competitor")
                else ""
            )
            # 来源类型徽章:渲染时未做实际可达性检测,绝不标"可访问"冒充已验证
            kind_tag = (
                "👤 用户社区" if s.get("verified") == "user" else "🤖 官方页面"
            )
            parts.append(
                f'<div class="source-item" id="src-{s["idx"]}">'
                f'<span class="src-num">{s["idx"]}</span>'
                f'<span class="src-claim">{comp_part}{html.escape(s.get("claim", ""))}</span>'
                f'<div class="src-meta">'
                f'<a href="{html.escape(s["url"])}" target="_blank">{html.escape(s["url"])}</a>'
                f'<span style="background:var(--bg-soft); color:var(--fg-mute); padding:0.05rem 0.4rem; border-radius:3px; font-size:0.7rem; margin-left:0.5rem;">{kind_tag}</span>'
                f"</div></div>"
            )
        parts.append("</div>")
    return "\n".join(parts)


def _render_canonical_section_html(canonical_matrix, source_index=None):
    """预渲染 § 5.2.1/5.2.2 (基于 canonical 功能集) 的 HTML。

    关键设计:
      - **每行都有中文释义** + 行业意义说明 —— 用户一眼看懂这是什么
      - **每个单元格** 都是 ✓/✗/?,附 tooltip 解释「为什么是这个状态」
      - **? 单元格** 鼠标悬停展示「该厂商公开材料未发现此能力」+ 缺失原因
      - 支持级别(深浅色)按「N 家支持」自动映射
      - 行排序: P0 优先 → 支持数多 → 名称
    """
    cats = canonical_matrix["categories"]
    comp_names = canonical_matrix["competitor_names"]
    totals = canonical_matrix["totals_per_competitor"]
    canonical_total = canonical_matrix["canonical_total"]
    confidence_counts = canonical_matrix["confidence_counts"]
    n_vendors = len(comp_names)

    def _src_n(url):
        if not source_index or not url:
            return 0
        # source_index 可能是 {url: n} 或 {n: {url: ...}} 两种形式
        if url in source_index and isinstance(source_index[url], (int, str)):
            return int(source_index[url])
        for k, v in source_index.items():
            if isinstance(v, dict) and v.get("url") == url:
                return int(k) if isinstance(k, (int, str)) else 0
        return 0

    out = []

    # ───── 顶部统计条 + 完整性说明 ─────
    total_high = sum(v["high"] for v in confidence_counts.values())
    total_low = sum(v["low"] for v in confidence_counts.values())
    coverage_pct = (
        round(100 * total_high / (total_high + total_low), 1)
        if (total_high + total_low)
        else 0
    )
    out.append('<div class="feat-summary-bar">')
    out.append(
        f'<div class="feat-summary-stat"><div class="num">{canonical_total}</div><div class="lbl">行业标准功能</div></div>'
    )
    out.append(
        f'<div class="feat-summary-stat"><div class="num">{n_vendors}</div><div class="lbl">对比厂商</div></div>'
    )
    out.append(
        f'<div class="feat-summary-stat"><div class="num">{len(cats)}</div><div class="lbl">功能类别</div></div>'
    )
    out.append(
        f'<div class="feat-summary-stat"><div class="num">{coverage_pct}%</div><div class="lbl">证据完整度</div></div>'
    )
    out.append("</div>")

    # 方法学声明 — 这是「权威矩阵」与之前「厂商原功能矩阵」的核心区别
    out.append(
        '<div style="background:linear-gradient(135deg, var(--accent-soft) 0%, var(--bg-soft) 100%); '
        'border-left:3px solid var(--accent); padding:0.75rem 1rem; border-radius:6px; '
        'margin-bottom:1.25rem; font-size:0.82rem; line-height:1.7;">'
        '<strong style="color:var(--accent);">📐 方法学:</strong> '
        "本矩阵以<strong>行业标准功能集(canonical feature set)</strong>为行,"
        "而非各厂商原始功能列表 —— 避免「各家叫法不同」导致的虚假「独家」。"
        "<br>"
        "<strong>判定规则:</strong> 厂商原始 <code>feature_catalog</code> 命中 canonical 别名 → <span style='color:var(--good); font-weight:600;'>✓ 支持</span>(高置信);"
        "未命中 → <span style='color:var(--warn); font-weight:600;'>? 未找到公开材料</span>(低置信)。"
        "<br>"
        "<strong>完整性:</strong> 即使所有厂商都未公开宣传某能力,该行<strong>仍保留</strong> —— 这恰恰是市场空白信号。"
        "</div>"
    )

    # ───── § 5.2.1 厂商功能对比矩阵 ─────
    out.append(
        '<h4 style="font-family:var(--font-display); font-size:1.05rem; color:var(--accent); margin: 1.5rem 0 0.5rem;">📊 5.2.1 厂商功能对比矩阵 · 基于行业标准</h4>'
        '<p style="color: var(--fg-mute); font-size: 0.85rem; margin: 0 0 1rem; line-height: 1.7;">'
        f"<strong>行 = {canonical_total} 个行业标准功能</strong>({len(cats)} 个类别);"
        f"<strong>列 = {n_vendors} 家竞品</strong>。"
        "<strong>✓=支持(高置信,附证据) / ?=未找到公开材料(低置信)</strong>。"
        "<br>"
        '<strong style="color:var(--accent);">颜色含义:</strong> '
        '<span style="background:rgba(196,49,75,0.18); color:#c4314b; padding:0.05rem 0.45rem; border-radius:3px;">🔴 红=独家(1家)</span> '
        '<span style="background:rgba(184,142,47,0.18); color:#b88e2f; padding:0.05rem 0.45rem; border-radius:3px;">🟡 黄=少数(2家)</span> '
        '<span style="background:rgba(31,122,68,0.18); color:var(--good); padding:0.05rem 0.45rem; border-radius:3px;">🟢 绿=多家共有(3-4家)</span> '
        '<span style="background:rgba(107,70,160,0.20); color:#6b46a0; padding:0.05rem 0.45rem; border-radius:3px;">🟣 紫=行业标配(5家)</span>'
        " —— <strong>颜色饱和度反映「支持者数量」</strong>(饱和=独家,浅=共有)。"
        "</p>"
    )
    out.append(
        '<div class="feat-matrix-wrap"><table class="feat-matrix"><thead><tr>'
        '<th style="text-align:left;">功能 / Feature</th>'
    )
    for cn in comp_names:
        short_name = cn.replace(".io", "")
        out.append(
            f'<th title="{html.escape(cn)}">{html.escape(short_name)}<br>'
            f'<span style="font-size:0.65rem; color:var(--fg-mute); font-weight:400;">'
            f"{totals.get(cn, 0)}/{canonical_total}</span></th>"
        )
    out.append("</tr></thead><tbody>")
    # 图例:重要度分级说明(徽章颜色红=核心 橙=重要 灰=进阶)
    out.append(
        '<tr><td colspan="{n}" style="background:var(--bg-deeper); text-align:left;">'
        '<span style="font-size:0.72rem; color:var(--fg-mute);">'
        "徽章图例：<b style='color:var(--bad);'>核心能力</b> = 赛道立身之本,缺失难以存活 · "
        "<b style='color:var(--warn);'>重要能力</b> = 影响竞争力与转化 · "
        "<b>进阶能力</b> = 差异化加分项。徽章后半句为该能力对业务的实际价值。"
        "</span></td></tr>".format(n=n_vendors + 1)
    )

    for cat in cats:
        # 类别小标题行
        out.append(
            f'<tr class="cat-divider"><td colspan="{n_vendors + 1}">'
            f'<strong style="color:var(--accent);">📁 {html.escape(cat["name"])}</strong>'
            f'<span style="color:var(--fg-mute); font-size:0.7rem; margin-left:0.6rem;">'
            f"{cat['total_features']} 项功能</span></td></tr>"
        )
        for f in cat["features"]:
            vendors = f.get("vendors", {})
            support_n = f["support_count"]
            total_n = f["vendor_total"]
            importance = f.get("importance", "")
            out.append("<tr>")
            # ── 左列:中文名 + 释义 + 行业意义 ──
            importance_html = ""
            if importance:
                # P0/P1/P2 是内部优先级黑话,读者看不懂 —— 渲染时翻译成明文
                imp_label = (
                    importance.replace("P0", "核心能力", 1)
                    .replace("P1", "重要能力", 1)
                    .replace("P2", "进阶能力", 1)
                    .replace("P3", "加分项", 1)
                )
                importance_color = (
                    "var(--bad)" if importance.startswith("P0")
                    else "var(--warn)" if importance.startswith("P1")
                    else "var(--fg-mute)"
                )
                importance_html = (
                    f'<span style="display:inline-block; background:{importance_color}; '
                    f"color:white; padding:0.05rem 0.4rem; border-radius:3px; "
                    f'font-size:0.65rem; margin-left:0.4rem; font-weight:600;" '
                    f'title="该功能对此赛道产品的重要度分级(行业基准清单,按公开材料核对)">'
                    f"{html.escape(imp_label)}</span>"
                )
            desc_html = (
                f'<div class="feat-desc" style="margin-top:0.2rem;">'
                f'<strong style="color:var(--fg);">释义:</strong> {html.escape(f.get("desc", ""))}</div>'
                if f.get("desc")
                else ""
            )
            why_html = (
                f'<div class="feat-why" style="margin-top:0.15rem; color:var(--fg-mute); font-size:0.72rem;">'
                f'🎯 {html.escape(f.get("why", ""))}</div>'
                if f.get("why")
                else ""
            )
            eng_name_html = (
                f'<span style="color:var(--fg-mute); font-size:0.7rem; margin-left:0.3rem; font-weight:400;">'
                f"{html.escape(f.get('name_en', ''))}</span>"
            )
            out.append(
                f"<td>"
                f'<div class="feat-name-line">'
                f'<strong>{html.escape(f["name_cn"])}</strong>{eng_name_html}{importance_html}'
                f"</div>"
                f"{desc_html}{why_html}"
                f"</td>"
            )
            # ── 单元格 ──
            for cn in comp_names:
                v = vendors.get(cn, {})
                status = v.get("status", "unknown")
                if status == "supports":
                    support_ratio = support_n
                    cls = f"feature-cell share-{min(support_ratio, 6)}"
                    src = v.get("evidence_source", "")
                    ref_n = _src_n(src)
                    ref_html = (
                        f'<a href="#src-{ref_n}" class="cell-ref">{ref_n}</a>'
                        if ref_n
                        else ""
                    )
                    evidence_name = v.get("evidence_feature_name", "")
                    matched = v.get("matched_alias", "")
                    tooltip_parts = [
                        f"✓ {cn} 支持「{f['name_cn']}」",
                    ]
                    if evidence_name and evidence_name != f["name_cn"]:
                        tooltip_parts.append(f"原页面叫法: {evidence_name}")
                    if matched and matched != f["name_cn"].lower():
                        tooltip_parts.append(f"匹配别名: {matched}")
                    tooltip_parts.append(
                        f"{support_n}/{total_n} 家支持 · 证据 [{ref_n or '?'}]"
                    )
                    if src:
                        tooltip_parts.append(f"来源: {src}")
                    tooltip = " · ".join(tooltip_parts)
                    out.append(
                        f'<td class="{cls}" title="{html.escape(tooltip)}">'
                        f'<span class="cell-mark">✓</span>{ref_html}</td>'
                    )
                else:
                    # ? 单元格 — 明确说明「未找到公开材料」并给原因候选
                    note = v.get("note", "公开材料未提及")
                    tooltip = (
                        f"? {cn} 未公开宣传「{f['name_cn']}」\n"
                        f"判定: 该厂商 feature_catalog 中未找到 canonical 别名匹配\n"
                        f"缺失原因: {note}\n"
                        f"行业意义: {f.get('why', '—')}\n"
                        f"可信度: 低(未深入查 docs/blog/changelog)"
                    )
                    out.append(
                        f'<td class="feature-cell unknown-cell" '
                        f'title="{html.escape(tooltip)}" '
                        f'style="opacity:0.55; background:var(--warn-soft);">'
                        f'<span class="cell-mark">?</span></td>'
                    )
            out.append("</tr>")

    # 总数行
    out.append('<tr class="total-row"><td>📊 <strong>命中标准功能</strong></td>')
    for cn in comp_names:
        out.append(
            f'<td class="feature-cell"><strong>{totals.get(cn, 0)}</strong>/{canonical_total}</td>'
        )
    out.append("</tr>")
    out.append("</tbody></table></div>")

    # ───── § 5.2.2 按功能类别分组(紧凑 3 栏布局) ─────
    out.append(
        '<h4 style="font-family:var(--font-display); font-size:1.05rem; color:var(--accent); margin: 1.5rem 0 0.5rem;">📂 5.2.2 按功能类别分组(谁独家 / 谁缺位)</h4>'
        '<p style="color: var(--fg-mute); font-size: 0.85rem; margin: 0.3rem 0 1rem;">每个类别下列出所有 canonical 功能;'
        '<strong style="color:var(--good);">✓ 绿</strong> = 该厂商支持(高置信,附证据),'
        '<strong style="color:var(--warn);">? 黄</strong> = 未找到公开材料(低置信)。'
        "🏆 = 行业标配(全员支持);🕳 = 真空地带(全员缺失,市场空白)。</p>"
    )

    cats_sorted = sorted(cats, key=lambda c: -c["total_features"])
    cat_icon_map = {
        "消息渠道": "💬",
        "营销自动化": "📣",
        "AI 客服": "🤖",
        "收件箱协作": "🗂",
        "CRM / 客户数据": "👥",
        "电商 / Commerce": "🛒",
        "集成 / Integration": "🔌",
        "分析 / Analytics": "📊",
        "数据安全": "🔒",
        "开发者": "🛠",
    }

    for cat in cats_sorted:
        out.append('<div class="feat-category-card">')
        cat_icon = cat_icon_map.get(cat["name"], "📦")
        cat_coverage = cat.get("coverage", {})
        cov_text = " · ".join(
            f"<strong>{html.escape(cn)}</strong>:{cat_coverage.get(cn, 0)}/{cat['total_features']}"
            for cn in comp_names
        )
        out.append(
            f'<div class="cat-head">'
            f'<div class="cat-icon">{cat_icon}</div>'
            f'<div class="cat-info">'
            f'<div class="cat-name">{html.escape(cat["name"])} '
            f'<span class="cat-count-inline">{cat["total_features"]} 项</span></div>'
            f'<div class="cat-coverage">{cov_text}</div>'
            f"</div>"
            f"</div>"
        )
        out.append('<div class="feat-grid">')
        for f in cat["features"]:
            vendors = f.get("vendors", {})
            support_n = f["support_count"]
            total_n = f["vendor_total"]
            is_universal = support_n == total_n and total_n > 0
            is_none = support_n == 0
            importance = f.get("importance", "")
            importance_badge = ""
            if importance:
                # P0/P1/P2 → 明文(与 5.2.1 矩阵同一翻译规则)
                imp_label = (
                    importance.replace("P0", "核心能力", 1)
                    .replace("P1", "重要能力", 1)
                    .replace("P2", "进阶能力", 1)
                    .replace("P3", "加分项", 1)
                )
                importance_color = (
                    "#c4314b" if importance.startswith("P0")
                    else "#b88e2f" if importance.startswith("P1")
                    else "var(--fg-mute)"
                )
                importance_badge = (
                    f'<span class="imp-badge" style="background:{importance_color};" '
                    f'title="该功能对此赛道产品的重要度分级(行业基准清单,按公开材料核对)">'
                    f"{html.escape(imp_label)}</span>"
                )
            # 中文释义 + 行业意义 — 一行内紧凑显示
            desc_short = f.get("desc", "")
            why_short = f.get("why", "")
            star = ""
            if is_universal:
                star = '<span class="feat-flag flag-universal" title="行业标配:全员支持">🏆</span>'
            elif is_none:
                star = '<span class="feat-flag flag-void" title="真空地带:全员未公开此能力 = 市场空白信号">🕳</span>'

            # 厂商支持网格 — 每家一个小方块(✓/?)+ 简名
            vendor_cells = []
            for cn in comp_names:
                v = vendors.get(cn, {})
                if v.get("status") == "supports":
                    src = v.get("evidence_source", "")
                    evidence_name = v.get("evidence_feature_name", "")
                    title = (
                        f"✓ {cn} 支持 · 原页面叫法: {evidence_name}"
                        + (f" · 证据: {src}" if src else "")
                    )
                    vendor_cells.append(
                        f'<div class="vc-supports" title="{html.escape(title)}">'
                        f'<span class="vc-mark">✓</span>'
                        f'<span class="vc-name">{html.escape(cn.replace(".io", ""))}</span>'
                        f"</div>"
                    )
                else:
                    note = v.get("note", "公开材料未提及")
                    title = f"? {cn} 未公开宣传 · {note} · 行业意义: {why_short}"
                    vendor_cells.append(
                        f'<div class="vc-unknown" title="{html.escape(title)}">'
                        f'<span class="vc-mark">?</span>'
                        f'<span class="vc-name">{html.escape(cn.replace(".io", ""))}</span>'
                        f"</div>"
                    )

            vendor_cells_html = "".join(vendor_cells)
            # 行高亮:全员支持 → 浅绿边框;全员缺失 → 浅红边框;独家 → 浅金边框
            card_class = "feat-compact-card"
            if is_universal:
                card_class += " feat-card-universal"
            elif is_none:
                card_class += " feat-card-void"
            elif support_n == 1:
                card_class += " feat-card-unique"

            out.append(
                f'<div class="{card_class}">'
                f'<div class="feat-card-head">'
                f'<div class="feat-card-title">'
                f"{star}"
                f"<strong>{html.escape(f['name_cn'])}</strong>"
                f"{importance_badge}"
                f'<span class="feat-card-en">{html.escape(f.get("name_en", ""))}</span>'
                f"</div>"
                f'<div class="feat-card-score" title="支持数/总厂商数">'
                f"{support_n}/{total_n}"
                f"</div>"
                f"</div>"
                f'<div class="feat-card-desc">'
                f'<strong>释义:</strong>{html.escape(desc_short)}'
                f"</div>"
                f'<div class="feat-card-why">'
                f"🎯 <strong>行业意义:</strong>{html.escape(why_short)}"
                f"</div>"
                f'<div class="feat-card-vendors">{vendor_cells_html}</div>'
                f"</div>"
            )
        out.append("</div>")  # feat-grid
        out.append("</div>")  # feat-category-card

    return "\n".join(out)


def _render_section5_2_html(matrix, unique_features):
    """预渲染 § 5.2 厂商对比矩阵 + 类别分组 + 独家功能面板(避免模板引擎 quadratic)。"""
    cats = matrix["categories"]
    comp_names = matrix["competitor_names"]
    totals = matrix["totals_per_competitor"]
    n_vendors = len(comp_names)
    max_share = max(
        (len(f.get("_comps", [])) for c in cats for f in c["features"]),
        default=0,
    )

    out = []
    # 顶部统计条
    out.append('<div class="feat-summary-bar">')
    out.append(
        f'<div class="feat-summary-stat"><div class="num">{sum(totals.values())}</div><div class="lbl">功能总数</div></div>'
    )
    out.append(
        f'<div class="feat-summary-stat"><div class="num">{n_vendors}</div><div class="lbl">竞品数</div></div>'
    )
    out.append(
        f'<div class="feat-summary-stat"><div class="num">{len(cats)}</div><div class="lbl">功能类别</div></div>'
    )
    out.append(
        f'<div class="feat-summary-stat"><div class="num">{max_share}</div><div class="lbl">最多家共有</div></div>'
    )
    out.append("</div>")

    # 主对比矩阵 — 5.2.1 标题列窄(180px)、功能列详细、通俗解释
    out.append(
        '<h4 style="font-family:var(--font-display); font-size:1.05rem; color:var(--accent); margin: 1.5rem 0 0.5rem;">📊 5.2.1 厂商功能对比矩阵</h4>'
        '<p style="color: var(--fg-mute); font-size: 0.85rem; margin: 0 0 1rem; line-height: 1.7;">'
        "<strong>行 = 功能</strong>(共 "
        f"{sum(c['total_features'] for c in cats)}"
        " 个),<strong>列 = 竞品</strong>(6 家)。"
        "✓=支持 / —=不支持。<strong>颜色越深=支持者越多</strong>(独家功能高亮 ⭐)。"
        '<strong style="color:var(--accent);">每个 ✓ 旁边的 [N] 是该厂商自己的来源证据</strong>,可点击跳转到文末原始来源。'
        "</p>"
        '<div style="background:var(--bg-soft); border-left:3px solid var(--warn); padding:0.65rem 0.9rem; border-radius:6px; margin-bottom:1rem; font-size:0.78rem; line-height:1.6; color:var(--fg-soft);">'
        '<strong style="color:var(--warn);">📖 怎么读:</strong> 同义功能(如「团队收件箱」/「团队共享收件箱」)已自动合并为 canonical,但 <strong style="color:var(--bad);">不同能力的同名功能</strong>(如「意图识别」/「智能路由」/「升级转人工」)会保留为多行 —— 鼠标悬停查看各家原始描述,确认是否真同义。'
        "</div>"
    )
    out.append(
        '<div class="feat-matrix-wrap"><table class="feat-matrix"><thead><tr><th style="text-align:left;">功能 / Feature</th>'
    )
    for cn in comp_names:
        short_name = cn.replace(".io", "")
        out.append(f"<th>{html.escape(short_name)}</th>")
    out.append("</tr></thead><tbody>")

    for cat in cats:
        for f in cat["features"]:
            comps_list = f.get("_comps", [])
            n = len(comps_list)
            is_unique = n == 1
            tr_class = ' class="unique-feature"' if is_unique else ""
            out.append(f"<tr{tr_class}>")
            desc = f.get("desc", "") or ""
            display_names = f.get("_display_names") or {}
            vendor_descs = f.get("_vendor_descs") or {}
            rationale = f.get("_rationale", "")
            # ── 别名 chip 行(各家叫法 + 原始描述对比 — 防误判) ──
            aliases_html = ""
            if display_names or (
                len(vendor_descs) > 1 and len(set(vendor_descs.values())) > 1
            ):
                chips = "".join(
                    f'<span class="alias-chip"><strong>{html.escape(comp)}</strong> 「{html.escape(dn)}」</span>'
                    for comp, dn in display_names.items()
                )
                # 各家原始描述对比表(可展开)
                if len(vendor_descs) > 1 and len(set(vendor_descs.values())) > 1:
                    desc_compare = '<div class="vendor-desc-compare">'
                    for comp, d in sorted(vendor_descs.items()):
                        desc_compare += (
                            f'<div class="vdc-row"><span class="vdc-name">{html.escape(comp)}</span>'
                            f'<span class="vdc-desc">{html.escape(d)}</span></div>'
                        )
                    desc_compare += "</div>"
                    aliases_html = (
                        f'<div class="feat-aliases">{chips}{desc_compare}</div>'
                    )
                    tooltip_text = html.escape(
                        rationale or "对比各家原始描述确认是否真同义"
                    )
                else:
                    aliases_html = f'<div class="feat-aliases">{chips}</div>'
                    tooltip_text = html.escape(rationale or "各厂商叫法不同")
                # 把 tooltip 加到外层 td（render 后整体处理）
                if aliases_html and aliases_html.startswith("<div"):
                    # 替换 title 让 hover 显示各家描述
                    aliases_html = aliases_html.replace(
                        '<div class="feat-aliases">',
                        f'<div class="feat-aliases" title="{tooltip_text}">',
                        1,
                    )
            # ── 功能行 ──
            ref_html = (
                f'<a href="#src-{f.get("_ref", 0)}" class="ref">{f["_ref"]}</a>'
                if f.get("_ref")
                else ""
            )
            desc_html = (
                f'<div class="feat-desc">{html.escape(desc)}</div>' if desc else ""
            )
            out.append(
                f'<td title="{html.escape(desc)}">'
                f'<div class="feat-name-line"><strong>{html.escape(f["name"])}</strong>{ref_html}</div>'
                f"{desc_html}{aliases_html}"
                f"</td>"
            )
            for cn in comp_names:
                if cn in comps_list:
                    cls = f"feature-cell share-{min(n, 6)}"
                    # 该厂商自己的来源 [N]
                    vrefs = f.get("_vendor_refs") or {}
                    vref = vrefs.get(cn, 0)
                    ref_html = (
                        f'<a href="#src-{vref}" class="cell-ref">{vref}</a>'
                        if vref
                        else ""
                    )
                    out.append(
                        f'<td class="{cls}" title="{n}/{n_vendors} 家支持 · {cn} 来源 [{vref or "?"}]">'
                        f'<span class="cell-mark">✓</span>{ref_html}</td>'
                    )
                else:
                    out.append('<td class="feature-cell" style="opacity:0.18;">—</td>')
            out.append("</tr>")

    # 总数行
    out.append('<tr class="total-row"><td>📊 <strong>功能总数</strong></td>')
    for cn in comp_names:
        out.append(f'<td class="feature-cell">{totals.get(cn, 0)}</td>')
    out.append("</tr>")
    out.append("</tbody></table></div>")

    # 类别汇总
    out.append(
        '<h4 style="font-family:var(--font-display); font-size:1.05rem; color:var(--accent); margin: 1.5rem 0 0.5rem;">📂 5.2.2 按功能类别分组(谁有独家?)</h4>'
    )
    out.append(
        '<p style="color: var(--fg-mute); font-size: 0.85rem; margin: 0.3rem 0 1rem;">每个类别下列出所有功能,<span style="background:var(--accent-soft); padding:0.1rem 0.4rem; border-radius:3px; color:var(--accent); font-weight:600;">彩色</span> = 独家(只此一家),<span style="background:var(--bg-soft); padding:0.1rem 0.4rem; border-radius:3px;">灰色</span> = 多家共有。</p>'
    )

    cats_sorted = sorted(cats, key=lambda c: -c["total_features"])
    for cat in cats_sorted:
        out.append('<div class="feat-category-card">')
        out.append(
            f'<div class="cat-head"><div class="cat-icon">{html.escape(cat["name"][:1])}</div>'
        )
        out.append(f'<div class="cat-name">{html.escape(cat["name"])}</div>')
        out.append(f'<div class="cat-count">{cat["total_features"]} 项功能</div></div>')
        for f in cat["features"]:
            comps_list = f.get("_comps", [])
            is_unique = len(comps_list) == 1
            out.append('<div class="feat-row"><div>')
            star = "⭐ " if is_unique else ""
            ref_html = (
                f'<a href="#src-{f.get("_ref", 0)}" class="ref">{f["_ref"]}</a>'
                if f.get("_ref")
                else ""
            )
            desc_html = (
                f'<div class="feat-desc">{html.escape(f.get("desc", "") or "")}</div>'
                if f.get("desc")
                else ""
            )
            # 别名提示行 + 各家描述对比(防误判)
            display_names = f.get("_display_names") or {}
            vendor_descs = f.get("_vendor_descs") or {}
            rationale = f.get("_rationale", "")
            alias_html = ""
            if display_names or (
                len(vendor_descs) > 1 and len(set(vendor_descs.values())) > 1
            ):
                chips = " · ".join(
                    f'<span style="display:inline-block; background:var(--bg-soft); padding:0.05rem 0.45rem; '
                    f'border-radius:3px; font-size:0.72rem; margin:0.05rem 0.15rem 0.05rem 0;">'
                    f"<strong>{html.escape(comp)}</strong> 叫「{html.escape(dn)}」</span>"
                    for comp, dn in display_names.items()
                )
                # 各家原始描述对比
                desc_compare = ""
                if len(vendor_descs) > 1 and len(set(vendor_descs.values())) > 1:
                    desc_compare = (
                        '<div class="vendor-desc-compare" style="margin-top:0.4rem;">'
                    )
                    desc_compare += '<div style="font-size:0.72rem; color:var(--accent); font-weight:600; margin-bottom:0.2rem;">📋 各家原始描述（请确认是否真同义）：</div>'
                    for comp, d in sorted(vendor_descs.items()):
                        desc_compare += (
                            f'<div class="vdc-row"><span class="vdc-name">{html.escape(comp)}</span>'
                            f'<span class="vdc-desc">{html.escape(d)}</span></div>'
                        )
                    desc_compare += "</div>"
                tooltip = html.escape(rationale or "请对比各家原始描述确认")
                alias_html = (
                    f'<div class="feat-aliases" title="{tooltip}" '
                    f'style="margin-top:0.15rem; line-height:1.7;">{chips}{desc_compare}</div>'
                )
            out.append(
                f'<div class="feat-name">{star}{html.escape(f["name"])}{ref_html}</div>{desc_html}{alias_html}'
            )
            out.append("</div><div class='feat-comps'>")
            for cp in comps_list:
                pill_cls = "unique" if is_unique else "shared"
                star2 = " ⭐" if is_unique else ""
                out.append(
                    f'<span class="comp-pill {pill_cls}" title="{html.escape(cp)}{" · 独家" if is_unique else ""}">{html.escape(cp)}{star2}</span>'
                )
            out.append("</div></div>")
        out.append("</div>")

    # 独家功能面板 — 抽到独立函数,canonical 模式也能复用
    out.append(_render_unique_features_panel(unique_features))

    return "\n".join(out)


def _render_unique_features_panel(unique_features):
    """§ 5.2.3 各家独家功能面板 —— canonical 模式与 fallback 模式共用。

    Args:
        unique_features: dict, {vendor_name: [unique_feature, ...]}

    Returns:
        HTML 字符串
    """
    out = []
    out.append(
        '<h4 style="font-family:var(--font-display); font-size:1.05rem; color:var(--accent); margin: 1.5rem 0 0.5rem;">⭐ 5.2.3 各家独家功能 <span style="font-size:0.7rem; color:var(--fg-mute); font-weight:400; margin-left:0.5rem;">其他家都没有的独家卖点(每条带 owner + [N] 内部证据 + ↗ 原文 + 通俗讲解)</span></h4>'
        '<p style="font-size:0.78rem; color:var(--fg-mute); margin-bottom:1rem; line-height:1.6;">'
        '每条独家功能都标记 <code>owner</code> 厂商 + <span class="ref">N</span> 内部证据编号 + ↗ 原文 URL。</p>'
    )
    out.append('<div class="cluster-comp-grid">')
    for idx, (c_name, uniques) in enumerate(unique_features.items()):
        border_color = f"var(--data-{idx % 6 + 1})"
        out.append(
            f'<div class="cluster-comp-card" style="border-top-color: {border_color};">'
        )
        out.append(
            f'<div class="cc-name">{html.escape(c_name[:2])} · {html.escape(c_name)}</div>'
        )
        out.append(
            f'<div style="font-size:0.78rem; color:var(--accent); margin-bottom:0.5rem; font-weight:600;">⭐ {len(uniques)} 个独家功能(其他家都没有)</div>'
        )
        for u in uniques:
            owner = u.get("_owner", c_name)
            ref_n = u.get("_ref", 0)
            source_url = u.get("_source", "")
            desc = u.get("desc", "") or ""
            cat = u.get("category", "") or ""
            plain = {
                "消息 API": "用于程序化发送/接收 WhatsApp 消息(开发者集成场景)",
                "收件箱": "客服团队共用一个对话视图,提升响应效率",
                "AI": "用 AI 自动化处理客户对话,降低人工成本",
                "AI 客服": "用 AI 自动回复客户,降本增效",
                "工作流": "拖拉拽配置业务流程,无需代码",
                "营销": "营销自动化(广播 / 模板群发)",
                "营销自动化": "营销自动化(广播 / 模板群发)",
                "分析": "数据洞察(响应时间 / 转化率 / KPI 仪表盘)",
                "电商集成": "对接 Shopify 等电商平台,同步订单/库存",
                "合规与安全": "SOC2 / HIPAA / GDPR 等合规认证",
                "客户数据": "客户画像 + 标签 + 行为追踪",
                "广告集成": "对接 Meta/Google 广告平台,做归因",
                "集成": "与第三方 SaaS 工具打通(CRM / Helpdesk 等)",
                "联络中心": "全渠道联络中心(语音 + 消息 + 邮件)",
                "开发者": "面向开发者的 SDK / API / Webhook",
                "基础设施": "底层架构(消息路由 / 数据中台)",
                "多渠道": "统一管理多个渠道(WhatsApp/IG/Messenger/邮件)",
                "多语言": "支持多语言客服",
                "Help Desk": "工单系统 + 帮助中心",
                "增长工具": "增长黑客工具(裂变 / 邀请)",
            }.get(cat, "面向企业用户的差异化能力")
            cat_html = (
                f'<span style="background:var(--accent-soft); color:var(--accent); padding:0.05rem 0.45rem; border-radius:3px; font-size:0.7rem; font-weight:600; margin-right:0.4rem;">{html.escape(cat)}</span>'
                if cat
                else ""
            )
            ref_html = (
                f'<a href="#src-{ref_n}" class="ref" title="文末对应证据">证据 {ref_n}</a>'
                if ref_n
                else '<span style="color:var(--bad); font-size:0.72rem;">⚠ 无来源</span>'
            )
            verify_html = (
                f'<a href="{html.escape(source_url)}" target="_blank" rel="noopener" class="tsr-verify" title="点击访问 {html.escape(owner)} 官方页面验证">↗ 原文</a>'
                if source_url
                else ""
            )
            plain_html = (
                f'<div style="font-size:0.75rem; color:var(--info); margin-top:0.25rem; line-height:1.5;">📖 <strong>怎么用:</strong>{html.escape(plain)}</div>'
                if plain
                else ""
            )
            desc_html = (
                f'<div style="font-size:0.75rem; color:var(--fg-mute); margin-top:0.15rem; line-height:1.5;"><strong>官方描述:</strong>{html.escape(desc)}</div>'
                if desc
                else ""
            )
            out.append(
                f'<div class="unique-feature-row">'
                f'<div class="ufr-head">'
                f'<span class="ufr-name">⭐ {html.escape(u["name"])}</span>'
                f'<span class="ufr-meta">{cat_html}<span class="ufr-owner">📌 {html.escape(owner)}</span></span>'
                f"</div>"
                f'<div class="ufr-evidence">{ref_html}{verify_html}</div>'
                f"{plain_html}{desc_html}"
                f"</div>"
            )
        out.append("</div>")
    out.append("</div>")
    return "\n".join(out)


# ─────────── 数据修复:脏 feature 清洗 / 空 SWOT 推断 / 默认分校准 ───────────
# 爬虫输出的 JSON 常见三类问题,统一在渲染入口修复(老 JSON 也能渲染干净):
#   1. features 混入营销标题("Respond.io vs Manychat"/人名证言/**加粗**残留)
#   2. strengths/weaknesses 留空(爬虫约定由渲染端补全)
#   3. scores 全部相等(爬虫没算分,写入默认 5)

_JUNK_MARKETING_RX = re.compile(
    r"\b(vs\.?|versus|explore|discover|power of|trusted by|proven results"
    r"|here's how|learn how|see how|introducing|get to know|meet the|why choose)\b",
    re.I,
)
_JUNK_FEAT_KEYWORDS = (
    "api", "ai", "sdk", "crm", "chat", "inbox", "message", "call", "email",
    "sms", "bot", "workflow", "automation", "analytics", "integration",
    "whatsapp", "campaign", "集成", "自动化", "收件箱", "渠道", "客服", "营销",
    "分析", "机器人", "推送", "同步", "管理", "消息", "工单", "坐席",
)


def _clean_feature_text(t: str) -> str:
    s = (t or "").strip()
    s = re.sub(r"\[([^\]]*)\]\(https?://[^)]*\)", r"\1", s)  # 剥 markdown 链接语法
    return re.sub(r"\s+", " ", re.sub(r"[*_`]+", "", s)).strip()


# 渲染层第二道防线:cookie 横幅 / JS 模板变量 / 多语言碎片 / 证言引语
# (爬取层 _is_real_feature 已过滤,这里兜住旧 JSON / 手工数据 / 截断残留)
_JUNK_FEATURE_RX2 = re.compile(
    r"^manage\s+(options|services|vendors|preferences|consent)"
    r"|\{[^}]*\\[a-z][^}]*\}"
    r"|accept\s+(all|cookies)|cookie\s+preferences|do\s+not\s+sell|opt-out"
    r"|[\u0600-\u06FF]"
    r'|^["\u201c\u2018]'
    r"|\[",  # 残留方括号(截断的 markdown 链接)—— 功能名不含 [
    re.I,
)
# 常见多语言站导航动词(纯 ASCII 的西/德/语碎片,重音检测抓不到:"Explorar" 事故)
_JUNK_NAV_WORDS = (
    "explorar", "descubrir", "descubre", "conocer más", "entdecken",
    "mehr erfahren", "découvrir", "en savoir plus", "scopri di più",
    "saiba mais", "了解更多",
)


def _is_junk_feature(t: str) -> bool:
    """营销标题/证言人名/句子/横幅菜单/多语言碎片 → 不是产品功能。"""
    s = _clean_feature_text(t)
    if not s or len(s) < 2:
        return True
    if _JUNK_MARKETING_RX.search(s):
        return True
    if _JUNK_FEATURE_RX2.search(s):
        return True
    s_low = s.lower().strip()
    if any(s_low == w or s_low.startswith(w + " ") for w in _JUNK_NAV_WORDS):
        return True
    # 重音字母密集 → 西/法/葡语翻译碎片
    if len(re.findall(r"[àâäéèêëïîôöùûüçñáíóú]", s, re.I)) > len(s) * 0.22:
        return True
    if s.endswith((".", "!", "?")) or ". " in s:  # 句子不是功能名
        return True
    return False


def _fix_feature_lists(c: dict) -> None:
    """清洗单个竞品的 feature_catalog + core_features(去营销噪音 + 大小写不敏感去重)。"""
    name = c.get("name", "")
    catalog = c.get("feature_catalog", {})
    if isinstance(catalog, dict) and isinstance(catalog.get(name), list):
        seen, cleaned = {}, []
        for f in catalog[name]:
            if not isinstance(f, dict):
                continue
            f["name"] = _clean_feature_text(f.get("name", ""))
            key = f["name"].lower()
            if not f["name"] or _is_junk_feature(f["name"]):
                continue
            if key in seen:  # 大小写变体:保留大写字母更多的(品牌原文)
                kept = seen[key]
                if sum(ch.isupper() for ch in f["name"]) > sum(
                    ch.isupper() for ch in kept["name"]
                ):
                    kept["name"] = f["name"]
                continue
            seen[key] = f
            cleaned.append(f)
        catalog[name] = cleaned
    if isinstance(c.get("core_features"), list):
        seen, cleaned = {}, []
        for f in c["core_features"]:
            f = _clean_feature_text(f if isinstance(f, str) else str(f))
            key = f.lower()
            if not f or _is_junk_feature(f):
                continue
            if key in seen:
                kept = seen[key]
                if sum(ch.isupper() for ch in f) > sum(ch.isupper() for ch in kept):
                    seen[key] = f
                continue
            seen[key] = f
            cleaned.append(seen[key])
        c["core_features"] = cleaned


def _calibrate_scores(c: dict, feature_counts: list) -> None:
    """scores 全相等(爬虫默认值)时标记低置信,不再按关键词捏造分数。

    历史缺陷:这里曾用关键词启发式"发明"6 维分数(免费→8分/阶段查表→momentum),
    把默认数据伪装成真实评分。现在:全相等 = Step 3 没给真实分 → 标记
    scores_confidence=low,模板显示"评分未基于证据"警示,排名不具参考性。
    """
    scores = c.get("scores", {})
    vals = [scores.get(k) for k, _ in SCORE_DIMS]
    if vals and len(set(vals)) == 1:  # 全相等 = 默认值
        c["scores_confidence"] = "low"
    else:
        c["scores_confidence"] = "normal"


def _repair_competitors(competitors: list) -> None:
    """渲染前统一修复竞品数据(必须在 source/_ref 收集之前跑)。"""
    for c in competitors:
        _fix_feature_lists(c)
        # 爬虫约定:strengths/weaknesses 由渲染端推断补全
        if not c.get("strengths") or not c.get("weaknesses"):
            pos, neg = _infer_strengths_weaknesses(c)
            if not c.get("strengths"):
                c["strengths"] = pos
            if not c.get("weaknesses"):
                c["weaknesses"] = neg
    feature_counts = [
        len(c.get("feature_catalog", {}).get(c.get("name", ""), []))
        for c in competitors
    ]
    for c in competitors:
        _calibrate_scores(c, feature_counts)


def normalize(data: dict) -> dict:
    """标准化 + 派生 飞书模板所需的全部字段。

    输入 schema (SKILL.md 描述的 13 字段):
        topic, subtitle, date, generated_at, executive_summary,
        market_segments, competitors[*], feature_overlap,
        gaps, opportunities

    派生字段 (templates/report.html 实际渲染需要):
        background, goals, inspiration_points, opportunity_points,
        product_slogans, user_positioning, commercial_strategies,
        product_overview, visual_signals, user_feedback, data_growth,
        avg_maturity, top_competitor, bottom_competitor,
        top_gap, top_opportunity, toc_items
    """
    data = dict(data)
    data.setdefault("topic", "未命名主题")
    data.setdefault("date", datetime.now().strftime("%Y-%m-%d"))
    data.setdefault("subtitle", "深度竞品分析 + 颠覆性机会挖掘")
    data.setdefault(
        "generated_at", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    )
    data.setdefault("executive_summary", "（无摘要）")
    data.setdefault("market_segments", [])
    data.setdefault("competitors", [])
    data.setdefault("gaps", [])
    data.setdefault("opportunities", [])
    data.setdefault("feature_overlap", {})
    data.setdefault("recommendations", [])
    data.setdefault("background", "")
    data.setdefault("goals", [])

    # ── 先修复数据(脏 feature / 空 SWOT / 默认分),再做 source 收集 ──
    _repair_competitors(data["competitors"])

    # ── 提前定义 _add_source + 收集 sources ──
    # 这样下面 inspiration_points / swot / 等派生都能拿到 _ref
    sources: list = []
    source_idx: dict = {}

    def _add_source(url: str, kind: str, claim: str, competitor: str = "") -> int:
        """注册来源,返回角标号。

        去重键 = (url, claim, competitor) —— 而非裸 URL。
        历史缺陷:同一 URL(官网首页)被 tagline/founded/GTM/护城河 等
        多个不同论断复用同一个角标,来源区却只显示第一次注册的 claim
        ("X · tagline")。读者点「Meta 官方伙伴」旁的 [1] 落地却是
        "tagline" 条目 = 角标定位全部错位的观感。
        现在每个论断一个条目,点击落地即所见。
        """
        if not url:
            return 0
        key = (url, (claim or "")[:80], competitor)
        if key in source_idx:
            return source_idx[key]
        source_idx[key] = len(sources) + 1
        sources.append(
            {
                "idx": len(sources) + 1,
                "url": url,
                "kind": kind,
                "claim": claim[:80] if claim else "",
                "competitor": competitor,
                "verified": (
                    "user"
                    if any(d in url for d in ("g2.com", "reddit.com", "crunchbase.com"))
                    else "bot"
                ),
            }
        )
        return source_idx[key]

    # 1. background / executive_summary 等顶层 source 列表
    for src_list_key in ["background_sources", "executive_summary_sources"]:
        for src in data.get(src_list_key, []):
            _add_source(src.get("source", ""), "narrative", src.get("claim", ""), "")

    # 2. 每个 competitor 的字段 (tagline/founded/pricing etc.)
    for c in data["competitors"]:
        comp = c.get("name", "")
        for field in [
            "tagline",
            "founded",
            "stage",
            "headquarters",
            "funding",
            "team_size",
            "pricing",
            "target_users",
            "core_features",
            "differentiators",
            "tech_signals",
            "scores",
        ]:
            url = c.get(field + "_source", "")
            if url:
                idx = _add_source(url, "competitor_meta", f"{comp} · {field}", comp)
                c.setdefault("_refs", {})[field] = idx
        # strengths / weaknesses: 给每条加 _ref(包含 URL 提取)
        for st in c.get("strengths", []):
            url = st.get("source", "")
            ev = st.get("evidence", "")
            if not url and ev:
                m = re.search(r"https?://[^\s\)]+", ev)
                url = m.group(0).rstrip(".,;:") if m else ""
            if url:
                idx = _add_source(url, "strength", st.get("point", ""), comp)
                st["_ref"] = idx
        for w in c.get("weaknesses", []):
            url = w.get("source", "")
            ev = w.get("evidence", "")
            if not url and ev:
                m = re.search(r"https?://[^\s\)]+", ev)
                url = m.group(0).rstrip(".,;:") if m else ""
            if url:
                idx = _add_source(url, "weakness", w.get("point", ""), comp)
                w["_ref"] = idx
        # GTM/护城河证据条目注册来源(§4 商业策略可追溯的关键)
        for ev_key, ev_kind in (("gtm_evidence", "competitor_meta"),
                                ("moat_evidence", "competitor_meta")):
            for ev_item in c.get(ev_key, []) or []:
                u = ev_item.get("source", "")
                if u:
                    ev_item["_ref"] = _add_source(
                        u, ev_kind, f"{comp} · {'GTM' if ev_key=='gtm_evidence' else '护城河'}: {ev_item.get('name','')[:50]}", comp
                    )

    # 3. feature_catalog 来源:按厂商注册(非每功能一条 —— 5 家 × 50 功能
    # = 250 条来源曾把来源区撑爆;单元格的 tooltip 已含功能名证据,
    # 来源条目只需精确到"该厂商功能清单出自哪页")
    for c in data["competitors"]:
        comp = c.get("name", "")
        _feat_ref_cache = {}
        for feat in c.get("feature_catalog", {}).get(comp, []):
            url = feat.get("source", "")
            if url:
                if url not in _feat_ref_cache:
                    _feat_ref_cache[url] = _add_source(
                        url, "feature", f"{comp} · 功能清单", comp
                    )
                feat["_ref"] = _feat_ref_cache[url]

    # 4. market_segments
    for seg in data.get("market_segments", []):
        url = seg.get("source", "")
        if url:
            idx = _add_source(url, "market_segment", seg.get("label", ""), "")
            seg["_ref"] = idx

    # 5. gaps
    for g in data.get("gaps", []):
        url = g.get("source", "")
        if url:
            idx = _add_source(url, "gap", g.get("gap", "")[:60], "")
            g["_ref"] = idx

    # 6. opportunities + validation_sources
    for o in data.get("opportunities", []):
        url = o.get("source", "")
        if url:
            idx = _add_source(url, "opportunity", o.get("title", ""), "")
            o["_ref"] = idx
        for vs in o.get("validation_sources", []):
            _add_source(vs, "opportunity_validation", o.get("title", ""), "")

    # 7. other_competitors
    for oc in data.get("other_competitors", []):
        url = oc.get("source", "") or oc.get("url", "")
        if url:
            idx = _add_source(
                url, "other_competitor", oc.get("name", ""), oc.get("name", "")
            )
            oc["_ref"] = idx

    # 8. tech_signals / differentiators / user_feedback enrichment
    # 历史缺陷:没来源的条目拿官网首页冒充证据源("source laundering")——读者点进去
    # 根本找不到该论断。现在:没来源就留空,模板按"来源未记录"如实显示。
    for c in data["competitors"]:
        for fld in ("tech_signals", "differentiators"):
            items = c.get(fld, [])
            enriched = []
            for it in items:
                if isinstance(it, str):
                    enriched.append(
                        {
                            "name": it,
                            "source": c.get(fld + "_source", ""),
                        }
                    )
                elif isinstance(it, dict):
                    src = it.get("source") or c.get(fld + "_source", "")
                    enriched.append({"name": it.get("name", ""), "source": src})
                else:
                    enriched.append({"name": str(it), "source": ""})
            for e in enriched:
                if e["source"]:
                    e["_ref"] = _add_source(
                        e["source"],
                        "tech_signal" if fld == "tech_signals" else "differentiator",
                        e["name"][:50],
                        c.get("name", ""),
                    )
            c[fld] = enriched

        # user_feedback: 给 positive/negative 项加 _ref(没有真实来源就不渲染链接)
        fb = c.get("_user_feedback_meta") or c.get("user_feedback")
        if not isinstance(fb, dict):
            fb = _derive_user_feedback(c)
        for polarity in ("positive", "negative"):
            for item in fb.get(polarity, []):
                src = item.get("source_url") or item.get("source") or ""
                if src in ("G2/Reddit/官网评测", "G2/Reddit/社区抱怨", "—"):
                    src = ""  # 占位字符串不是来源
                if src:
                    item["_ref"] = _add_source(
                        src,
                        "user_feedback",
                        item.get("text", "")[:60],
                        c.get("name", ""),
                    )
        c["_user_feedback_enriched"] = fb

    # ===== 竞品：基础字段补全 + scores 校准 =====
    for c in data["competitors"]:
        c.setdefault("founded", "—")
        c.setdefault("stage", "未知")
        c.setdefault("target_users", [])
        c.setdefault("core_features", [])
        c.setdefault("strengths", [])
        c.setdefault("weaknesses", [])
        c.setdefault("differentiators", [])
        c.setdefault("tech_signals", [])
        c.setdefault("tagline", "")
        c.setdefault("pricing", "—")
        c.setdefault("url", "#")
        # 证据铁律:pricing_verified 缺失 = 未验证(历史缺陷:缺失时模板徽章逻辑
        # 整体跳过,LLM 忘写字段就能让所有价格裸奔不带 ⚠ —— 现在缺省即 False)
        if "pricing_verified" not in c:
            c["pricing_verified"] = False
        # 定价币种:优先用爬取层从实际价格符号检测的值(₹→INR 等)。
        # 历史缺陷:这里曾按总部所在地猜币种(总部未知→一律 USD),
        # 把 WATI 页面上的 ₹999 印度价标成 USD —— 币种错 = 价格错。
        # 只在爬取层没给值时才按总部兜底。
        if not c.get("pricing_currency"):
            currency = "USD"
            hq = (c.get("headquarters") or "").lower()
            if any(k in hq for k in ["中国", "china", "大陆", "beijing", "shanghai", "shenzhen"]):
                currency = "CNY"
            elif any(k in hq for k in ["hong kong", "hk"]):
                currency = "HKD"
            c["pricing_currency"] = currency

        # 注意:不再向 c['pricing'] 字符串附加"(✓N引擎一致)"——这是 debug 元信息,不应暴露给最终用户
        # 可信度标记走独立的 _pricing_consensus 字段,模板按需显示
        c.setdefault("slug", slugify(c.get("name", "")))
        c.setdefault("icon", smart_icon(c.get("name", "")))
        scores = c.get("scores", {})
        for k, _ in SCORE_DIMS:
            scores.setdefault(k, 0)
        c["scores"] = scores
        for s in c["strengths"]:
            s.setdefault("score", 5)
            s.setdefault("point", s.get("point", ""))
            s.setdefault("evidence", s.get("evidence", ""))
        for w in c["weaknesses"]:
            w.setdefault("score", 5)
            w.setdefault("point", w.get("point", ""))
            w.setdefault("evidence", w.get("evidence", ""))

    # ===== opportunities：基础字段补全 =====
    for o in data["opportunities"]:
        o.setdefault("target_users", [])
        o.setdefault("differentiators", [])
        o.setdefault("validation", [])
        o.setdefault("moat", "")
        o.setdefault("inspiration", "")
        o.setdefault("title", o.get("title", "（未命名机会）"))
        if not isinstance(o.get("disrupt_score"), (int, float)):
            o["disrupt_score"] = 7

    # ===== market_segments：基础字段补全 =====
    for seg in data["market_segments"]:
        seg.setdefault("players_count", len(seg.get("players", [])))
        seg.setdefault("icon", smart_icon(seg.get("label", "")))
        seg.setdefault("desc", seg.get("desc", ""))

    # ===== 派生：飞书模板所需字段 =====
    data["inspiration_points"] = _derive_inspiration_points(data["competitors"])
    data["opportunity_points"] = _derive_opportunity_points(data["competitors"])
    # 同时按竞品分组,供 §2.4 / §2.5 按厂商查阅
    data["inspiration_by_competitor"] = _group_inspiration_by_competitor(
        data["inspiration_points"], data["competitors"]
    )
    data["opportunity_by_competitor"] = _group_opportunity_by_competitor(
        data["opportunity_points"], data["competitors"]
    )

    # ─────────── 同类厂商聚类(用于 § 2 结论与建议) ───────────
    # 按 "主要功能类别" 聚类,让同类厂商放一起
    data["competitors_by_segment"] = _group_competitors_by_segment(
        data["competitors"], data.get("market_segments", [])
    )
    data["competitors_by_stage"] = _group_competitors_by_stage(data["competitors"])

    data["product_slogans"] = {
        c["name"]: c.get("tagline", "") or "（暂无口号）" for c in data["competitors"]
    }
    data["user_positioning"] = {
        c["name"]: _derive_user_positioning(c) for c in data["competitors"]
    }
    # 任一竞品有真实 region 数据才显示该列(全"未采集"时隐藏整列,
    # 比一排灰色的"未采集"干净)
    data["has_region_data"] = any(
        (c.get("region") or "").strip() not in ("", "未采集")
        for c in data["competitors"]
    )
    data["commercial_strategies"] = {
        c["name"]: _derive_commercial_strategies(c) for c in data["competitors"]
    }
    data["product_overview"] = {
        c["name"]: _derive_product_overview(c) for c in data["competitors"]
    }
    data["visual_signals"] = {
        c["name"]: _derive_visual_signals(c) for c in data["competitors"]
    }
    data["user_feedback"] = {
        c["name"]: _derive_user_feedback(c) for c in data["competitors"]
    }
    data["data_growth"] = _derive_data_growth(data["competitors"])

    # ─────────── § 5.2 功能全集 · 厂商对比矩阵 ───────────
    # 把所有竞品的功能汇集成一个大矩阵:
    #   横轴:每个 category,纵轴:每个 feature,格子:各竞品是否支持
    # feature_aliases: 可选,把同义功能合并到 canonical_name(团队收件箱 vs 团队共享收件箱)
    aliases = data.get("feature_aliases") or {}

    # ─── 决定是否启用 canonical(行业标准) 模式 ───
    # 默认开启:任何报告都可受益(行业标准 = 权威);用户可通过 feature_canonical.enabled=false 关闭
    canonical_features = None
    fc_config = data.get("feature_canonical") or {}
    if fc_config.get("enabled", True):
        # 优先用用户配置的 features,否则按 topic 自动选
        custom = fc_config.get("features")
        topic_lower = (data.get("topic") or "").lower()
        if custom:
            canonical_features = custom
        elif "whatsapp" in topic_lower or "whatsapp" in (data.get("topic") or "").lower():
            canonical_features = list(_CANONICAL_FEATURES_WHATSAPP)

    if canonical_features:
        # ── canonical 模式:行 = 行业标准能力,列 = 厂商 ──
        # 1) 应用用户覆盖 evidence_notes(若有): 形如
        #      {"whatsapp_business_api": {"Sleekflow": {"status": "supports", "evidence_feature_name": "WABA", "evidence_source": "...", "note": "..."}}}
        #    用户可以「手动确认/否定」自动匹配结果,避免误判;也支持添加原文 URL/补充说明
        evidence_notes = fc_config.get("evidence_notes") or {}
        for f_canon in canonical_features:
            fid = f_canon.get("id")
            if fid and fid in evidence_notes:
                notes_per_vendor = evidence_notes[fid]
                if isinstance(notes_per_vendor, dict):
                    f_canon.setdefault("_user_overrides", {})
                    for cn, override in notes_per_vendor.items():
                        if isinstance(override, dict):
                            f_canon["_user_overrides"][cn] = override

        data["canonical_matrix"] = _build_canonical_matrix(
            data["competitors"], canonical_features
        )
        # 应用用户覆盖到判定结果
        _apply_evidence_notes_overrides(
            data["canonical_matrix"]["canonical_features"],
            evidence_notes,
        )
        # 暴露给模板的统计
        data["feature_canonical_total"] = data["canonical_matrix"]["canonical_total"]
        data["feature_canonical_companies"] = len(
            data["canonical_matrix"]["competitor_names"]
        )
        # 把每家厂商命中 canonical 时引用的 source URL 注册为内部证据 [N]
        source_index_for_render: dict = {}  # {url: n} — _render_canonical 用 url 查
        _canon_seen_pairs = set()  # (url, vendor) 只注册一次
        for f in data["canonical_matrix"]["canonical_features"]:
            for cn, v in f.get("vendors", {}).items():
                src = v.get("evidence_source", "")
                if src and (src, cn) not in _canon_seen_pairs:
                    _canon_seen_pairs.add((src, cn))
                    if src not in source_index_for_render:
                        source_index_for_render[src] = _add_source(
                            src, "feature", f"{cn} · 矩阵判定依据", cn
                        )
        # 仍跑一遍旧逻辑以保留 5.2.3 独家功能面板的兼容性
        data["feature_comparison_matrix"] = _build_feature_comparison_matrix(
            data["competitors"], aliases
        )
        _vsrc_cache = {}
        for cat in data["feature_comparison_matrix"]["categories"]:
            for f in cat["features"]:
                vrefs = {}
                for comp, url in (f.get("_vendor_sources") or {}).items():
                    if url:
                        ck = (url, comp)
                        if ck not in _vsrc_cache:
                            _vsrc_cache[ck] = _add_source(
                                url, "feature", f"{comp} · 功能清单", comp
                            )
                        vrefs[comp] = _vsrc_cache[ck]
                if vrefs:
                    f["_vendor_refs"] = vrefs
        data["unique_features_by_competitor"] = _find_unique_features(
            data["competitors"], aliases
        )
        # 预渲染 canonical 矩阵 HTML（权威版本 — 取代旧 section5_2_html 的 5.2.1/5.2.2 部分）
        data["feature_matrix_mode"] = "canonical"
        data["section5_2_html"] = _render_canonical_section_html(
            data["canonical_matrix"],
            source_index_for_render,
        ) + _render_unique_features_panel(data["unique_features_by_competitor"])
    else:
        # ── fallback:旧版按厂商原始功能 ──
        data["feature_comparison_matrix"] = _build_feature_comparison_matrix(
            data["competitors"], aliases
        )
        # 把每家厂商自己的来源 URL 转为 [N] 编号,用于 §5.2.1 单元格内显示
        _vsrc_cache = {}
        for cat in data["feature_comparison_matrix"]["categories"]:
            for f in cat["features"]:
                vrefs = {}
                for comp, url in (f.get("_vendor_sources") or {}).items():
                    if url:
                        ck = (url, comp)
                        if ck not in _vsrc_cache:
                            _vsrc_cache[ck] = _add_source(
                                url, "feature", f"{comp} · 功能清单", comp
                            )
                        vrefs[comp] = _vsrc_cache[ck]
                if vrefs:
                    f["_vendor_refs"] = vrefs
        # 每家独有功能清单(其他家都没有的功能)
        data["unique_features_by_competitor"] = _find_unique_features(
            data["competitors"], aliases
        )

        data["feature_matrix_mode"] = "vendor"
        # 预渲染 § 5.2 矩阵 HTML（避免模板嵌套循环 quadratic 性能）
        data["section5_2_html"] = _render_section5_2_html(
            data["feature_comparison_matrix"], data["unique_features_by_competitor"]
        )

    # ─────────── 派生:6 家总分排名(领先/中坚/跟随) ───────────
    comp_total = []
    for c in data["competitors"]:
        sc = c["scores"]
        valid = [v for v in sc.values() if isinstance(v, (int, float))]
        avg = sum(valid) / max(len(valid), 1)
        comp_total.append({"name": c["name"], "avg": round(avg, 1), "scores": sc})
    comp_total.sort(key=lambda x: x["avg"], reverse=True)
    if comp_total:
        top = comp_total[0]["avg"]
        for c in comp_total:
            if c["avg"] >= top * 0.9:
                c["tier"] = "领先"
            elif c["avg"] >= top * 0.7:
                c["tier"] = "中坚"
            else:
                c["tier"] = "跟随"
    data["competitors_ranked"] = comp_total
    data["leader"] = comp_total[0]["name"] if comp_total else "—"
    data["leader_avg"] = comp_total[0]["avg"] if comp_total else 0

    # 派生指标
    data["competitor_count"] = len(data["competitors"])
    data["opportunity_count"] = len(data["opportunities"])
    data["gap_count"] = len(data["gaps"])
    # 评分真实性:任一竞品有非占位分(Step 3 已评)才渲染评分/排名/momentum
    # 派生章节 —— 全占位时 4.1 奖牌表与 6.x momentum 结论都是伪权威
    data["scores_real"] = any(
        c.get("scores_confidence") == "normal"
        for c in data["competitors"]
    )

    # 平均成熟度（六维综合均值）
    if data["competitors"]:
        totals = []
        for c in data["competitors"]:
            sc = c["scores"]
            valid = [v for v in sc.values() if isinstance(v, (int, float))]
            avg = sum(valid) / max(len(valid), 1)
            totals.append((c["name"], avg))
        if totals:
            avg_all = sum(t for _, t in totals) / len(totals)
            data["avg_maturity"] = round(avg_all, 1)
            totals.sort(key=lambda x: x[1], reverse=True)
            data["top_competitor"] = totals[0][0]
            data["bottom_competitor"] = totals[-1][0]

    # 最严重空白
    if data["gaps"]:
        sev_order = {"high": 3, "medium": 2, "low": 1}
        sorted_gaps = sorted(
            data["gaps"],
            key=lambda g: sev_order.get(g.get("severity", "low"), 0),
            reverse=True,
        )
        top_gap_text = sorted_gaps[0].get("gap") or "—"
        data["top_gap"] = _truncate_with_ellipsis(top_gap_text, 18)
    else:
        data["top_gap"] = "—"

    # 最高颠覆机会
    if data["opportunities"]:
        sorted_opps = sorted(
            data["opportunities"],
            key=lambda o: o.get("disrupt_score", 0)
            if isinstance(o.get("disrupt_score"), (int, float))
            else 0,
            reverse=True,
        )
        top_opp_title = sorted_opps[0].get("title") or "—"
        data["top_opportunity"] = _truncate_with_ellipsis(top_opp_title, 18)
    else:
        data["top_opportunity"] = "—"

    # 主题副标题
    data["topic_accent"] = "竞争格局与颠覆性机会图谱"
    data["glossary"] = _build_glossary(data.get("topic") or "")

    # ─────────── § 8 其他竞品资料库 ───────────
    data["other_competitors"] = data.get("other_competitors", [])
    # 按 category 分组
    other_by_cat = {}
    for oc in data["other_competitors"]:
        cat = oc.get("category", "其他")
        other_by_cat.setdefault(cat, []).append(oc)
    data["other_competitors_by_category"] = sorted(
        [
            {"category": k, "players": v, "count": len(v)}
            for k, v in other_by_cat.items()
        ],
        key=lambda x: -x["count"],
    )

    # ─────────── 竞品扩展字段 ───────────
    for c in data["competitors"]:
        c.setdefault("headquarters", "—")
        c.setdefault("funding", "—")
        c.setdefault("team_size", "—")

    # 功能全集（竞品画像）—— 5.2 产品功能
    # 支持两种 schema：
    #   1. 顶层 feature_catalog: {comp_name: [{category, name, desc}, ...]}
    #   2. 每个 competitor.feature_catalog: {comp_name: [...]} (新 schema)
    fc = data.get("feature_catalog") or {}
    if not fc:
        # 兼容新 schema：从每个 competitor 抽出
        for c in data["competitors"]:
            inner = c.get("feature_catalog")
            if inner:
                # inner 是 {name: [...]}
                for k, v in inner.items():
                    fc[k] = v
    fc = fc or {}
    comp_meta = {c["name"]: c for c in data["competitors"]}
    fc_companies = []
    for comp_name, features in fc.items():
        if not isinstance(features, list):
            continue
        meta = comp_meta.get(comp_name, {})
        # 按 category 分组并保留输入顺序
        cat_dict: dict = {}
        cat_order: list = []
        for f in features:
            cat = f.get("category", "其他")
            if cat not in cat_dict:
                cat_dict[cat] = []
                cat_order.append(cat)
            cat_dict[cat].append(f)
        categories = [(c, cat_dict[c]) for c in cat_order]
        fc_companies.append(
            {
                "name": comp_name,
                "url": meta.get("url", "#"),
                "icon": meta.get("icon") or smart_icon(comp_name),
                "category_count": len(categories),
                "feature_count": len(features),
                "categories": categories,
            }
        )
    data["feature_catalog_companies"] = fc_companies
    data["feature_total_count"] = sum(
        len(v) for v in fc.values() if isinstance(v, list)
    )

    # ─────────── 7 个新增派生字段 ───────────

    # 1. 6 维评分对比表 —— 每维度排名 + 每家总分
    score_table = []  # [{dim_key, dim_label, rows: [{name, score, rank}]}]
    for dim_key, dim_label in SCORE_DIMS:
        rows = []
        for c in data["competitors"]:
            rows.append({"name": c["name"], "score": c["scores"].get(dim_key, 0)})
        rows.sort(key=lambda r: r["score"], reverse=True)
        for i, r in enumerate(rows):
            r["rank"] = i + 1
            r["medal"] = (
                "🥇" if i == 0 else ("🥈" if i == 1 else ("🥉" if i == 2 else ""))
            )
        score_table.append({"key": dim_key, "label": dim_label, "rows": rows})
    data["score_table"] = score_table

    # 2. 竞品总分排名 —— 综合分(6 维均值)排序 + 标签(领先/跟随/落后)
    # 已在上面派生,这里跳过

    # 3. 成立年份时间线 —— 按 founded 升序
    timeline = []
    for c in data["competitors"]:
        try:
            y = int(c.get("founded", 0))
        except (ValueError, TypeError):
            continue
        if y > 0:
            timeline.append(
                {
                    "year": y,
                    "name": c["name"],
                    "stage": c.get("stage", "未知"),
                    "tagline": c.get("tagline", "")[:50],
                    "url": c.get("url", "#"),
                }
            )
    timeline.sort(key=lambda e: e["year"])
    data["founding_timeline"] = timeline
    data["founding_year_range"] = (
        f"{timeline[0]['year']}–{timeline[-1]['year']}" if timeline else "—"
    )

    # 4. 阶段分布统计
    stage_dist: dict = {}
    for c in data["competitors"]:
        s = c.get("stage", "未知")
        stage_dist[s] = stage_dist.get(s, 0) + 1
    data["stage_distribution"] = sorted(
        [
            {
                "stage": k,
                "count": v,
                "pct": round(v / max(len(data["competitors"]), 1) * 100, 1),
            }
            for k, v in stage_dist.items()
        ],
        key=lambda x: x["count"],
        reverse=True,
    )

    # 5. 目标用户重叠矩阵 —— 谁抢同一批用户
    user_overlap = []
    user_to_comps: dict = {}
    for c in data["competitors"]:
        for u in c.get("target_users", []):
            user_to_comps.setdefault(u, []).append(c["name"])
    for u, comps in sorted(user_to_comps.items(), key=lambda x: -len(x[1])):
        if len(comps) >= 2:
            user_overlap.append(
                {
                    "user_segment": u,
                    "competitors": comps,
                    "count": len(comps),
                    "intensity": "🔥" * min(len(comps), 4),
                }
            )
    data["user_overlap"] = user_overlap

    # 6. 技术栈信号汇总 —— 每个竞品 tech_signals + 全行业聚类
    # 注意:tech_signals 在 Phase 1A 后是 list[{name, source, _ref}],兼容旧 list[str]
    tech_clusters: dict = {}
    for c in data["competitors"]:
        for t in c.get("tech_signals", []):
            if isinstance(t, dict):
                key = t.get("name", "")
                ref = t.get("_ref", 0)
            else:
                key = t
                ref = 0
            key = key.strip()
            tech_clusters.setdefault(key, {"adopters": [], "ref": ref})
            tech_clusters[key]["adopters"].append(c["name"])
    tech_top = sorted(
        [
            {
                "signal": k,
                "adopters": v["adopters"],
                "count": len(v["adopters"]),
                "_ref": v["ref"],
            }
            for k, v in tech_clusters.items()
        ],
        key=lambda x: x["count"],
        reverse=True,
    )
    data["tech_signal_clusters"] = tech_top[:15]
    data["tech_signals_per_competitor"] = {
        c["name"]: c.get("tech_signals", []) for c in data["competitors"]
    }

    # 7. SWOT —— 每家基于 strengths/weaknesses/tech_signals
    # 注意:tech_signals / differentiators 仍可能是 list[str] (Phase 1A 后才会变 dict),
    # 这里直接引用,source/_ref 由下面的 enrichment 阶段统一处理。
    swot_per_competitor = []
    for c in data["competitors"]:
        # 占位条目(待补充)不进 SWOT 卡 —— 渲染为优雅空态,而不是
        # 一堆"待补充:爬取文本中未提取到…"的占位行污染阅读(真实事故:
        # 每张卡 3 条相同占位文本 × 5 家 = 15 行垃圾)
        strengths = [
            x for x in c.get("strengths", [])[:6] if not _is_placeholder_swot(x)
        ][:3]
        weaknesses = [
            x for x in c.get("weaknesses", [])[:6] if not _is_placeholder_swot(x)
        ][:3]
        swot_per_competitor.append(
            {
                "name": c["name"],
                "icon": c.get("icon", smart_icon(c["name"])),
                "url": c.get("url", "#"),
                "stage": c.get("stage", "未知"),
                "founded": c.get("founded", "—"),
                "tagline": c.get("tagline", ""),
                "target_users": c.get("target_users", []),
                "pricing": c.get("pricing", "—"),
                "pricing_tiers": c.get("pricing_tiers", []),
                "core_features": c.get("core_features", []),
                "strengths": strengths,
                "weaknesses": weaknesses,
                "tech_signals": c.get("tech_signals", []),
                "differentiators": c.get("differentiators", []),
                "scores": c.get("scores", {}),
            }
        )
    data["swot_per_competitor"] = swot_per_competitor

    # (原 7.5 重复 enrichment 已删除 —— sources 收集阶段的第 8 步已统一处理,
    #  且 SWOT 引用的是同一 list 对象,二次包装只会在旧数据上重新引入 homepage 兜底)

    # ─────────── 全局引用系统(sources 已在开头收集完毕) ───────────

    data["sources"] = sources
    data["source_count"] = len(sources)
    bot_verified = sum(1 for s in sources if s.get("verified") == "bot")
    data["source_bot_verified_count"] = bot_verified
    data["source_user_verified_count"] = len(sources) - bot_verified
    # 来源按 kind 分组（用于 Sources 区块分组渲染）
    by_kind: dict = {}
    for s in sources:
        by_kind.setdefault(s["kind"], []).append(s)
    data["sources_by_kind"] = [
        {"kind": k, "items": v, "count": len(v)}
        for k, v in sorted(by_kind.items(), key=lambda x: -len(x[1]))
    ]
    # 来源分组图标 + 标签
    data["kind_icon"] = {
        "narrative": "📰",
        "competitor_meta": "🏢",
        "strength": "💪",
        "weakness": "⚠",
        "feature": "⚙",
        "market_segment": "🧭",
        "gap": "🕳",
        "opportunity": "💡",
        "opportunity_validation": "📊",
        "other_competitor": "📦",
    }
    data["kind_label"] = {
        "narrative": "背景叙事",
        "competitor_meta": "竞品基础信息",
        "strength": "竞品优势",
        "weakness": "竞品弱点",
        "feature": "产品功能",
        "market_segment": "市场细分",
        "gap": "市场空白",
        "opportunity": "颠覆机会",
        "opportunity_validation": "机会验证",
        "other_competitor": "其他竞品",
    }
    # 预渲染 sources 区块 HTML（避免嵌套 for 循环 quadratic 性能）
    data["sources_html"] = _render_sources_html(data["sources_by_kind"])

    # ─────────── 证据质量审计:把"不可追溯/未验证"大声暴露,绝不静默降级 ───────────
    evidence_warnings: list = []
    if not sources:
        evidence_warnings.append(
            "本报告没有任何带 URL 的来源证据 —— 全部结论不可追溯。"
            "通常是 03-analysis.json 缺失 source/quote 字段(Step 3 未按证据三元组规范填写),"
            "请重跑 /youzi Step 2(爬取)+ Step 3(逐字段带 {值, source_url, quote} 提取)。"
        )
    unverified_pricing = [
        c["name"]
        for c in data["competitors"]
        if not c.get("pricing_verified")
        and (c.get("pricing") or "").strip()
        and c.get("pricing") != "—"
    ]
    if unverified_pricing:
        evidence_warnings.append(
            f"{len(unverified_pricing)} 家竞品的定价未经 ≥2 引擎交叉验证"
            f"（{('、'.join(unverified_pricing[:6]))}{'…' if len(unverified_pricing) > 6 else ''}）"
            "—— 价格可能过时或不准确,请以官网为准。"
        )
    placeholder_only = [
        c["name"]
        for c in data["competitors"]
        if c.get("strengths")
        and all(_is_placeholder_swot(s) for s in c["strengths"])
    ]
    if placeholder_only:
        evidence_warnings.append(
            f"{len(placeholder_only)} 家竞品的优劣势为占位符(爬取证据不足,"
            f"涉及 {'、'.join(placeholder_only[:6])})—— 相关板块不可作为决策依据。"
        )
    uncalibrated = [
        c["name"] for c in data["competitors"] if c.get("scores_confidence") == "low"
    ]
    if uncalibrated:
        evidence_warnings.append(
            f"{len(uncalibrated)} 家竞品的 6 维评分是默认值而非基于证据的评分"
            f"（{('、'.join(uncalibrated[:6]))}{'…' if len(uncalibrated) > 6 else ''}）"
            "—— 排名/领先/跟随标签不具参考性。"
        )
    data["evidence_warnings"] = evidence_warnings

    # 8. Top 3 机会卡片 —— disrupt_score 排序
    sorted_opps = sorted(
        [
            o
            for o in data["opportunities"]
            if isinstance(o.get("disrupt_score"), (int, float))
        ],
        key=lambda o: o["disrupt_score"],
        reverse=True,
    )
    data["top_opportunities"] = sorted_opps[:3]

    # 9. 竞品分段 + 每段平均分
    if comp_total:
        data["leader"] = comp_total[0]["name"] if comp_total else "—"
        data["leader_avg"] = comp_total[0]["avg"] if comp_total else 0

    # 10. 市场细分概览（在 sources 之后构建以带上 _ref）
    seg_summary = []
    for seg in data["market_segments"]:
        seg_summary.append(
            {
                "label": seg.get("label", "—"),
                "desc": seg.get("desc", ""),
                "players": seg.get("players", []),
                "count": len(seg.get("players", [])),
                "icon": seg.get("icon", smart_icon(seg.get("label", ""))),
                "source": seg.get("source", ""),
                "_ref": seg.get("_ref", 0),
            }
        )
    data["segments_summary"] = seg_summary

    # TOC 列表（按章节顺序，与 templates/report.html 的 section ID 对齐）
    data["toc_items"] = [
        {"id": "background", "title": "背景与目标"},
        {"id": "conclusion", "title": "结论与建议"},
        {"id": "positioning", "title": "产品定位分析"},
        {"id": "business", "title": "商业策略分析"},
        {"id": "product-design", "title": "产品设计分析"},
        {"id": "data-growth", "title": "产品数据分析"},
        {"id": "user-feedback", "title": "用户反馈分析"},
        {"id": "other-competitors", "title": "其他竞品资料库"},
        {"id": "sources", "title": "来源与参考资料"},
    ]

    return data


# 历史 bug 回归检查:旧版 _infer_strengths_weaknesses 注入过的伪造引文 ——
# 这些句子从未真的来自 G2,渲染结果里再次出现即视为伪造回归
_FABRICATED_QUOTE_RX = re.compile(
    r"Pricing gets expensive at scale"
    r"|AI features locked behind premium plans"
    r"|BSP 接入 WhatsApp Business API,合规可靠"
)


def self_check(data, html_str):
    """严格自检：未解析模板标签 / 数据完整性 / 文件大小。"""
    print("\n=== self-check ===")
    ok = True
    # 1. 模板里残留的 {{ 或 {% 必须为 0
    unresolved = html_str.count("{{") + html_str.count("{%")
    # 排除 JS 中的字面 {{ ... }}（例如模板字符串）—— 这里简单计数足够
    checks = [
        ("竞品 ≥ 3", len(data["competitors"]) >= 3),
        ("executive_summary 非空", bool(data.get("executive_summary"))),
        ("主题 token 完整", "--accent:" in html_str and "--bg:" in html_str),
        ("未解析模板标签 = 0", unresolved == 0),
        (
            "所有 9 个 section 渲染齐全",
            all(
                f'id="{sid}"' in html_str
                for sid in [
                    "background",
                    "conclusion",
                    "positioning",
                    "business",
                    "product-design",
                    "data-growth",
                    "user-feedback",
                    # other-competitors 无数据时整节隐藏(空"0 家"章节 =
                    # 目录噪音),有数据才要求渲染
                    *(
                        ["other-competitors"]
                        if data.get("other_competitors")
                        else []
                    ),
                    "sources",
                ]
            ),
        ),
        (
            "每个竞品卡片渲染",
            html_str.count('class="card"') >= len(data["competitors"]),
        ),
        (
            "无伪造引文(历史 bug 回归检查)",
            not _FABRICATED_QUOTE_RX.search(html_str),
        ),
        (
            "每个竞品都有定价验证标记(pricing_verified)",
            all("pricing_verified" in c for c in data["competitors"]),
        ),
        (
            "未验证定价都有 ⚠ 徽章(逐竞品)",
            html_str.count('class="pc-unverified"')
            >= sum(
                1
                for c in data["competitors"]
                if c.get("pricing_verified") is False
                and (c.get("pricing") or "").strip()
                and c.get("pricing") != "—"
            ),
        ),
        (
            "证据可追溯(source_count > 0)",
            data.get("source_count", 0) > 0,
        ),
        (
            "无 Python repr 泄漏(乱码回归)",
            "{&#39;" not in html_str and "['" not in html_str and "{'" not in html_str,
        ),
        (
            "无占位符混入启发/机会派生板块",
            not any(
                "待补充" in (it.get("inspiration") or "") + (it.get("opportunity") or "")
                for group in list((data.get("inspiration_points") or {}).values())
                + list((data.get("opportunity_points") or {}).values())
                for it in group
            ),
        ),
    ]
    for name, passed in checks:
        print(f"  {'✓' if passed else '✗'} {name}")
        if not passed:
            ok = False
    # opportunities 不再由脚本伪造(历史缺陷)——只提醒,不算失败。
    # 完整报告应跑 SKILL.md Step 3:LLM 基于证据生成。
    if len(data["opportunities"]) < 3:
        print("  ⚠ opportunities < 3 —— 由 LLM Step 3 基于证据生成(见 references/analysis-framework.md),脚本不代劳")
    if unresolved > 0:
        print(f"  ⚠ 检测到 {unresolved} 处未解析标签，前 3 行：")
        for line in html_str.split("\n"):
            if "{{" in line or "{%" in line:
                print(f"    → {line.strip()[:120]}")
    size_kb = len(html_str.encode("utf-8")) / 1024
    print(
        f"  📦 HTML size: {size_kb:.1f} KB"
        + (" (✓ < 1.5MB)" if size_kb < 1500 else " (✗ 过大)")
    )
    print("==================\n")
    return ok


def main():
    ap = argparse.ArgumentParser(description="youzi 报告渲染器")
    ap.add_argument("--input", required=True, help="analysis JSON 文件")
    ap.add_argument("--output", required=True, help="输出 HTML 路径")
    ap.add_argument("--template", default=str(DEFAULT_TEMPLATE), help="模板路径")
    ap.add_argument("--no-check", action="store_true", help="跳过自检")
    ap.add_argument("--verbose", "-v", action="store_true", help="输出 DEBUG 级日志")
    args = ap.parse_args()

    if args.verbose:
        logger.setLevel(logging.DEBUG)
        logger.debug("Verbose mode enabled")

    in_path = Path(args.input)
    out_path = Path(args.output)
    tmpl_path = Path(args.template)

    if not in_path.exists():
        logger.error("input not found: %s", in_path)
        sys.exit(1)
    if not tmpl_path.exists():
        logger.error("template not found: %s", tmpl_path)
        sys.exit(1)

    logger.info("loading JSON: %s", in_path)
    raw = json.loads(in_path.read_text(encoding="utf-8"))
    logger.info("normalizing data")
    data = normalize(raw)

    logger.info("loading template: %s", tmpl_path)
    template = Template(tmpl_path.read_text(encoding="utf-8"))
    logger.debug("rendering HTML")
    rendered = template.render(data)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered, encoding="utf-8")
    logger.info("wrote %d chars to %s", len(rendered), out_path)
    logger.info("  · %d competitors", data["competitor_count"])
    logger.info("  · %d opportunities", data["opportunity_count"])
    logger.info("  · %d sources", data["source_count"])

    if not args.no_check:
        ok = self_check(data, rendered)
        sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
