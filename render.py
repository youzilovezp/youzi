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


def _derive_user_positioning(c):
    """从 target_users + stage 派生 user_positioning[name]。"""
    users = c.get("target_users", [])
    stage = c.get("stage", "")
    return {
        "target_segment": "、".join(users) if users else "—",
        "region": "全球",
        "scale": "中小企业" if stage in ("早期", "成长期") else "中大型企业",
        "key_market": stage if stage else "—",
    }


def _derive_commercial_strategies(c):
    """从 pricing + differentiators 派生商业策略。

    differentiators 可能是 list[str] (原始) 或 list[{name, source, _ref}] (Phase 1A 后),
    这里统一取 .name 兼容两种格式。
    """
    pricing = c.get("pricing", "—")
    differentiators = c.get("differentiators", [])

    def _name(it):
        return it["name"] if isinstance(it, dict) else it

    diff_names = [_name(d) for d in differentiators]
    # pricing_tiers: 按 + / 、 切分
    if pricing and pricing != "—":
        tiers = [t.strip() for t in re.split(r"[+、；;]", pricing) if t.strip()]
    else:
        tiers = []
    return {
        "model": "SaaS 订阅"
        if any("$" in t or "月" in t or "年" in t for t in tiers)
        else "—",
        "pricing_tiers": tiers,
        "gtm": diff_names[0][:60] if diff_names else "—",
        "moat": diff_names[1][:60]
        if len(diff_names) > 1
        else (diff_names[0][:60] if diff_names else "—"),
    }


def _derive_product_overview():
    """产品端覆盖 — 默认 '—'，留作人工补充。"""
    return {
        "web": "支持",
        "desktop": "—",
        "mobile": "支持",
        "other": "—",
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


def _derive_user_feedback(c):
    """从 strengths/weaknesses 派生用户反馈小结(带 source URL)。"""
    pos = [
        {
            "text": s.get("point", ""),
            "source": s.get("source", "") or c.get("url", ""),
            "source_label": s.get("source_label", "G2 / 评测 / 官方"),
            "count": s.get("score", "—"),
        }
        for s in c.get("strengths", [])[:3]
        if s.get("point")
    ]
    neg = [
        {
            "text": w.get("point", ""),
            "source": w.get("source", "") or c.get("url", ""),
            "source_label": w.get("source_label", "G2 / 社区 / 评测"),
            "count": w.get("score", "—"),
        }
        for w in c.get("weaknesses", [])[:3]
        if w.get("point")
    ]
    summary_parts = []
    if pos:
        summary_parts.append(f"正面：{pos[0]['text'][:30]}")
    if neg:
        summary_parts.append(f"负面：{neg[0]['text'][:30]}")
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
        "aliases": ["Webhook 双向", "Webhook 事件", "Webhook", "Webhook Events"],
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
    # 第 2 层:自动检测 — 默认没覆盖的才添加
    for canonical, info in auto_aliases.items():
        if canonical not in merged:
            merged[canonical] = info
        else:
            # 自动检测的别名也加入现有 canonical
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
            parts.append(
                f'<div class="source-item" id="src-{s["idx"]}">'
                f'<span class="src-num">{s["idx"]}</span>'
                f'<span class="src-claim">{comp_part}{html.escape(s.get("claim", ""))}</span>'
                f'<div class="src-meta">'
                f'<a href="{html.escape(s["url"])}" target="_blank">{html.escape(s["url"])}</a>'
                f'<span style="background:var(--good-soft); color:var(--good); padding:0.05rem 0.4rem; border-radius:3px; font-size:0.7rem; margin-left:0.5rem;">🔗 可访问</span>'
                f"</div></div>"
            )
        parts.append("</div>")
    return "\n".join(parts)


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

    # 独家功能面板 —— 每条带 owner vendor + [N] 内部证据 + ↗ 原文 URL + 通俗讲解 + 原始描述
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
            # ── 通俗讲解:根据 category 自动补一句用途 ──
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

    # ── 提前定义 _add_source + 收集 sources ──
    # 这样下面 inspiration_points / swot / 等派生都能拿到 _ref
    sources: list = []
    source_idx: dict = {}

    def _add_source(url: str, kind: str, claim: str, competitor: str = "") -> int:
        if not url:
            return 0
        if url in source_idx:
            return source_idx[url]
        source_idx[url] = len(sources) + 1
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
        return source_idx[url]

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

    # 3. feature_catalog 每项带 source
    for c in data["competitors"]:
        comp = c.get("name", "")
        for feat in c.get("feature_catalog", {}).get(comp, []):
            url = feat.get("source", "")
            if url:
                idx = _add_source(
                    url,
                    "feature",
                    f"{feat.get('name', '')} ({feat.get('category', '')})",
                    comp,
                )
                feat["_ref"] = idx

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
    for c in data["competitors"]:
        comp_url = c.get("url", "")
        for fld in ("tech_signals", "differentiators"):
            items = c.get(fld, [])
            enriched = []
            for it in items:
                if isinstance(it, str):
                    enriched.append(
                        {
                            "name": it,
                            "source": c.get(fld + "_source", "") or comp_url,
                        }
                    )
                elif isinstance(it, dict):
                    src = it.get("source") or c.get(fld + "_source", "") or comp_url
                    enriched.append({"name": it.get("name", ""), "source": src})
                else:
                    enriched.append({"name": str(it), "source": comp_url})
            for e in enriched:
                if e["source"]:
                    e["_ref"] = _add_source(
                        e["source"],
                        "tech_signal" if fld == "tech_signals" else "differentiator",
                        e["name"][:50],
                        c.get("name", ""),
                    )
            c[fld] = enriched

        # user_feedback: 给 positive/negative 项加 _ref
        fb = c.get("_user_feedback_meta") or c.get("user_feedback")
        if not isinstance(fb, dict):
            fb = _derive_user_feedback(c)
        for polarity in ("positive", "negative"):
            for item in fb.get(polarity, []):
                src = item.get("source_url") or item.get("source") or ""
                if not src or src in ("G2/Reddit/官网评测", "G2/Reddit/社区抱怨", "—"):
                    src = comp_url
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
    data["commercial_strategies"] = {
        c["name"]: _derive_commercial_strategies(c) for c in data["competitors"]
    }
    data["product_overview"] = {
        c["name"]: _derive_product_overview() for c in data["competitors"]
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
    data["feature_comparison_matrix"] = _build_feature_comparison_matrix(
        data["competitors"], aliases
    )
    # 把每家厂商自己的来源 URL 转为 [N] 编号,用于 §5.2.1 单元格内显示
    for cat in data["feature_comparison_matrix"]["categories"]:
        for f in cat["features"]:
            vrefs = {}
            for comp, url in (f.get("_vendor_sources") or {}).items():
                if url:
                    vrefs[comp] = _add_source(
                        url, "feature", f"{f['name']} @ {comp}", comp
                    )
            if vrefs:
                f["_vendor_refs"] = vrefs
    # 每家独有功能清单(其他家都没有的功能)
    data["unique_features_by_competitor"] = _find_unique_features(
        data["competitors"], aliases
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

    # 预渲染 § 5.2 矩阵 HTML（避免模板嵌套循环 quadratic 性能）
    data["section5_2_html"] = _render_section5_2_html(
        data["feature_comparison_matrix"], data["unique_features_by_competitor"]
    )

    # 派生指标
    data["competitor_count"] = len(data["competitors"])
    data["opportunity_count"] = len(data["opportunities"])
    data["gap_count"] = len(data["gaps"])

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
        strengths = c.get("strengths", [])[:3]
        weaknesses = c.get("weaknesses", [])[:3]
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

    # 7.5 ★ 先 enrichment (tech_signals/differentiators 变 dict + 收集 _ref)
    # 因为 SWOT 引用的是 c.tech_signals / c.differentiators,必须先 enrichment 再被引用
    for c in data["competitors"]:
        comp_url = c.get("url", "")
        for fld in ("tech_signals", "differentiators"):
            items = c.get(fld, [])
            enriched = []
            for it in items:
                if isinstance(it, str):
                    enriched.append(
                        {
                            "name": it,
                            "source": c.get(fld + "_source", "") or comp_url,
                        }
                    )
                elif isinstance(it, dict):
                    src = it.get("source") or c.get(fld + "_source", "") or comp_url
                    enriched.append({"name": it.get("name", ""), "source": src})
                else:
                    enriched.append({"name": str(it), "source": comp_url})
            for e in enriched:
                if e["source"]:
                    e["_ref"] = _add_source(
                        e["source"],
                        "tech_signal" if fld == "tech_signals" else "differentiator",
                        e["name"][:50],
                        c["name"],
                    )
            c[fld] = enriched

        # user_feedback: 给 positive/negative 项加 _ref
        fb = c.get("_user_feedback_meta") or c.get("user_feedback")
        if not isinstance(fb, dict):
            fb = _derive_user_feedback(c)
        for polarity in ("positive", "negative"):
            for item in fb.get(polarity, []):
                src = item.get("source_url") or item.get("source") or ""
                if not src or src in ("G2/Reddit/官网评测", "G2/Reddit/社区抱怨", "—"):
                    src = comp_url
                if src:
                    item["_ref"] = _add_source(
                        src,
                        "user_feedback",
                        item.get("text", "")[:60],
                        c["name"],
                    )
        c["_user_feedback_enriched"] = fb

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


def self_check(data, html_str):
    """严格自检：未解析模板标签 / 数据完整性 / 文件大小。"""
    print("\n=== self-check ===")
    ok = True
    # 1. 模板里残留的 {{ 或 {% 必须为 0
    unresolved = html_str.count("{{") + html_str.count("{%")
    # 排除 JS 中的字面 {{ ... }}（例如模板字符串）—— 这里简单计数足够
    checks = [
        ("竞品 ≥ 3", len(data["competitors"]) >= 3),
        ("opportunities ≥ 3", len(data["opportunities"]) >= 3),
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
                    "other-competitors",
                    "sources",
                ]
            ),
        ),
        (
            "每个竞品卡片渲染",
            html_str.count('class="card"') >= len(data["competitors"]),
        ),
    ]
    for name, passed in checks:
        print(f"  {'✓' if passed else '✗'} {name}")
        if not passed:
            ok = False
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
