#!/usr/bin/env python3
"""
crawl_competitors · 批量爬取指定竞品并生成分析 JSON

输入: 竞品名称列表 (逗号分隔)
输出: 可直接给 render.py 用的 13 字段分析 JSON

工作流:
1. resolve_competitors(names) → URL + 价格/功能页
2. 对每个竞品,scrape_smart 并行抓取 features / pricing / docs
3. 启发式提取 13 字段(tagline / founded / pricing / features / strengths / weaknesses / tech_signals)
4. 跨竞品去重(feature_aliases 合并 + 同名自动检测)
5. 输出 03-analysis.json

用法:
    python3 scripts/crawl_competitors.py --competitors "ycloud,sleekflow,wati,respond.io,meetbot" \
        --topic "WhatsApp 营销 SaaS" --output ./03-analysis.json
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adapters.competitor_resolver import resolve_competitors  # noqa: E402
from adapters import scrape_smart  # noqa: E402


# ─────────────────────────────────────────────────────────
# 启发式结构化提取 — 从 markdown 内容抽取 13 字段
# ─────────────────────────────────────────────────────────

_PRICE_PATTERNS = [
    r"\$\s*\d+(?:[\.,]\d+)?(?:\s*/\s*(?:mo|month|user|seat|yr|year))?",
    r"(?:USD|SGD|HKD)\s*\$?\s*\d+",
    r"(?:免费|Free|Trial|免费试用)",
    r"(?:联系销售|Contact Sales|企业版|报价|起\s*\$|from\s*\$)",
    r"(?:Pro|Plus|Business|Team)\s*\$?\d+",
    r"\$\d+\s*/\s*月",
    r"\d+\s*条\s*(?:消息|对话)\s*/\s*月",
    r"per\s+(?:user|seat|agent)\s*/\s*(?:month|mo)",
    r"(?:起价|starting\s+(?:at|from)|from)\s*\$?\d+",
]

# 非价格金额:融资额/估值/营收/案例数字("$62.5M Series B" 曾被当 WATI 价格)
_NOT_A_PRICE_RX = re.compile(
    r"raised?|funding|funded|series\s+[a-e]\b|valuation|revenue|营收|融资|估值"
    r"|\b\d+\s*(?:k|m|b|bn|million|billion|万|亿)\b"
    r"|credits?|tokens?|额度|积分",
    re.I,
)

_FOUNDED_PATTERNS = [
    # "founded in Hong Kong in 2017"(respond.io 形态:地点插在中间)
    r"(?:founded|established|launched)\s+(?:in\s+)?[\w\s,]{0,30}?\b(20\d{2}|19\d{2})\b",
    r"(?:since|成立于|创立于)\s*(20\d{2}|19\d{2})",
]
# 版权行/URL 日期不是成立年份(真实事故:WATI founded=2026 来自版权/URL 里的日期)
_FOUNDED_JUNK_RX = re.compile(
    r"©|\(c\)|copyright|all\s+rights|/20\d{2}/|uploads/|assets/|reserved", re.I,
)

_LOCATION_PATTERNS = [
    # "headquartered in Kuala Lumpur, Malaysia"(respond.io 形态:in 挡住
    # 大写捕获,历史正则要求总部词后紧跟大写地名 → 全部漏提取)
    r"(?:headquartered|based|located)\s+(?:in\s+)?([A-Z][a-zA-Z\s,]+?)(?:[\.,]|$)",
    r"(?:总部|位于|总部位于)\s*:?\s*([^\n，。,]{2,30})",
    r"(?:HQ|headquarters|Headquarters)\s*[：:]\s*([^\n,]+)",
]

_TEAM_SIZE_PATTERNS = [
    r"(\d+)\s*[\-\s]?\s*(?:employees|staff|team\s+members|people|员工|团队|人)\b",
    r"team\s+(?:size\s*:?|of)\s*(\d+)",
]

# 排除噪音行
_NOISE_PREFIXES = (
    ".css-",
    ".js-",
    "(function",
    "(self.",
    "var ",
    "const ",
    "let ",
    "window.",
    "document.",
    "document",
    "$(",
    "{\\n",
    "import ",
    "{",
    "<!",
    "/*",
    "//",
    "<!--",
)


def _is_noise_line(line: str) -> bool:
    """检测是否为 CSS/JS/HTML 噪音行。"""
    s = line.strip()
    if not s:
        return True
    if len(s) < 15:
        return True
    for p in _NOISE_PREFIXES:
        if s.startswith(p):
            return True
    # 看起来像代码:含大量 { } ; = 或 () 但无空格
    if sum(s.count(c) for c in "{};=") > 5:
        return True
    # 全小写无空格 + 大量连字符 → CSS class
    if re.match(r"^[\w\-\.\(\)\{\}\:\;]{15,}$", s) and " " not in s:
        return True
    return False


def _clean_markdown(md: str) -> str:
    """去掉 CSS/JS 噪音行。"""
    if not md:
        return ""
    lines = md.split("\n")
    clean = []
    for line in lines:
        if not _is_noise_line(line):
            clean.append(line)
    return "\n".join(clean)


def _extract_price(markdown: str) -> str:
    """从 markdown 中找定价信息(过滤融资额/credits 等非价格数字)。"""
    # 逐行清洗但保留含价格 token 的短行:"Growth $39/mo"(13 字符)曾被
    # len<15 噪音规则整行删掉,导致定价页只剩营销句(真实事故)
    lines = []
    for line in markdown.split("\n"):
        if not _is_noise_line(line) or _PRICE_TOKEN_RX.search(line):
            lines.append(line)
    md = "\n".join(lines)
    hits = []
    for pat in _PRICE_PATTERNS:
        for m in re.finditer(pat, md, re.IGNORECASE):
            hit = m.group(0).strip()
            if not hit or len(hit) >= 80:
                continue
            # 金额后紧跟 M/B/万/亿 量级词,或就近语境含融资/营收 → 不是价格。
            # 豁免:命中自身带强套餐信号(/month、/mo、/月、per month,或前面
            # 紧邻套餐词 Starter/Growth/Pro/from/起)—— "Earned $600k revenue.
            # Starter $49/month" 里的 $49 是真价格,不能被隔壁 revenue 误杀
            tail = md[m.end():m.end() + 6]
            if re.match(r"\s*(?:m\b|b\b|bn\b|k\b|million|billion|万|亿)", tail, re.I):
                continue
            after = md[m.end():m.end() + 12]
            before = md[max(0, m.start() - 20):m.start()]
            strong_price_signal = bool(
                re.match(r"\s*(?:/(?:mo|month|yr|year|月)|per\s+(?:month|user))", after, re.I)
                or re.search(r"(?:starter|growth|pro|plus|business|team|basic|from|起|free)\s*[*~`]?\s*$", before, re.I)
            )
            if strong_price_signal:
                hits.append(hit)
                continue
            line_start = md.rfind("\n", 0, m.start()) + 1
            line_end = md.find("\n", m.end())
            line = md[line_start:line_end if line_end != -1 else len(md)]
            if len(line) > 60:
                ctx = md[max(line_start, m.start() - 40):min(m.end() + 40, line_end if line_end != -1 else len(md))]
            else:
                ctx = line
            if _NOT_A_PRICE_RX.search(ctx):
                continue
            hits.append(hit)
    seen = set()
    unique = []
    for h in hits:
        key = h.lower().replace(" ", "")
        # 价格 token 天然很短("$39/mo"=6 字符),噪音规则只用于非价格文本命中
        has_price = bool(_PRICE_TOKEN_RX.search(h)) or bool(
            re.search(r"(?:联系销售|Contact Sales|企业版|报价|免费|free)", h, re.I)
        )
        if key not in seen and (has_price or not _is_noise_line(h)):
            seen.add(key)
            unique.append(h)
        if len(unique) >= 5:
            break
    return " / ".join(unique[:5]) if unique else "—"


# 定价上下文词:价格必须出现在含这些词的行/邻行,才视为"套餐价格"而非随机数字
# billed annually/monthly = 计费周期 toggle(WATI 等站的价格标题与周期分离)
_PLAN_CONTEXT_RX = re.compile(
    r"(plan|pricing|per\s+(?:month|mo|user|seat|agent|month/user)|/mo|/month|月|免费"
    r"|free|trial|starter|growth|pro|plus|business|enterprise|team|basic"
    r"|billed\s+(?:annually|monthly)|\bmonth\b|contact\s+sales|报价|定制|起)", re.I,
)

# 套餐名词(用于从孤立价格标题上方的标题里恢复套餐名)
_PLAN_NAME_RX = re.compile(
    r"starter|growth|\bpro\b|plus|business|enterprise|team|basic|free\b|solo"
    r"|scale|advanced|essential|standard|premium|lite|fundamental|专业版|企业版|免费版", re.I,
)


def _dedupe_plan_name(name: str) -> str:
    """修复驼峰融合的套餐名:"Pro AIPro"(Pro AI + Pro)→ "Pro AI"。

    firecrawl 渲染卡片时会把相邻文本节点无缝拼接,"Pro AI"+"Pro" 融成
    "Pro AIPro"。规则:末词驼峰拆开后,去掉与前面词重复的部分。
    """
    words = re.findall(r"[A-Za-z]+|\d+", name)
    if len(words) >= 2:
        last = words[-1]
        # 末词内驼峰拆分:AIPro → AI + Pro
        inner = re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z]+|\d+|[a-z]+", last)
        if len(inner) >= 2:
            # 拆出的尾部词组若已出现在前面的词里 → 末词只留前半
            front_words = {w.lower() for w in words[:-1]}
            while len(inner) > 1 and inner[-1].lower() in front_words:
                inner.pop()
            new_last = "".join(inner)
            if new_last != last:
                words = words[:-1] + ([new_last] if new_last else [])
    # 词级去重(保序,大小写不敏感)
    seen, out = set(), []
    for w in words:
        k = w.lower()
        if k in seen:
            continue
        seen.add(k)
        out.append(w)
    return " ".join(out) if out else name

# 严格计费周期(周期字段只认这些 —— 邻行营销文案"Big wins for small teams"
# 曾因含 "team" 匹配上下文词而被误当计费周期)
_PERIOD_STRICT_RX = re.compile(
    r"per\s+(?:user|seat|agent)\s*(?:/|per\s*)\s*(?:month|mo)\b"  # 组合优先,防"per user"吞掉"/month"
    r"|billed\s+(?:annually|monthly|yearly)|annually|monthly|yearly"
    r"|per\s+(?:month|user|seat|year)|/mo(?:nth)?|/yr|/year|one[-\s]?time"
    r"|月付|年付|按月|按年|/月|/年", re.I,
)
# 划线促销价:~~$99~~ now $79 —— 划线段是旧价,不能当现价提取
_STRIKETHROUGH_RX = re.compile(r"~~[^~]+~~")
_PRICE_TOKEN_RX = re.compile(
    r"(?:US\$|\$|US＄|€|£|S\$|₹)\s?\d[\d,\.]*|\bRs\.\s?\d[\d,\.]*|免费|Free\s+(?:trial|plan|forever)"
    r"|\d+\s*credits?|联系销售|Contact\s+Sales|企业报价|Custom\s+pricing", re.I,
)


# 附加项/导航/营销噪音:不是套餐价格(additional users / shopify addon / 登录按钮 / 培训课程)
# 含葡/西语(WATI 等站多语言定价页的 shopify 附加项曾绕过英文过滤进入票选)
# channel/account/shop/number 是按量附加项(YCloud 事故:$5/channel、$10/account
# 混进套餐价,真套餐 $468/yr 反被挤出)
_PRICE_ADDON_RX = re.compile(
    r"additional|add-?on|extra\s+(user|seat|agent|member)|another\s+team\s+member"
    r"|\bper\s+(?:channel|account|shop|number|sender)\b|/\s*(?:channel|account|shop|number|sender)\b"
    r"|登录|log\s*in|book\s+a\s+demo|预约演示|预约\s*demo|schedule\s+a\s+demo"
    r"|contact\s+us\s*$|not\s+available|unavailable"
    r"|add\s+more|top\s*up|充值"
    r"|课程|course|training|webinar|ebook|白皮书|udemy|academy"
    r"|shopify|complemento|compra\s+do\s+aplicativo|requiere\s+la\s+compra|加购|附加"
    r"|refer\s*\s*&?\s*earn|referral|奖励|推荐有礼",
    re.I,
)
# CTA 整行(聚类后代表行也要再查一遍 —— 不同引擎的变体可能绕过行级过滤)
_PRICE_CTA_LINE_RX = re.compile(
    r"^(?:start\s+)?(?:free\s+trial|free\s+plan|免费试用|免费版|try\s+\w+\s+free"
    r"|get\s+started|book\s+a\s+demo|预约演示|contact\s+(?:sales|us)|登录"
    r"|log\s*in|sign\s*up)\s*$",
    re.I,
)
# CTA 词全部剥掉后什么都不剩 → 该行是纯按钮组合(可能融合:"Contact salesFree Trial")
_CTA_PHRASES_RX = re.compile(
    r"contact\s*sales|free\s*trial|book\s*(?:a\s*)?demo|get\s*started|预约演示|免费试用"
    r"|立即(?:体验|咨询|购买)|talk\s*to\s*sales|request\s*a\s*demo|sign\s*up|log\s*in",
    re.I,
)


def _extract_price_lines(markdown: str) -> List[Dict]:
    """抽取带套餐上下文的价格行,返回结构化 {line, plan, price, period}。

    保持文档顺序。不做 _clean_markdown 预清洗 —— 它会删掉 "## $119" 这类
    短行(len<15 噪音规则),而孤立价格标题正是真实定价页的主流形态
    (WATI 事故:全部真实套餐价被预清洗删光,只剩营销句)。

    上下文判定支持两种形态:
    a) 同行含套餐词 —— "Growth $39/mo"
    b) 孤立价格标题行 —— "## **$59**"(真实站常见:套餐名/计费周期在邻行)
       ±2 行窗口找计费周期,向上 20 行找套餐名标题(同一价格卡片内)
    """
    raw_lines = markdown.split("\n")
    out, seen = [], set()
    for i, line in enumerate(raw_lines):
        line = line.strip()
        if not line or len(line) > 120:
            continue
        # 逐字原文(引擎 md 的原始行,G2 回查依据)—— 之后的划线剥离/
        # 套餐名前缀/token 融合都是合成,不可 grep
        verbatim = line[:120]
        # 划线促销段是旧价(~~$99~~ now $79):整段剥掉,只留现价
        if "~~" in line:
            line = _STRIKETHROUGH_RX.sub(" ", line).strip()
            if not line:
                continue
        # 纯 CTA 行("Start Free Trial")在行级就丢弃 —— 它不含任何货币,
        # 却占用 out[:6] 名额把真套餐价挤掉(respond.io $279/mo 事故)
        if _CTA_PHRASES_RX.sub("", line).strip(" —-|·,，:").strip() == "":
            continue
        tok = _PRICE_TOKEN_RX.search(line)
        if not tok:
            continue
        # 价格行仍防代码垃圾,但纯价格+markdown符号的短行(## $119)放行
        # —— len<15 噪音规则会误杀孤立价格标题(真实事故:WATI $59/$119/$279)
        if _is_noise_line(line) and not (
            re.fullmatch(r"[*_`#>\s\$€£₹US\d,\.]+", line)
            # 带短前缀/后缀的孤立价格行("now **$79**/mo" 划线价剥离后的形态)
            or (
                _PRICE_TOKEN_RX.search(line)
                and len(re.sub(r"[^A-Za-z\u4e00-\u9fff/]", "", line)) <= 12
            )
        ):
            continue
        # 卡片内噪音扫描(带上边界):addon/充值标签常与价格分离 ——
        #   "Shopify Integration" 标签在 "$4.99/Month" 上方 2 行(WATI 中文区)
        #   "Shopify addon" 与 "$4.99/month" 同行或紧邻(英文区)
        #   "…back as message credits" 在 "₹999" 标题下方(印度充值 promo)
        # 向上最多 3 行、向下 1 行;扫到套餐名标题/裸套餐行即停 ——
        # 那是新卡片边界,噪音行属于上一张卡(±2 裸窗口会误伤隔壁真套餐)。
        ctx_probe = [line]  # 本行也要查:WATI "Pay ₹999 … message credits" 同行自带充值语境
        if i + 1 < len(raw_lines) and raw_lines[i + 1].strip():
            ctx_probe.append(raw_lines[i + 1].strip())
        for j in range(i - 1, max(0, i - 4), -1):
            w = raw_lines[j].strip()
            if not w:
                continue
            ctx_probe.append(w)
            m_h = re.match(r"^[\->\s]*#{1,4}\s+(.+)$", w)
            h_txt = m_h.group(1) if m_h else w
            is_card_head = (
                (m_h and len(h_txt.split()) <= 4 and _PLAN_NAME_RX.search(h_txt))
                or (
                    2 <= len(w) <= 25
                    and len(w.split()) <= 3
                    and re.fullmatch(r"[\w\s\-+\u4e00-\u9fff]+", w)
                    and _PLAN_NAME_RX.search(w)
                )
            )
            if is_card_head:
                break
        probe_text = " \n ".join(ctx_probe)
        if _PRICE_ADDON_RX.search(probe_text) or _NOT_A_PRICE_RX.search(probe_text):
            continue
        plan, period = "", ""
        if _PLAN_CONTEXT_RX.search(line):
            ctx_line = line  # 形态 a:自带上下文
        else:
            # 形态 b:孤立价格行,看 ±2 行窗口
            window = raw_lines[max(0, i - 2): i + 3]
            ctx_hits = [w.strip() for w in window
                        if w.strip() and _PLAN_CONTEXT_RX.search(w)
                        and not _PRICE_TOKEN_RX.search(w)]
            if not ctx_hits:
                continue
            # 取最近的一条上下文(优先下方 —— 计费周期通常在价格正下方)
            below = [w for w in ctx_hits if w in [x.strip() for x in raw_lines[i + 1: i + 3]]]
            ctx_word = (below or ctx_hits)[-1]
            # 周期字段只认严格周期词;邻行是营销文案时周期留空(不做猜测)
            pm = _PERIOD_STRICT_RX.search(ctx_word)
            period = pm.group(0) if pm else ""
            ctx_line = f"{line} ({ctx_word[:40]})"
        # 套餐名恢复(途径 2):行内显式标注 "(Upgrade to Pro)" /
        # "升级到专业版" —— YCloud 定价页把套餐名放括号里
        if not plan:
            pm2 = re.search(
                r"(?:upgrade\s+to|choose\s+|选择|升级到|upgrade)",
                ctx_line, re.I,
            )
            if pm2:
                tail = ctx_line[pm2.end():].strip(" ·:-—()")
                nm = re.match(r"([A-Za-z\u4e00-\u9fff][\w\u4e00-\u9fff ]{1,18})", tail)
                if nm and _PLAN_NAME_RX.search(nm.group(1)):
                    plan = _dedupe_plan_name(nm.group(1).strip())
        # 套餐名恢复(途径 1):向上扫 20 行找套餐名(同一价格卡片内;
        # 遇到上一个价格行即停,防止跨卡片串名)。两种形态都认:
        #   a) markdown 标题:"## **Growth**"(WATI 形态)
        #   b) 裸短行:"Enterprise" / "Growth"(YCloud 形态 —— 卡片标题
        #      不是 # 标题,历史缺陷导致 YCloud 全部套餐名丢失)
        # 防营销句误中:"Find your right plan for business" 含 "business"
        # 曾被当成套餐名 → 标题限 ≤4 词、裸行限 ≤3 词且 ≤25 字符
        # 判断"行内是否已有套餐名"只看原始行 —— ctx_line 尾部拼接的
        # "(下张卡标题)" 会让本行误判已有套餐名而跳过恢复($468 事故)
        if not plan and not _PLAN_NAME_RX.search(line):
            # range 不含 stop:必须用 max(-1, i-21) 才能扫到第 0 行
            # (历史 off-by-one:i=1 时 range(0, 0, -1) 为空,紧邻首行的
            # 套餐标题永远恢复不到)
            for j in range(i - 1, max(-1, i - 21), -1):
                w = raw_lines[j].strip()
                if _PRICE_TOKEN_RX.search(w) and j < i - 1:
                    # 撞到价格行:若它已带套餐名,说明是同卡片的另一计费
                    # 选项(月付行下方的 "Billed $468 /yr")→ 继承套餐名
                    prev_plan = ""
                    for pl in out:
                        if pl["line"].startswith(w[:20]) or w[:12] in pl["line"]:
                            prev_plan = pl.get("plan") or ""
                            break
                    if prev_plan:
                        plan = prev_plan
                        break
                    break  # 真·卡片边界
                m2 = re.match(r"^[\->\s]*#{1,4}\s+(.+)$", w)
                cand = ""
                if m2:
                    cand = re.sub(r"[*_`#]", "", m2.group(1)).strip()
                    if not (_PLAN_NAME_RX.search(cand) and len(cand) < 45
                            and len(cand.split()) <= 4
                            and not _PRICE_TOKEN_RX.search(cand)):
                        cand = ""
                elif (
                    2 <= len(w) <= 25
                    and len(w.split()) <= 3
                    and re.fullmatch(r"[\w\s\-+\u4e00-\u9fff]+", w)
                    and _PLAN_NAME_RX.search(w)
                    and not _PRICE_TOKEN_RX.search(w)
                ):
                    cand = w  # 裸短行:Enterprise / Growth / Pro AI
                if cand:
                    plan = _dedupe_plan_name(cand)
                    break
        if plan:
            ctx_line = f"{plan} · {ctx_line}"
        if _PRICE_ADDON_RX.search(ctx_line):
            continue
        # 归一:去 markdown 链接/强调符号;融合 token 拆开
        text = re.sub(r"\[([^\]]*)\]\([^)]+\)", r"\1", ctx_line)
        text = re.sub(r"[*_`#>]", "", text).strip()
        text = re.sub(
            r"((?:US\$|S\$|\$|€|£|₹)\s?\d[\d,\.]*)\s*(?=(?:US\$|S\$|\$|€|£|₹)\s?\d)",
            r"\1 / ", text,
        )
        # 同行内找计费周期(形态 a 或 period 还没填)—— 只认严格周期词
        if not period:
            bm = _PERIOD_STRICT_RX.search(text)
            period = bm.group(0) if bm else ""
        # 多价格 token 时:促销格式把旧价放前("$1,188 $948/yr"/"was $99 now $79")
        # —— 首匹配会取到旧价(真实事故:respond.io 年付显示 $1,188 而现价 $948)。
        # 规则:优先取紧邻严格周期词的 token;否则取最后一个(现价在后)。
        _tok_rx = re.compile(r"(?:US\$|HK\$|NT\$|CA\$|A\$|S\$|\$|€|£|₹|\bRs\.)\s?\d[\d,\.]*", re.I)
        _toks = list(_tok_rx.finditer(text))
        if _toks:
            pm = _PERIOD_STRICT_RX.search(text)
            if pm:
                # 周期词前的最近 token(如 "$948/yr" → $948;"billed yearly"前的 $59)
                price_m = max(
                    (t for t in _toks if t.end() <= pm.start() + 12),
                    key=lambda t: t.end(), default=_toks[-1],
                )
            else:
                price_m = _toks[-1]
        else:
            price_m = re.search(r"免费|Free\s+(?:trial|plan)|联系销售|Contact\s+Sales", text, re.I)
        # 周期跟随所选价格 token(±15 字符窗口):"$948/yr (billed yearly)"
        # 的周期是 /yr,不能取邻行 "79/month"(真实事故:respond 年付价标成月付)
        if price_m:
            _near = text[price_m.end():price_m.end() + 15]
            _pm_near = _PERIOD_STRICT_RX.match(_near) or _PERIOD_STRICT_RX.search(_near[:8])
            if _pm_near:
                period = _pm_near.group(0)
        key = text[:40].lower()
        if key not in seen:
            seen.add(key)
            # 币种与数字间的空格排版噪音("US$ 149" → "US$149")
            _pv = re.sub(
                r"([A-Za-z]{1,2}\$|Rs\.)\s+(\d)", r"\1\2",
                price_m.group(0) if price_m else "",
            )
            out.append({
                "line": text[:100],
                "raw_line": verbatim,
                "plan": plan,
                "price": _pv,
                "period": period,
            })
    return out[:10]


def _normalize_price_token(s: str) -> str:
    """/mo $39 → $39;US$39.00 → US$39;Rs. 999 → ₹999 —— 用于跨引擎投票比对。

    币种前缀必须保留:HK$/NT$/CA$/A$ 塌缩成裸 $ 会让不同货币的价
    投成同一票(历史缺陷:港币价和美元价互相"验证")。
    """
    m = re.search(r"(?:US\$|HK\$|NT\$|CA\$|A\$|S\$|\$|€|£|₹|\bRs\.|¥|￥)\s?(\d+)(?:\.\d+)?", s, re.I)
    if m:
        if "₹" in s or re.search(r"rs\.?", s, re.I):
            cur = "₹"
        elif "HK$" in s:
            cur = "HK$"
        elif "NT$" in s:
            cur = "NT$"
        elif "CA$" in s:
            cur = "CA$"
        elif re.search(r"(?<![A-Za-z])A\$", s):
            cur = "A$"
        elif "US$" in s:
            cur = "US$"
        elif "S$" in s:
            cur = "S$"
        elif "€" in s:
            cur = "€"
        elif "£" in s:
            cur = "£"
        elif "¥" in s or "￥" in s:
            cur = "¥"
        else:
            cur = "$"
        return f"{cur}{m.group(1)}"
    if re.search(r"免费|free", s, re.I):
        return "free"
    if re.search(r"联系销售|contact\s+sales|企业报价|custom", s, re.I):
        return "enterprise-quote"
    return s.lower().strip()[:20]


def _extract_pricing_evidence(scrape_result: Dict, pricing_url: str = "") -> Dict:
    """跨引擎定价证据:对每个引擎的 markdown 分别抽价格行,投票聚类。

    输出的 pricing 是"带套餐上下文的整行"(如 'Growth $39/mo'),不是散落的数字 ——
    跨引擎同 token 组的行聚为一簇,取最短(最干净)的代表行。
    verified = picked 簇中最高票数 ≥2 个独立引擎(且 engines 列表必与
    verified 一致 —— 历史版本曾出现 verified=True 但 engines=[] 的矛盾输出)。
    每条证据带 source_url + scraped_at,报告端可回链核对。
    """
    per_engine = {}
    for r in (scrape_result.get("all_results") or []):
        if r.get("success") and r.get("markdown"):
            lines = _extract_price_lines(r["markdown"])
            if lines:
                per_engine[r.get("scraper", "?")] = lines

    # F5 引擎独立性:按引擎原文内容哈希判定 —— 名称不同的两个引擎若拿到
    # 逐字相同的内容(同一反爬/区域变体),只是一次取证,不是交叉验证
    engine_hash = {}
    for r in (scrape_result.get("all_results") or []):
        if r.get("success") and r.get("markdown"):
            engine_hash[r.get("scraper", "?")] = _content_hash(r["markdown"])

    # 按 token 组聚类行:key = 行内全部价格 token 的有序元组
    clusters: Dict[tuple, Dict] = {}
    token_engines: Dict[str, set] = {}
    for eng, lines in per_engine.items():
        for pline in lines:
            toks = sorted(
                {
                    _normalize_price_token(m.group(0))
                    for m in _PRICE_TOKEN_RX.finditer(pline["line"])
                }
            )
            if not toks:
                continue
            for t in toks:
                token_engines.setdefault(t, set()).add(eng)
            ent = clusters.setdefault(
                tuple(toks), {"line": pline["line"], "len": len(pline["line"]), "engines": set(), "parts": pline}
            )
            ent["engines"].add(eng)
            ent.setdefault("hashes", set()).add(
                engine_hash.get(eng) or f"no-md:{eng}"
            )
            # 代表行选择:信息量优先(套餐名 > 计费周期),然后取短
            # —— "Growth $59 (billed annually)" 完胜裸 "$59"
            def _rep_key(s: str):
                return (
                    bool(_PLAN_NAME_RX.search(s)),
                    bool(re.search(r"billed|/mo|/yr|/month|annual|year|月付|年付", s, re.I)),
                    -len(s),
                )
            if _rep_key(pline["line"]) > _rep_key(ent["line"]):
                ent["line"], ent["len"], ent["parts"] = pline["line"], len(pline["line"]), pline

    # 排序:含真实货币/计费 token 的行绝对优先(营销句"7天免费试用"即使
    # 4 引擎全票也不是价格),然后按引擎一致数,同票取短行;CTA 二次过滤
    def _has_currency(line: str) -> bool:
        return bool(re.search(r"(?:US\$|\$|€|£|S\$|₹|\bRs\.)\s?\d", line))

    ranked = sorted(
        clusters.items(),
        key=lambda kv: (
            -int(_has_currency(kv[1]["line"])),
            # 有套餐名的簇绝对优先 —— 无名簇排在带名簇之后,确保
            # "单票无名让位"规则被评估时 picked 里已有足够带名档
            # (WATI "$40/Month" 事故:无名簇按行短排第一,让位规则
            # 在 picked=[] 时永不触发)
            -int(bool(kv[1]["parts"].get("plan"))),
            -int(bool(kv[1]["parts"].get("period"))),
            # 单 token 簇(真分档)优先于多 token 摘要行:导航/汇总行
            # "Starter $39 Pro $99" 曾靠套餐词加分赢下排名,再借 seen_tokens
            # 把真正的 $39/$99 分档簇全部挤掉(历史缺陷:多档丢档)
            -int(len(kv[0]) == 1),
            -len(kv[1]["engines"]),
            kv[1]["len"],
        ),
    )
    picked, seen_tokens = [], set()
    for toks, ent in ranked:
        line = ent["line"].strip()
        if _PRICE_CTA_LINE_RX.match(line):
            continue
        # 剥掉 CTA 词后无实质内容 → 纯按钮组合行
        if _CTA_PHRASES_RX.sub("", line).strip(" —-|·,，"):
            pass  # 还有实质内容,保留
        else:
            continue
        # 裸价格行(无套餐名/无周期/无其他实词,如 YCloud 页面上散落的 "$5")
        # 只在前 3 席之外让位 —— 已有 ≥3 个富信息 tier 时不再收编裸价格
        parts = ent["parts"]
        if (
            not parts.get("plan")
            and not parts.get("period")
            and len(picked) >= 3
            and len(re.sub(_PRICE_TOKEN_RX.pattern, "", line).strip(" ·—-()")) < 6
        ):
            continue
        # 纯非货币 token(free trial / 联系销售)且无套餐名 → 营销句/CTA
        # 不是定价档(WATI 事故:"7 days free trial, zero setup fees"
        # 4 引擎全票混进 tiers)。Free plan 带 $0 等货币 token 的保留。
        parts0 = ent["parts"]
        has_currency_tok = any(
            re.search(r"(?:US\$|\$|€|£|₹|Rs\.?|[\u00a5\uffe5]|S\$|HK\$)", t)
            for t in toks
        )
        if not has_currency_tok and not parts0.get("plan"):
            continue
        # 单引擎票 + 无套餐名 + 已有 ≥3 个带名档位 → 变体噪音让位
        # (WATI "$40/Month" 间歇出现于某引擎的多语言区,与任何套餐对不上)
        if (
            len(ent["engines"]) == 1
            and not parts0.get("plan")
            and sum(1 for e2 in picked if e2["parts"].get("plan")) >= 3
        ):
            continue
        # 同 token 已有更高票簇则跳过(避免 $39 与 $39/mo 重复出现)
        if seen_tokens & set(toks):
            continue
        seen_tokens |= set(toks)
        picked.append(ent)
        if len(picked) >= 8:
            break
    # 展示串:由结构化部件生成干净行(plan · price (period)) —— 原始投票行
    # 带验证上下文括号("(Big wins for small teams...)"),留在 vote_detail
    # 供追溯,不进展示。超长时在完整行边界截断(绝不切半行)。
    def _fmt(p):
        core = f"{p['plan']} · {p['price']}" if p["plan"] else p["price"]
        return f"{core} ({p['period']})" if p["period"] else core
    pricing, used = [], 0
    for e in picked:
        dl = _fmt(e["parts"])
        if used + len(dl) > 150 and pricing:
            pricing.append("…(见官网)")
            break
        pricing.append(dl)
        used += len(dl) + 3
    pricing = " / ".join(pricing) if pricing else "—"
    # verified 按「内容独立引擎数」:同一变体页被 N 个引擎复述仍只有 1 票
    # (历史缺陷:仅数引擎名个数,反爬变体页被双引擎抓到 = 错价被交叉"验证")
    max_indep = max(
        (len(e.get("hashes") or e["engines"]) for e in picked), default=0
    )
    verified = max_indep >= 2
    engines = sorted({e for ent in picked for e in ent["engines"]})

    # 结构化 tiers(套餐名/价格/周期 来自证据行的原始部件,render 直接用,
    # 不再从展示串二次猜测解析)。排序:免费($0)在前,其余按数值升序 ——
    # 历史输出按票数排序,$39/$89/$0/$399 阅读体验差
    def _tier_sort_key(p):
        m = re.search(r"(\d[\d,]*(?:\.\d+)?)", p.get("price") or "")
        if not m:
            return (1, float("inf"))
        v = float(m.group(1).replace(",", ""))
        return (0 if v > 0 else -1, v)  # $0/free 最前,非数值最后

    ordered_parts = sorted(
        (e["parts"] for e in picked), key=_tier_sort_key
    )
    # 不做跨周期合并(历史教训:把 $59(按年结算月价) 与 $69(按月结算月价)
    # 合并成 "$59 或 $69 · 月付/年付" 语义错误 —— $69 不是年价!)。
    # 现在每行保留原始周期文本(billed annually / /yr / /mo ...),
    # 同套餐多计费选项各自成行,按 (套餐分组, 价格升序) 排列。
    _plan_rank = {}
    for p in ordered_parts:
        pn = (p.get("plan") or "").strip()
        if pn and pn.lower() not in _plan_rank:
            _plan_rank[pn.lower()] = len(_plan_rank)

    def _row_key(p):
        m = re.search(r"(\d[\d,]*(?:\.\d+)?)", p.get("price") or "")
        v = float(m.group(1).replace(",", "")) if m else float("inf")
        pn = (p.get("plan") or "").strip().lower()
        grp = _plan_rank.get(pn, 99)
        named = 0 if pn else 1
        return (0 if v == 0 else 1, named, grp, v)

    tiers = [
        {
            "name": (p["plan"] or "—")[:40],
            "price": p["price"] or "—",
            "billing_period": (p["period"][:24] if p["period"] else "—"),
            "features": [],
            "source_url": pricing_url,
        }
        for p in sorted(ordered_parts, key=_row_key)
    ][:8]

    # 无公开价格时的套餐名降级:很多站(尤其中国 SaaS)有套餐结构但
    # 不公示数字(Meebot:专业版/企业版/定制版)—— 输出套餐名 + "价格未公开",
    # 比 "—" 有信息量且完全诚实
    plan_names = []
    if not picked:
        for r in (scrape_result.get("all_results") or []):
            if not (r.get("success") and r.get("markdown")):
                continue
            for pline2 in r["markdown"].split("\n"):
                pline2 = pline2.strip().lstrip("#*-> ").strip()
                if (
                    2 <= len(pline2) <= 12
                    and re.search(r"专业版|企业版|定制版|标准版|基础版|旗舰版|免费版|高级版", pline2)
                    and pline2 not in plan_names
                ):
                    plan_names.append(pline2)
                elif re.fullmatch(r"starter|basic|standard|pro|premium|enterprise|growth|team|plus", pline2, re.I):
                    l2 = pline2.capitalize()
                    if l2 not in plan_names:
                        plan_names.append(l2)
            if len(plan_names) >= 4:
                break
        if plan_names:
            # 不断言「未公开」:我们无法区分"官网真不公示"与"本次变体页没抓到"
            pricing = " / ".join(plan_names[:4]) + "（数字价格本次未能提取，请以官网定价页为准）"
            tiers = [
                {"name": n, "price": "未能提取(见注)", "billing_period": "—", "features": [], "source_url": pricing_url}
                for n in plan_names[:4]
            ]

    return {
        "pricing": pricing,
        "verified": verified,
        "engines": engines,
        # F2:无任何证据(0 价格行且无套餐名)时 source 留空 —— 未抓到的
        # 页面不能当来源(404 URL 当 pricing_source 的事故)
        "source_url": pricing_url if (per_engine or plan_names) else "",
        "scraped_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
        "vote_detail": [
            {"line": e["line"],
             "raw_line": (e["parts"].get("raw_line") or e["line"]),
             "engines": sorted(e["engines"]),
             "independent_votes": len(e.get("hashes") or e["engines"])}
            for e in picked
        ],
        "tiers": tiers,
        "per_engine_lines": {
            k: [d["line"] for d in v[:3]] for k, v in list(per_engine.items())[:6]
        },
    }


_PRICING_CACHE_PATH = ROOT / "storage" / "pricing-cache.json"
_PRICING_CACHE_TTL_DAYS = 14


def _content_hash(md: str) -> str:
    """引擎原文的内容指纹(空白归一化后 SHA-256 前 16 位)。

    用途:①manifest.fetched 记录(G3 独立性判定);②定价投票的引擎
    独立性(F5)—— 两引擎拿到同一反爬变体页时指纹相同,不算交叉验证。
    """
    import hashlib
    return hashlib.sha256(
        re.sub(r"\s+", " ", (md or "")).encode("utf-8")
    ).hexdigest()[:16]


def _load_pricing_cache() -> Dict:
    try:
        return json.loads(_PRICING_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_pricing_cache(cache: Dict) -> None:
    # 原子写(tmp+rename):避免并发运行读到半截 JSON 被静默判损坏
    import os
    try:
        _PRICING_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PRICING_CACHE_PATH.with_suffix(".json.tmp")
        tmp.write_text(
            json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        os.replace(tmp, _PRICING_CACHE_PATH)
    except Exception:
        pass


def _cache_fresh(cached: Dict) -> bool:
    """缓存是否仍在 TTL 内(scraped_at 距今 ≤14 天)。

    历史缺陷:_PRICING_CACHE_TTL_DAYS 定义后从未被引用,陈旧缓存
    永久有效 —— verified 定价可能是数月前的价格。
    """
    import calendar
    try:
        ts = time.strptime((cached or {}).get("scraped_at", ""), "%Y-%m-%d %H:%M UTC")
        age_days = (time.time() - calendar.timegm(ts)) / 86400.0
    except (ValueError, OverflowError):
        return False
    return 0 <= age_days <= _PRICING_CACHE_TTL_DAYS


def _has_real_prices(tiers: list) -> bool:
    return any(
        re.search(r"\d", t.get("price") or "") for t in (tiers or [])
    )


def _extract_founded(markdown: str) -> str:
    # 同时在原始文本上匹配:清洗会删掉裸年份行/打乱 "founded in X
    # 
    # 2019" 的相邻结构(respond.io 形态),raw 里 founded 与年份只隔 ~20 字符
    md = _clean_markdown(markdown) + "\n" + (markdown or "")
    for pat in _FOUNDED_PATTERNS:
        for m in re.finditer(pat, md, re.IGNORECASE):
            # 版权行/URL 日期不算(排除 "© 2026"/"uploads/2025/08")
            ctx_start = max(0, m.start() - 40)
            ctx = md[ctx_start:m.end() + 10]
            if _FOUNDED_JUNK_RX.search(ctx):
                continue
            year_match = re.search(r"\b(20\d{2}|19\d{2})\b", m.group(0))
            if year_match:
                year = int(year_match.group(1))
                if year <= _NOW_YEAR:  # 未来年份必是噪音
                    return str(year)
    return "—"


_NOW_YEAR = __import__("datetime").date.today().year


def _extract_location(markdown: str) -> str:
    md = _clean_markdown(markdown)
    for pat in _LOCATION_PATTERNS:
        m = re.search(pat, md, re.IGNORECASE)
        if m:
            loc = m.group(1).strip().strip(".,").strip()
            loc = re.sub(r"^(?:in|at|于|在)\s+", "", loc, flags=re.I)  # "in Kuala Lumpur" → "Kuala Lumpur"
            # 地名常 <15 字符("Kuala Lumpur"),只挡代码/链接噪音,不做长度噪音判定
            if (
                loc
                and 3 < len(loc) < 50
                and not re.search(r"[{};=]|https?://|\bfunction\b|\.css|\.js", loc)
            ):
                return loc
    return "—"


def _extract_team_size(markdown: str) -> str:
    md = _clean_markdown(markdown)
    for pat in _TEAM_SIZE_PATTERNS:
        m = re.search(pat, md, re.IGNORECASE)
        if m:
            size = re.search(r"\d+", m.group(0))
            if size:
                return size.group(0) + "+"
    return "—"


# F3 行级归属:founded/HQ/team 命中行的上下文特征(找 quote 用)
_COMPANY_CTX_RX = {
    "founded": re.compile(
        r"founded|established|launched|成立于|创立于|\b(19|20)\d{2}\b", re.I),
    "headquarters": re.compile(
        r"headquartered|based in|总部|位于|address", re.I),
    "team_size": re.compile(r"employees|people|团队|员工|人", re.I),
}


def _extract_company_field(pages, extractor, ctx_rx):
    """逐页跑 extractor,返回 (value, url, quote) —— 行级归属。

    历史缺陷:来源只标到「about 页或官网」页级 —— 年份在定价页命中
    也被标成官网来源,读者点开首页找不到任何公司信息。
    pages: [(markdown, url)] 按优先级排序(about 先,home 次之)。
    """
    for md, url in pages:
        if not md or not url:
            continue
        val = extractor(md)
        if val and val != "—":
            quote = ""
            for line in md.split("\n"):
                t = line.strip().strip("*_`#> ")
                if 3 <= len(t) <= 160 and ctx_rx.search(t):
                    quote = t[:120]
                    break
            return val, url, quote
    return "", "", ""


# cookie/GDPR 同意横幅 + meta 标题 + 隐私/版权页脚 —— SPA 站正文提取最常见的伪 tagline
# (真实事故:WATI tagline = "The technical storage or access is necessary for
#  the legitimate purpose...",Respond.io tagline = "Do Not Sell or Share My
#  Personal Information",Tidio tagline = meta title)
_TAGLINE_JUNK_RX = re.compile(
    r"technical storage|cookie|gdpr|consent|privacy|terms of service"
    r"|personal data|personal information|third-party tools|we use cookies"
    r"|accept all|do not sell|all rights reserved|copyright"
    r"|必要的|合法目的|隐私政策|同意|^\s*title\s*[:：]|^\s*-\s*\[", re.I,
)


def _clean_tagline_text(text: str) -> str:
    """tagline 文本清理:图片 alt 残留/驼峰重复词/emoji 前缀。

    真实事故:YCloud H1 = "Boost your business on !whatsApp WhatsApp"
    (图片 alt "!whatsApp" 混入 + 驼峰变体重复词 "whatsApp WhatsApp")。
    """
    # 剥内嵌图片语法 ![alt](url) —— alt 不是文案
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", text)
    # 剥残留的孤立 "!" 前缀 token(alt 被部分剥离后)
    text = re.sub(r"(?<!\w)![\w-]+", "", text)
    # 剥 markdown 强调符号(Respond.io "**Explore...**" 残留事故)
    text = re.sub(r"[*_`]", "", text)
    # emoji/符号前缀清理(🚀 等 promo banner 图标)
    text = re.sub(r"^[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\s]+", "", text).strip()
    # 词级去重(大小写不敏感,保首个):whatsApp WhatsApp → WhatsApp
    words, seen = [], set()
    for w in text.split():
        k = w.lower().strip(".,!?:;·—-")
        if k and k in seen and re.match(r"^[\w\-']+$", w):
            continue  # 重复词跳过(连接词 the/of 允许重复)
        if k:
            seen.add(k)
        words.append(w)
    text = " ".join(words).strip()
    # 去品牌尾巴:"AI Suite for ... | SleekFlow" → 前半(<title> 惯用格式,
    # 主题在前品牌在后;若品牌在前 "Respond.io | #1 ..." 则保留后半)
    if "|" in text:
        parts = [x.strip() for x in text.split("|") if x.strip()]
        if len(parts) == 2:
            # 品牌段 = 与域名无关的短大写词;主题段通常更长
            if len(parts[1]) <= len(parts[0]) and len(parts[0]) >= 12:
                text = parts[0]
    return text


def _extract_tagline(markdown: str) -> str:
    """提取 tagline。

    策略(按可靠性排序):
    1. 首个 H1(hero 主标题;营销站真实 tagline)
    2. H2(过滤 promo banner:emoji 前缀/含"Get X and Y"句式优先级降低)
    3. 纯散文段落(过横幅过滤)
    都失败 → 空(上层显示 —,LLM Step 3 基于证据补)
    """
    md = _clean_markdown(markdown)
    h1s, h2s = [], []
    for line in md.split("\n"):
        line = line.strip()
        m = re.match(r"^#{1,2}\s+(.+)$", line)
        if not m:
            continue
        text = _clean_tagline_text(m.group(1))
        text = re.sub(r"\[([^\]]*)\]\([^)]+\)", r"\1", text)  # 去链接语法
        if not (8 <= len(text) <= 90):
            continue
        if _TAGLINE_JUNK_RX.search(text) or _is_noise_line(text):
            continue
        if text.endswith((".", "!", "?")) and len(text) > 60:
            continue  # 长句子是正文不是标题
        (h1s if line.startswith("# ") else h2s).append(text)
    for cand in h1s + h2s:
        return cand[:150]
    # 纯散文段落(过滤 emoji 开头的 promo 横幅 —— 多语言站会连出 4-6 条)
    for line in md.split("\n"):
        line = line.strip()
        if not line or line.startswith(("#", "[", "!", "- ", "*", "|")):
            continue
        if re.match(r"^[\U0001F300-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]", line):
            continue  # emoji 开头 = promo banner
        line = _clean_tagline_text(line)
        if re.search(r"[\[\]()!](https?://|www\.)", line):  # 含链接
            continue
        if len(line) < 30 or len(line) > 200 or _is_noise_line(line):
            continue
        if _TAGLINE_JUNK_RX.search(line):
            continue
        if not line.endswith(":"):
            return line[:150]
    return ""


def _looks_like_only_js_or_404(markdown: str) -> bool:
    """检测 markdown 是否几乎全是 JS 代码或 404。"""
    if not markdown or len(markdown) < 200:
        return True
    # 404 检测
    if "404" in markdown[:500] and "not found" in markdown[:500].lower():
        return True
    if "could not be found" in markdown[:1000].lower():
        return True
    if "page not found" in markdown[:1000].lower():
        return True
    # JS-only: 代码行占 > 50%
    lines = [ln for ln in markdown.split("\n") if ln.strip()]
    if not lines:
        return True
    js_like = sum(
        1
        for ln in lines
        if ln.strip().startswith(
            (
                "function",
                "var ",
                "const ",
                "let ",
                "if (",
                "//",
                "/*",
                "window.",
                "document.",
                "import ",
            )
        )
        or sum(ln.count(c) for c in "{};=") > 5
    )
    return js_like / len(lines) > 0.5




def _get_fallback_from_builtin(canonical_name: str) -> Dict:
    """内置 fallback 已删除。

    历史教训:这里曾内置一份静态价格/团队规模库,站点一改价就全错,
    且 SPA 站会整站落到过期数据上 —— "爬取的分析"实际输出的是硬编码。
    准确性原则:抓不到就标「未验证」,绝不拿静态数据冒充实时爬取结果。
    """
    return {}


def _is_real_feature(text: str, min_len: int = 8) -> bool:
    """判断列表项是否真的是产品功能(过滤掉 footer 链接、语言切换等)。

    中文规则独立:len>=4 即可、动作词表换成中文业务词 —— 英文阈值(8)+
    英文词表曾把 Meetbot 全部中文功能灭掉("弃购转化神器" 6 字被杀)。
    min_len: 冒号前缀标题("Capture: …" → "Capture")是强结构信号,
    允许调用方放宽到 3 —— respond.io 事故:全站功能都是这种形态,7 字符
    前缀被英文阈值 8 全杀,15k 页面只提取 1 条。
    """
    t = text.strip()
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", t))
    # 太短或太长(中英文阈值不同)
    if has_cjk:
        if len(t) < 4 or len(t) > 40:
            return False
    elif len(t) < min_len or len(t) > 80:
        return False
    # 未剥净的 markdown 链接语法(乱码主因:"[Explorar](https://...)" 整条显示)
    if re.search(r"\[[^\]]*\]\(https?://", t):
        return False
    # cookie/GDPR 同意横幅菜单("Manage options"/"Manage {vendor\count} vendors" 事故)
    if re.match(r"^manage\s+(options|services|vendors|preferences|consent)", t, re.I):
        return False
    if re.search(r"\{[^}]*\\[a-z][^}]*\}", t):  # 未渲染 JS 模板变量 {vendor\count}
        return False
    if re.search(r"accept\s+(all|cookies)|cookie\s+preferences|do\s+not\s+sell|privacy\s+choices|opt-out", t, re.I):
        return False
    # 阿拉伯文/重音字母密集 → 多语言站翻译碎片,不是独立功能(استكشاف 事故)
    if re.search(r"[\u0600-\u06FF]", t):
        return False
    # 国旗 emoji + 电话区号(Sleekflow pricing 页国家列表:"🇦🇿 Azerbaijan +994")
    if re.search(r"[\U0001F1E6-\U0001F1FF]{2}|\+\d{2,4}\s*$", t):
        return False
    # 服务说明句子(动词开头/含 this/can be/onboarding 等句子性词,来自 pricing
    # 页的服务条款段:"This service can be availed by any…")
    if re.match(r"^(this|these|our|all|the)\b", t, re.I) or re.search(
        r"\b(can be availed|is included|are available|plan for all|applies to)\b", t, re.I
    ):
        return False
    # 套餐名融合残留("Premium AIPremium"/"Enterprise AIEnterprise" —— 引擎拼接事故:
    # 驼峰拆分后末词与前面重复,正常功能名不会这样)
    camel_words = re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", t)
    if len(camel_words) >= 3 and camel_words[-1] in camel_words[:-1]:
        return False
    # 纯 markdown 标题符号残留("## Growth")
    if t.lstrip().startswith("#"):
        return False
    if len(re.findall(r"[àâäéèêëïîôöùûüçñáíóú]", t, re.I)) > len(t) * 0.22:
        return False
    # 证言句子(引号/方括号开头:"Now, we manage WhatsApp..." 截断残留)
    if re.match(r'^["\u201c\u2018\[]', t):
        return False
    # UI 图标垃圾(图标 class 名混入:chevronright/chatbubble/apps 前缀,Tidio 事故)
    if re.search(r"chevron\s*right|chevronright|chatbubble|^apps?\s", t, re.I):
        return False
    # 语言/地区类(firecrawl 容易误把 footer 语言列表当 feature)
    lang_patterns = (
        "English", "Chinese", "Bahasa", "Español", "Spanish", "Português",
        "Français", "French", "Deutsch", "German", "日本語", "Korean",
        "हिन्दी", "Русский", "العربية", "Türkçe", "Tiếng Việt",
    )
    for p in lang_patterns:
        if p in t and len(t.split()) <= 3:
            return False
    # 纯菜单/footer 词
    footer_words = (
        "Privacy Policy", "Terms of Service", "Cookie Policy",
        "Sign In", "Sign Up", "Get Started", "Contact Sales",
        "Try Free", "Book Demo", "Watch Demo", "Learn More",
        "Read More", "See All", "View All",
    )
    for w in footer_words:
        if w.lower() in t.lower():
            return False
    # 营销标题 / 对比页标题 / 证言句子
    if re.search(r"\b(vs\.?|versus)\b", t, re.I):
        return False
    if re.search(
        r"(explore|discover|power of|trusted by|proven results|here's how"
        r"|learn how|see how|introducing|get to know|why choose)",
        t, re.I,
    ):
        return False
    if t.endswith((".", "!", "?")) or ". " in t:  # 是句子,不是功能名
        return False
    # API 文档端点(xxxget / xxxpost 是 method 徽章融合;查整行而非首词)
    if re.search(r"[a-z](get|post|put|delete|patch)$", t.strip(), re.I) or re.search(
        r"\b(get|post|put|delete|patch)\s*$", t.strip(), re.I
    ):
        return False
    # docs 页专有垃圾:webhook 事件名 / 端点文档标题 / 版本号标题
    if re.search(
        r"^(message|template|delivery|conversation|order)\s+"
        r"(received|sent|failed|delivered|read|status)",
        t, re.I,
    ) or re.search(r"\bis\s+(read|sent|delivered|failed)\b", t, re.I):
        return False
    # webhook 事件参数名(WATI docs 事故:"Chatbot Triggered"/"(BSUID)" 变体)
    if re.search(r"^chatbot\s+triggered", t, re.I):
        return False
    # 事件状态名结尾:"Template Message FAILED" / "Message Delivered"
    if re.search(r"\s(received|failed|delivered|read)$", t.strip(), re.I):
        return False
    if re.search(r"^(api|endpoint|openapi|swagger)\s+(documentation|reference|availability)", t, re.I):
        return False
    if re.search(r"webhook\s+events?\s*(&|and)?\s*(payloads?|types)", t, re.I):
        return False
    if re.search(r"\bv\d+\s*$|version\s+\d+", t, re.I):
        return False
    # 必须有"功能动词"或"技术词"(中文/英文两套词表)
    if has_cjk:
        cjk_words = (
            "转化", "分层", "掌控", "互动", "弃购", "营销", "客服", "群发",
            "自动化", "数据", "分析", "消息", "客户", "用户", "订单", "回复",
            "提醒", "通知", "模板", "渠道", "广播", "聊天", "机器人", "智能",
            "集成", "同步", "管理", "追踪", "推送", "发送", "触达", "增长",
            "成交", "复购", "召回", "唤醒", "挽留", "裂变", "私域", "引流",
            "跟单", "导购", "导出", "报表", "看板", "权限", "协作", "受理",
        )
        return any(w in t for w in cjk_words)
    action_words = (
        "集成", "API", "SDK", "Webhook", "AI", "自动化", "管理", "分析",
        "发送", "接收", "推送", "同步", "导出", "导入", "跟踪", "追踪",
        "Support", "Manage", "Analytics", "Integration", "Send",
        "Receive", "Track", "Sync", "Export", "Import", "Push",
        "Monitor", "Report", "Configure", "Workflow", "Bot", "Chat",
        "Launch", "Drive", "Engage", "Unlock", "Automate", "Create",
        "Grow", "Convert", "Maximize", "Campaign", "Inbox", "Journey",
        "Calling", "Shop", "Broadcast", "Routing", "Segment",
        "Payment", "Voip", "Email", "Calls", "Links",
        "Capture", "Retain", "Nurture", "Onboard", "Qualify",
        "Respond", "Selling", "Outreach", "Prospecting",
    )
    return any(w.lower() in t.lower() for w in action_words)


_CJK_FEATURE_TITLE_RX = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9+·&\-\s]{4,18}$")


def _extract_cjk_feature_cards(markdown: str, max_count: int = 15) -> List[str]:
    """CJK 功能卡模式:短标题行(4-18字,无标点结尾) + 紧随的描述行(10-80字)。

    中文营销站的功能卡常无列表/标题标记(MMeetbot 首页:
    "跨平台整合营销
    打通独立站数据,整合 Meta 渠道,形成闭环私域运营")。
    标题-描述对的结构信号比裸行可靠得多。
    """
    lines = [ln.strip() for ln in markdown.split("\n")]
    out = []
    for i, s in enumerate(lines):
        if not s or s in ("—", "-") or s.startswith(("#", "-", "*", "!", "[", "|", "http")):
            continue
        if not _CJK_FEATURE_TITLE_RX.match(s):
            continue
        if not re.search(r"[\u4e00-\u9fff]", s):
            continue  # 只对中文标题启用(英文裸行噪音太多)
        if s.endswith((".", "。", "!", "?", "！", ",", "，", ":", "：")):
            continue
        # 下一行必须是描述(更长的中文句) —— 结构信号
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        if not (10 <= len(nxt) <= 90 and re.search(r"[\u4e00-\u9fff]", nxt) and nxt.endswith(("。", ".", "，", ",", "！", "!", ""))):
            continue
        if not _is_real_feature(s):
            continue
        out.append(s)
        if len(out) >= max_count:
            break
    return out


def _extract_features(markdown: str, max_count: int = 40) -> List[str]:
    """启发式提取功能列表(过滤掉 footer/菜单/语言切换等噪音)。"""
    features = []
    seen = set()

    # 0. CJK 功能卡(短标题+描述对;中文站主流形态)。用原始 markdown ——
    # _clean_markdown 的 len<15 短行规则会先杀掉 6 字中文标题(Meetbot 事故)
    for text in _extract_cjk_feature_cards(markdown, max_count=max_count):
        if text not in seen:
            seen.add(text)
            features.append(text)
        if len(features) >= max_count:
            return features

    md = _clean_markdown(markdown)

    # 1. 列表项 (- xxx, * xxx)
    for m in re.finditer(r"^\s*[-*]\s+([^\n]{4,100})", md, re.MULTILINE):
        text = re.sub(r"[*_`]", "", m.group(1)).strip()
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # 去链接
        if (
            text
            and text not in seen
            and _is_real_feature(text)
            and len(text.split()) <= 14
        ):
            seen.add(text)
            features.append(text)
            if len(features) >= max_count:
                break

    # 2. 加粗短语 (**xxx**) —— 同样过噪音过滤(营销口号不是功能)
    if len(features) < max_count:
        for m in re.finditer(r"\*\*([^*\n]{4,60})\*\*", md):
            text = re.sub(r"[*_`]", "", m.group(1)).strip()
            skip_words = (
                "Solutions",
                "Resources",
                "Industries",
                "Login",
                "Sign up",
                "Get Started",
                "Learn More",
                "Read More",
                "Contact Us",
            )
            if (
                text
                and text not in seen
                and 3 < len(text) < 60
                and _is_real_feature(text)
                and not _is_noise_line(text)
                and not any(s in text for s in skip_words)
            ):
                seen.add(text)
                features.append(text)
                if len(features) >= max_count:
                    break

    # 3. 标题(任意层级,当标题短且像功能名;营销 slogan 不配)
    if len(features) < max_count:
        _SLOGAN_RX = re.compile(
            r"&|\bwith\b|\byour\b|\bour\b|journey|complete|start\s|get\s+started"
            r"|trusted|empower|unlock\s+your|future|一起|之路|开启"
            r"|\b(efficiently|quickly|securely|effortlessly|seamlessly)\b"
            r"|you\s+can|^designed\s+for|^built\s+for|^made\s+for",
            re.I,
        )
        for m in re.finditer(r"^#{1,6}\s+([^\n]{3,60})$", md, re.MULTILINE):
            text = re.sub(r"[*_`]", "", m.group(1)).strip()
            is_colon_prefix = False
            skip_words_h2 = ("Solutions", "Resources", "Industries", "Login", "Sign up",
                             "Get Started", "Learn More", "Read More", "Contact Us",
                             "Products", "Pricing", "Docs", "Blog", "About")
            # 冒号前缀形态:"Capture: Unify customer touch points to drive revenue"
            # → 功能名取前缀(respond.io 事故:整段 54 字符被 len<40 全杀)
            colon_m = re.match(r"^([A-Za-z][\w\s&-]{2,18}):(\s+.+)$", text)
            if colon_m and not text.endswith("?"):
                text = colon_m.group(1).strip()
                is_colon_prefix = True
            if len(text) >= 40:
                continue
            if (
                text
                and text not in seen
                and 3 < len(text) < 40
                and _is_real_feature(text, min_len=3 if is_colon_prefix else 8)
                and (is_colon_prefix or not _is_noise_line(text))
                and not _SLOGAN_RX.search(text)
                and not any(s in text.lower() for s in skip_words_h2)
                and not text.endswith("?")
            ):
                seen.add(text)
                features.append(text)
                if len(features) >= max_count:
                    break

    # 4. 链接融合格式 [名称+描述](/slug) —— 营销站 nav 下拉功能列表的通用形态
    #   名称切分:驼峰边界中,余文以"描述动词/冠词"开头的那一处(描述句的起点),
    #   避免 "WhatsApp" 自身内部驼峰误切;切不出再用 URL slug 恢复
    if len(features) < max_count:
        _ACRONYMS = {"api", "ai", "crm", "sdk", "ctwa", "sms", "csp", "rbac"}
        _CAMEL_SPLIT = re.compile(
            r"(?<=[a-z])(?=[A-Z])"          # 小写|大写边界: Campaign|Drive
            r"|(?<=[A-Z]{2})(?=[A-Z][a-z])"  # 缩略词|TitleCase: API|Launch, CTWA|Maximize
        )
        _DESC_START = re.compile(
            r"^(?:Launch|Drive|Engage|Maximize|Unlock|Automate|Create|Connect"
            r"|Convert|Get|Boost|Build|Track|Manage|Send|Reach|Grow|Scale"
            r"|Personalize|Deliver|Streamline|Turn|Make|Help|Simplify|Power"
            r"|Empower|Enable|Support|Provide|Offer|Allow|Sync|Route|Monitor"
            r"|Analyze|Converse|Generate|Capture|Collect|Segment|Schedule"
            r"|Trigger|Chat|A |An |The |自动|管理|发送|一键)",
            re.I,
        )
        for m in re.finditer(
            r"\[([^\]\[]{14,130})\]\((?:https?://[^)]*?|)(/[a-z0-9][a-z0-9-]*)\)",
            md,
        ):
            text = re.sub(r"[*_`]", "", m.group(1)).strip()
            slug = m.group(2).rstrip("/").split("/")[-1]
            words = slug.split("-")
            # 纯导航页(pricing/docs/blog/contact)不配当功能
            nav_slugs = {
                "pricing", "docs", "blog", "contact", "about", "login",
                "sign-up", "signup", "careers", "partners", "demo", "home",
                "features", "solutions", "resources", "support",
            }
            if slug in nav_slugs or words[0] in ("blog", "docs", "help"):
                continue
            # 找"描述句起点"的边界:余文以动词/冠词开头
            name = ""
            for mm in _CAMEL_SPLIT.finditer(text):
                cand = text[: mm.start()].strip()
                if 3 <= len(cand) <= 60 and _DESC_START.match(text[mm.start():]):
                    name = cand
                    break
            if not name and len(words) >= 2:
                name = " ".join(
                    w.upper() if w in _ACRONYMS else w.capitalize() for w in words
                )
            if not name or len(name) > 60 or name in seen:
                continue
            # 名称 + 描述前缀 合起来过功能词过滤(截 60,避免超长被拒)
            if len(text) < 25 or not _is_real_feature(f"{name} {text[:60]}"):
                continue
            seen.add(name)
            features.append(name)
            if len(features) >= max_count:
                break

    return features


def _classify_feature(text: str) -> str:
    """启发式分类。"""
    if any(k in text for k in ["AI", "智能", "自动化", "Lyro", "GPT"]):
        return "AI 客服"
    if any(
        k in text for k in ["WhatsApp", "渠道", "邮件", "SMS", "Instagram", "Messenger"]
    ):
        return "消息渠道"
    if any(k in text for k in ["收件箱", "Inbox", "团队", "坐席", "工单"]):
        return "收件箱"
    if any(k in text for k in ["API", "Webhook", "SDK", "开发", "集成"]):
        return "开发者"
    if any(k in text for k in ["分析", "统计", "报告", "数据", "KPI", "仪表盘"]):
        return "分析"
    if any(k in text for k in ["营销", "广播", "群发", "活动", "Campaign"]):
        return "营销"
    if any(k in text for k in ["客户档案", "标签", "用户画像"]):
        return "客户数据"
    return "其他"




def _extract_pricing_tier_features(markdown: str, max_count: int = 18) -> List[str]:
    """定价页专用:套餐卡内的功能清单常是无标记的独立短行。

    respond.io 形态:计划名/价格/CTA 之间散布 "Data Export"/"Roles & Teams"
    等独立行 —— 无 bullet/加粗/标题标记,通用提取全漏(矩阵 ? 的主因之一)。
    过滤:首词大写 + 过 _is_real_feature(动作/技术词) + 非套餐名/CTA/周期。
    """
    if not markdown:
        return []
    _NAV_WORDS = {
        "product", "products", "solutions", "features", "pricing", "customers",
        "company", "blog", "docs", "login", "log in", "contact", "about", "home",
        "resources", "industries", "platform", "overview", "why us",
        "help center", "video guides", "success stories", "careers", "press",
        "partners", "affiliate", "whitepaper", "webinar", "ebook", "glossary",
        "api docs", "changelog", "status", "roadmap", "community", "support",
    }
    out, seen = [], set()
    for raw in markdown.split("\n"):
        t = raw.strip().strip("*_`#> ").strip()
        # markdown 链接融合碎片("Route Leads](/lead-distribution)" —— 嵌套
        # 图片链接的残骸):剥出纯文本部分;剥不干净(含 ]( 或 /) 的丢弃
        if "](" in t:
            m2 = re.match(r"^([A-Za-z][\w\s&+/-]{2,30})\]\(", t)
            if not m2:
                continue
            t = m2.group(1).strip()
        if not (3 <= len(t) <= 40) or len(t.split()) > 5:
            continue
        # 首字符必须大写字母(Title Case 能力名形态)
        if not t[0].isupper():
            continue
        # 排除:套餐名 / CTA / 周期 / "Most Popular" 类徽标 / 纯数字
        if _PLAN_NAME_RX.fullmatch(t) or _PRICE_TOKEN_RX.search(t):
            continue
        if _PERIOD_STRICT_RX.search(t) or _PRICE_CTA_LINE_RX.match(t):
            continue
        if t.lower() in (
            "most popular", "best value", "popular", "free", "custom",
            "select plan", "choose plan", "get started", "contact sales",
            "book a demo", "try free", "try for free", "sign up", "log in",
        ):
            continue
        # cookie/GDPR 按钮融合("CancelSave My Preferences")
        if re.search(r"cancel\s*save|save my preferences|accept\s*all|manage\s*cookies?", t, re.I):
            continue
        words = t.split()
        if t.lower() in _NAV_WORDS or (
            len(words) == 1 and not _is_real_feature(t)
        ):
            continue  # 单词必须是已知能力词(挡导航项);多词名词短语放行
        # 分隔/递进句("Everything in Growth, plus:")与冒号结尾行不是功能
        if t.endswith(":") or re.search(r"everything in\b", t, re.I):
            continue
        # 融合垃圾(引擎拼接的 "Premium AIPremium")即使 Title Case 也拒
        _camel = re.findall(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+", t)
        if len(_camel) >= 3 and _camel[-1] in _camel[:-1]:
            continue
        if not _is_real_feature(t) and not (
            len(words) >= 2 and all(w[0].isupper() or w in ("&", "and", "of", "for") for w in words)
        ):
            continue
        key = t.lower()
        if key not in seen:
            seen.add(key)
            out.append(t)
        if len(out) >= max_count:
            break
    return out



def _extract_features_from_page(markdown: str) -> List[Dict]:
    raw = _extract_features(markdown, max_count=40)
    feats = []
    for f in raw:
        feats.append(
            {
                "category": _classify_feature(f),
                "name": f[:60],
                "desc": "",
            }
        )
    return feats


def _enrich_features_with_source(features: List[Dict], fallback_source: str) -> List[Dict]:
    """给每个 feature 加 source URL (normalize 时会分配 _ref)。

    即使 firecrawl 没抓到具体 feature URL, 也可作为该 feature
    在该厂商官方页可见的证据。
    """
    enriched = []
    for f in features:
        if "source" not in f:
            f["source"] = fallback_source
        enriched.append(f)
    return enriched


def features_str_for_diff(name: str, feat_md: str) -> str:
    return feat_md or ""


def _derive_target_users(c) -> list:
    """根据 stage + features 反推目标用户。"""
    name = c.get("name", "")
    fc = c.get("feature_catalog", {}).get(name, [])
    f_str = " ".join(f["name"] for f in fc)

    users = []
    if "Shopify" in f_str or "电商" in f_str:
        users.append("跨境电商")
    if "营销" in f_str or "Campaign" in f_str or "广告" in f_str:
        users.append("营销团队")
    if "AI" in f_str:
        users.append("客服中心")
    if "API" in f_str or "Webhook" in f_str or "SDK" in f_str:
        users.append("开发者")
    if not users:
        users.append("中小企业")
    return users[:3]


def _derive_core_features(c) -> list:
    """基于 features 提取最核心的 3-6 个功能名。"""
    name = c.get("name", "")
    fc = c.get("feature_catalog", {}).get(name, [])
    # 选最前 5 个
    return [f["name"] for f in fc[:5]]


def _derive_differentiators(name: str, pricing: str, feat_md: str) -> list:
    """differentiators 已改为 LLM 在 Step 3 基于证据填写。

    历史教训:这里曾按关键词模板生成("检测到 AI → 'AI 驱动自动化'")并附
    g2.com/postman.com 等从未访问过的假 source —— 伪造证据。脚本层不再
    生成任何 differentiators。
    """
    return []


# docs 页可验证的技术能力词 → 中文名。逐词在 docs markdown 里确认存在才输出
# (证据 = docs 页 URL 本身;与历史"关键词模板伪造"的区别:内容真实可点开核对)
_TECH_SIGNAL_VOCAB = [
    (r"webhook", "Webhooks(事件推送)"),
    (r"\bREST\b|/v1/|api\s+(?:reference|endpoint)", "REST API"),
    (r"\bSDK\b|software development kit", "官方 SDK"),
    (r"oauth|single sign[- ]on|\bSSO\b", "OAuth / SSO 登录"),
    (r"graphql", "GraphQL API"),
    (r"\bAI\b|\bLLM\b|gpt|chatbot|agent", "AI / LLM 能力"),
    (r"iso\s*27001|soc\s*2|gdpr", "合规认证(ISO/SOC2/GDPR)"),
    (r"zapier|make\.com|workflow\s+automation", "自动化集成(Zapier 等)"),
    (r"shopify|woocommerce|salesforce|hubspot", "电商/CRM 集成"),
    (r"\bdocker\b|\bkubernetes\b|self[- ]host", "自托管/容器化部署"),
]


def _derive_tech_signals(docs_md: str, docs_url: str) -> list:
    """从 docs 页原文提取可验证的技术信号(每条附 docs 页 source URL)。

    与历史"关键词模板伪造"的本质区别:只在 docs markdown 里逐词真实
    命中才输出,source = 实际爬取的 docs URL,读者可点开核对。
    docs 页缺失时返回 [](不猜)。
    """
    if not docs_md or _looks_like_only_js_or_404(docs_md):
        return []
    low = docs_md.lower()
    out, seen = [], set()
    for pat, label in _TECH_SIGNAL_VOCAB:
        if re.search(pat, low, re.I) and label not in seen:
            seen.add(label)
            out.append({"name": label, "source": docs_url})
    return out[:6]


# ── 智能页面发现:从首页导航链接找真实 URL,不信任猜测路径 ──
# (YCloud 事故:resolver 猜的 /features 是 404,功能提取直接归零)

_DISCOVER_PATTERNS = {
    "pricing": re.compile(r"pricing|price|plans?|定价|价格|套餐", re.I),
    "features": re.compile(
        r"features?|functionalit|capabilities|platform|product|产品|功能", re.I
    ),
    "about": re.compile(r"^about|about[-\s]us|company|our[-\s]story|team$|关于|公司", re.I),
    "docs": re.compile(r"^docs?$|documentation|developers?|api[-\s]docs", re.I),
}


def _discover_urls(home_md: str, base_url: str) -> Dict[str, str]:
    """从首页 markdown 的链接里发现 pricing/features/about/docs 真实 URL。

    只信导航语义(链接文本优先,URL slug 次之),同域绝对/相对链接都处理。
    返回 {kind: url};没发现的 kind 不在返回里。
    """
    from urllib.parse import urljoin

    found: Dict[str, str] = {}
    for m in re.finditer(r"\[([^\]\[]{2,25})\]\(([^)#\s]+)\)", home_md or ""):
        text, href = m.group(1).strip(), m.group(2).strip()
        if not href or href.startswith(("mailto:", "tel:", "javascript:")):
            continue
        full = urljoin(base_url.rstrip("/") + "/", href)
        # 只认同域 http(s) 链接
        if not full.startswith("http"):
            continue
        try:
            from urllib.parse import urlparse
            if urlparse(full).netloc.replace("www.", "") != urlparse(base_url).netloc.replace("www.", ""):
                continue
        except Exception:
            continue
        for kind, pat in _DISCOVER_PATTERNS.items():
            if kind in found:
                continue
            if pat.search(text) or pat.search(full.replace(base_url.rstrip("/"), "", 1).split("?")[0]):
                found[kind] = full.split("?")[0].split("#")[0]
    return found


def _extract_site_title(scrape_result: Dict) -> Dict[str, str]:
    """从各引擎原文提取站点 <title> / meta description。

    来源优先级:trafilatura YAML frontmatter(title:/description:)>
    jina 的 "Title: ..." 行。站点 title 是官方一句话定位,比从被
    cookie 横幅/多语言 promo 污染的正文里猜 H1 可靠得多
    (WATI 事故:正文全是横幅,真 tagline 埋在导航垃圾下)。
    """
    title, desc = "", ""
    for r in (scrape_result.get("all_results") or []):
        if not (r.get("success") and r.get("markdown")):
            continue
        md = r["markdown"][:1200]
        eng = r.get("scraper", "")
        if eng == "trafilatura":
            tm = re.search(r'^title:\s*["\']?(.+?)["\']?\s*$', md, re.M)
            dm = re.search(r'^description:\s*["\']?(.+?)["\']?\s*$', md, re.M)
            if tm and not title:
                title = tm.group(1).strip()
            if dm and not desc:
                desc = dm.group(1).strip()
        elif eng == "jina" and not title:
            tm = re.search(r"^Title:\s*(.+)$", md, re.M)
            if tm:
                title = tm.group(1).strip()
        if title and desc:
            break
    return {"title": title[:120], "description": desc[:200]}


def _scrape_one(resolved: Dict, timeout: int = 30, max_chars: int = 25000) -> Dict:
    """抓单个竞品:home 先行 → 从导航发现真实页面 URL → 再爬其余模块。

    页面顺序与 URL 解析优先级:
      1. home(必爬,tagline/功能兜底来源 + URL 发现输入)
      2. pricing/features/about/docs:首页导航发现的 URL 优先于 resolver
         猜测路径 —— 猜测路径 404 时(YCloud /features 事故)自动换用发现
         的 URL 重爬;两者都无则该模块标空(后续字段显示"未获取")
    about 页供 founded/headquarters/team_size 提取(此前根本不爬,全空)。
    """
    result = {
        "name": resolved["canonical_name"],
        "url": resolved["url"],
        "pricing_source": resolved.get("pricing_url") or "",
        "tagline_source": resolved["url"],
        "founded_source": "",
        "team_size_source": "",
        "headquarters_source": "",
        "raw_markdown": {},
        "page_urls": {"home": resolved["url"]},
        # F8 证据包:本轮抓取记录 + 各引擎原文 + 失败清单
        # (verify.py G1/G2/G3 的消费对象,落盘到 claims-manifest.json)
        "_manifest": {"fetched": {}, "engines_by_url": {}, "failures": []},
    }

    def _crawl_page(kind: str, url: str) -> str:
        """爬单个页面,返回 markdown(JS-only 时 playwright 兜底)。"""
        try:
            t0 = time.time()
            # 定价页给更大配额:价格段常在长页面深处,WATI 事故 = 15k 截断
            # 把全部真实套餐价切掉,只剩页首营销句
            # 定价页 3x;首页/功能页 2x(功能区块在页面深处,截断即丢);
            # about/docs 维持 1x
            page_max = max_chars * (3 if kind == "pricing" else 2 if kind in ("home", "features") else 1)
            r = scrape_smart(url, max_chars=page_max, timeout=timeout)
            dt = time.time() - t0
            md = r.get("markdown", "") if r.get("success") else ""
            # F8 证据包:本轮抓取记录(状态 + 各引擎指纹)+ 引擎原文
            m_ent = {
                "status": "ok" if (r.get("success") and r.get("markdown")) else "failed",
                "engines": {},
                "fetched_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
            }
            for x in (r.get("all_results") or []):
                if x.get("scraper") and x.get("success") and x.get("markdown"):
                    m_ent["engines"][x["scraper"]] = {
                        "ok": True, "chars": len(x["markdown"]),
                        "content_hash": _content_hash(x["markdown"]),
                    }
                    # 截断上限 = 该页抓取配额(定价页 3x):硬编码 50000 会切掉
                    # 长定价页尾部的价格段,G2 回查时 quote 在存储副本里找不到
                    result["_manifest"]["engines_by_url"].setdefault(
                        url, {}
                    )[x["scraper"]] = x["markdown"][:page_max]
            result["_manifest"]["fetched"][url] = m_ent
            if m_ent["status"] == "failed":
                # G4:非异常失败(全部引擎空/JS-only)也必须留痕
                result["_manifest"]["failures"].append({
                    "competitor": resolved["canonical_name"],
                    "url": url, "kind": kind.replace("*", "").rstrip("?"),
                    "error": "all engines failed/empty",
                })
            if kind == "pricing":
                result["pricing_evidence"] = _extract_pricing_evidence(r, url)
                # 各引擎的定价页原文都保留 —— 套餐功能清单常只在某一个
                # 引擎的变体里完整(单引擎劣化时功能全丢),功能提取取并集
                result["pricing_all_markdowns"] = [
                    x["markdown"]
                    for x in (r.get("all_results") or [])
                    if x.get("success") and x.get("markdown")
                ]
            # 定价页 0 价格行 = 页面返回了反爬/区域变体(WATI 真实事故:
            # 某轮全部引擎拿到无价变体 → 回退断言「价格未公开」= 把
            # "没抓到"当"没公示"输出)→ 触发 playwright 重试
            _pricing_starved = (
                kind == "pricing"
                and not (result.get("pricing_evidence") or {}).get("vote_detail")
            )
            # firecrawl 等跑回 JS-only 时用 playwright 单独兜底(SPA 专用)
            if _looks_like_only_js_or_404(md) or _pricing_starved:
                pr = {}
                for _attempt in range(2):
                    try:
                        from adapters import playwright_scraper
                        pr = playwright_scraper.scrape(
                            url, wait_selector=None, screenshot_path=None,
                            timeout=timeout * 1000,
                        )
                        if pr.get("success") and pr.get("markdown"):
                            break
                    except Exception as _e:
                        print(f"    [{resolved['canonical_name']}] {kind} rescue#{_attempt} FAIL: {_e}")
                try:
                    if pr.get("success") and pr.get("markdown"):
                        pm = pr["markdown"][:page_max]
                        if (
                            not _looks_like_only_js_or_404(pm)
                            and (len(pm) > len(md) or _pricing_starved)
                        ):
                            md = pm
                            if kind == "pricing":
                                # rescue 成功 → 重算定价证据(历史缺陷:证据在
                                # rescue 之前算;starved 重试同样要重算)
                                merged = dict(r)
                                merged["all_results"] = [
                                    x for x in (r.get("all_results") or [])
                                    if x.get("scraper") != "playwright"
                                ] + [
                                    {"scraper": "playwright", "success": True, "markdown": pm}
                                ]
                                new_ev = _extract_pricing_evidence(merged, url)
                                # 只有拿到更多证据才覆盖(starved 重试失败时保留原结果)
                                if len(new_ev.get("vote_detail") or []) >= len(
                                    (result.get("pricing_evidence") or {}).get("vote_detail") or []
                                ):
                                    result["pricing_evidence"] = new_ev
                            # F8:rescue 的 playwright 原文也进证据包 ——
                            # vote 行可能出自 rescue 引擎,G2 要能回查
                            result["_manifest"]["fetched"].setdefault(url, {}).setdefault(
                                "engines", {}
                            )["playwright"] = {
                                "ok": True, "chars": len(pm),
                                "content_hash": _content_hash(pm),
                            }
                            result["_manifest"]["engines_by_url"].setdefault(
                                url, {}
                            )["playwright"] = pm[:page_max]
                except Exception:
                    pass
            # 定价来源只在真正拿到内容时记录(历史缺陷:抓取失败也写 source,
            # 读者点进去是一个 404,反而质疑其他真来源)
            if kind == "pricing" and md and not _looks_like_only_js_or_404(md):
                result["pricing_source"] = url
            stats = r.get("stats") or {}
            n_succ = stats.get("successful", 0)
            n_total = stats.get("total_scrapers", 0)
            print(
                f"    [{resolved['canonical_name']}] {kind:8s} "
                f"{len(md):>5d} chars ({dt:.1f}s, {n_succ}/{n_total} 爬虫成功)"
            )
            # home 页:从各引擎原文提取 <title>/meta description(tagline 优质源)
            if kind == "home":
                t = _extract_site_title(r)
                if t.get("title"):
                    result["site_title"] = t["title"]
                if t.get("description"):
                    result["site_meta_description"] = t["description"]
            return md
        except Exception as e:
            print(f"    [{resolved['canonical_name']}] {kind:8s} FAIL: {e}")
            # F8:失败必须留痕(verify G4:静默吞掉的失败 = 报告缺口无解释)
            result["_manifest"]["fetched"][url] = {
                "status": "failed", "engines": {},
                "fetched_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
            }
            result["_manifest"]["failures"].append({
                "competitor": resolved["canonical_name"],
                "url": url, "kind": kind.replace("*", "").rstrip("?"),
                "error": f"{type(e).__name__}: {e}",
            })
            return ""

    # 1) home 先爬
    home_md = _crawl_page("home", resolved["url"])
    result["raw_markdown"]["home"] = home_md

    # 2) 从首页导航发现真实页面 URL
    discovered = _discover_urls(home_md, resolved["url"])
    for kind, url in discovered.items():
        result["page_urls"][kind] = url

    # 3) 各模块:resolver 猜测 URL → 404 则用发现的 URL 重试
    guess_urls = {
        "pricing": resolved.get("pricing_url"),
        "features": resolved.get("features_url"),
        "about": resolved.get("about_url"),
        "docs": resolved.get("docs_url"),
    }
    # about 页补猜(内容感知):/about 常是首页重定向(WATI 事故:/about
    # 25017 字符但无公司信息,真正的 /about-us/ 因"先到先得"被跳过)
    # → 逐个试,按公司信息信号密度(founded/HQ/team 词)取最高分
    about_guesses = [
        resolved["url"].rstrip("/") + p
        for p in ("/about", "/about-us", "/company")
    ]

    def _about_score(md_text: str) -> int:
        if not md_text:
            return -1
        return sum(
            len(re.findall(pat, md_text))
            for pat in (
                r"founded|established|launched|成立于|创立于",
                r"headquartered|based in|总部|位于",
                r"employees|team members|team of|员工|团队规模",
                r"our story|我们的故事|about us",
            )
        )

    for kind in ("pricing", "features", "about", "docs"):
        guess = guess_urls.get(kind)
        if kind == "about" and not guess:
            best_md, best_url, best_score = "", "", -1
            for au in about_guesses:
                md_a = _crawl_page("about?", au)
                if not md_a or _looks_like_only_js_or_404(md_a):
                    continue
                sc = _about_score(md_a)
                if sc > best_score:
                    best_md, best_url, best_score = md_a, au, sc
            if best_md:
                result["raw_markdown"]["about"] = best_md
                result["page_urls"]["about"] = best_url
            continue
        disc = discovered.get(kind)
        # resolver 显式 URL 先爬,失败(404/JS/空)再试导航发现的 URL,
        # features 再失败试常见路径字典 —— 三层 fallback(Sleekflow 事故:
        # /features 404 且导航无发现时功能直接归零,而 /product 等价页存在)
        candidates = []
        if guess:
            candidates.append(guess)
        if disc and disc not in candidates and disc != (guess or "").split("?")[0]:
            candidates.append(disc)
        if kind == "features" and len(candidates) < 2:
            base = resolved["url"].rstrip("/")
            for extra in ("/platform", "/product", "/solutions", "/capabilities"):
                u2 = base + extra
                if u2 not in candidates:
                    candidates.append(u2)
        md = ""
        for ci, cand in enumerate(candidates):
            suffix = "" if ci == 0 else "*"
            md2 = _crawl_page(kind + suffix, cand)
            if len(md2) > len(md):
                md = md2
                result["page_urls"][kind] = cand
            if md and not _looks_like_only_js_or_404(md):
                break
        # F2:定价全失败时不再回填猜测 URL —— 未抓到的页面不能当来源
        # (历史缺陷:guess/disc 404 时 pricing_source 指向死链)
        result["raw_markdown"][kind] = md

    # about 页来源标记(founded/hq/team 提取证据)
    about_url = result["page_urls"].get("about") or ""
    result["founded_source"] = about_url or resolved["url"]
    result["headquarters_source"] = about_url or resolved["url"]
    result["team_size_source"] = about_url or resolved["url"]

    return result


# 币种符号 → 匹配正则 + ISO 代码。US$ 必须先于 $ 检查;S$ 用负向后行断言
# 避免 "US$149" 子串误中(真实事故:Sleekflow 全部 US$ 价被标 SGD)
_CURRENCY_PATTERNS = [
    ("USD", re.compile(r"(?:US\$|USD\s*\$?|(?<![A-Za-z])\$)\s?\d", re.I)),
    ("SGD", re.compile(r"(?<![A-Za-z])S\$\s?\d")),
    ("HKD", re.compile(r"(?:HK\$|HKD\s*\$?)\s?\d", re.I)),
    ("TWD", re.compile(r"(?:NT\$|TWD)\s?\d", re.I)),
    ("CAD", re.compile(r"(?:CA\$|CAD)\s?\d", re.I)),
    ("AUD", re.compile(r"(?<![A-Za-z])A\$\s?\d")),
    ("INR", re.compile(r"(?:₹|Rs\.?\s)\s?\d", re.I)),
    ("EUR", re.compile(r"€\s?\d")),
    ("GBP", re.compile(r"£\s?\d")),
    ("CNY", re.compile(r"[¥￥]\s?\d")),
    ("JPY", re.compile(r"[¥￥]\s?\d")),
]


def _detect_currency(pricing: str, price_tokens=None) -> str:
    """从定价串/tier 价格 token 推断主导币种。

    优先用 tiers 里被选中的价格 token(它们是已过滤的干净套餐价);
    多币种页面(WATI $ 主套餐 + ₹ 印度充值)按主导符号计数,
    而非固定顺序首次命中(历史缺陷:WATI 页 ₹ 充值 promo 出现 4 次
    就把 $59/$119 主套餐标成 INR)。
    ¥ 的歧义:中文语境 CNY,否则 JPY。
    """
    texts = list(price_tokens or []) + [pricing or ""]
    counts: Dict[str, int] = {}
    for t in texts:
        for cur, rx in _CURRENCY_PATTERNS:
            counts[cur] = counts.get(cur, 0) + len(rx.findall(t))
    # $ (USD) 的匹配已含 US$,把纯 $ 计数修正为 USD 总数
    cny_jpy = counts.pop("CNY", 0) + counts.pop("JPY", 0)
    han = any(re.search(r"[\u4e00-\u9fff]", t) for t in texts)
    counts["CNY" if han else "JPY"] = cny_jpy
    if not counts or max(counts.values(), default=0) == 0:
        return "USD"
    # 平票时按 USD > 其他(国际 SaaS 默认)
    best = max(counts.items(), key=lambda kv: (kv[1], kv[0] == "USD"))
    return best[0]


# ─────────────────────────────────────────────────────────
# GTM / 护城河:基于 home+about 原文逐词命中的证据推导(带 quote+source)
# 与历史"关键词模板伪造"的区别:每条结论附原文引文,可点开核对
# ─────────────────────────────────────────────────────────

_GTM_EVIDENCE_PATTERNS = [
    # (正则, 证据句) —— CTA 形态揭示获客模式
    (r"start\s+free\s+trial|try\s+for\s+free|免费试用", "self_trial",
     "官网提供自助免费试用入口"),
    (r"book\s+a\s+demo|request\s+a\s+demo|预约演示|预约 Demo", "sales_demo",
     "官网主打预约演示(销售驱动获客)"),
    (r"contact\s+sales|联系销售|获取报价|get\s+a\s+quote", "sales_quote",
     "官网引导联系销售获取报价"),
    (r"get\s+started\s+free|sign\s+up\s+free|免费注册", "self_signup",
     "官网提供免费注册自助开通"),
    (r"partner|channel\s+partner|reseller|代理商|渠道", "channel",
     "官网展示合作伙伴/渠道体系"),
    (r"\bAPI-first\b|developer|开发者", "dev_first",
     "官网面向开发者/API 优先定位"),
]

_MOAT_EVIDENCE_PATTERNS = [
    # (正则, 证据句) —— 客观资产:客户数/认证/官方身份/融资/规模
    (r"\d[\d,.+]*\s*(?:k\+?|m\+?|\+)?\s*(?:teams|businesses|customers|companies|brands|merchants|users|企业|客户|家)", "customers", None),
    (r"(soc\s*2|iso\s*27001|gdpr|hipaa|合格认证|数据安全认证)", "compliance", "持有国际合规认证(SOC2/ISO27001/GDPR)"),
    (r"(official\s+(?:business|solution|tech)\s+partner|meta\s+partner|bsp\b|官方(合作伙伴|服务商)|官方授权)", "official_partner", "Meta 官方合作伙伴/BSP 身份"),
    (r"(series\s+[a-e]\b|raised|funding|融资|轮)", "funding", "已获风险融资(详见融资料)"),
    (r"(\d[\d,.+]*\s*(?:employees|people|团队|员工|人))", "team", None),
    (r"(patent|专利)", "patent", "持有专利"),
]


def _find_evidence_lines(md: str, pattern: str, max_hits: int = 2):
    """在 markdown 里找命中行,返回 [(quote, ...)] —— quote 是原文行。"""
    if not md:
        return []
    out = []
    for raw in md.split("\n"):
        t = raw.strip().strip("*_`#> ")
        if 8 <= len(t) <= 160 and re.search(pattern, t, re.I):
            out.append(t[:120])
            if len(out) >= max_hits:
                break
    return out


def _derive_gtm_evidence(home_md: str, about_md: str, home_url: str, about_url: str) -> list:
    """从官网 CTA/定位文本推导 GTM 模式,每条带 quote+source。"""
    out, seen = [], set()
    for pat, key, label in _GTM_EVIDENCE_PATTERNS:
        if key in seen:
            continue
        for md, url in ((home_md, home_url), (about_md, about_url)):
            quotes = _find_evidence_lines(md, pat, 1)
            if quotes:
                seen.add(key)
                out.append({
                    "name": label,
                    "quote": quotes[0],
                    "source": url,
                })
                break
    return out[:3]


def _derive_moat_evidence(home_md: str, about_md: str, pricing_md: str,
                          home_url: str, about_url: str, pricing_url: str) -> list:
    """从官网客观资产(客户数/认证/官方身份)推导护城河,每条带 quote+source。"""
    out, seen = [], set()
    sources = (
        (about_md, about_url),
        (home_md, home_url),
        (pricing_md, pricing_url),
    )
    for pat, key, label in _MOAT_EVIDENCE_PATTERNS:
        if key in seen:
            continue
        for md, url in sources:
            quotes = _find_evidence_lines(md, pat, 1)
            if quotes:
                seen.add(key)
                text = re.sub(r"\s+", " ", quotes[0])
                if label is None:
                    # 客户数/团队规模:label 用通用句式,具体数字在 quote 里
                    if re.search(r"employees|people|团队|员工", text, re.I):
                        lab = "公开团队规模(见引文)"
                    else:
                        lab = "公开客户规模(见引文)"
                else:
                    lab = label
                out.append({"name": lab, "quote": text, "source": url})
                break
    return out[:3]


def _build_competitor_entry(scraped: Dict, idx: int = 0) -> Tuple[Dict, List[str], List[Dict]]:
    """从 scrape 结果构建 13 字段 competitor entry。

    Returns:
        (entry, warnings, claims): 竞品数据 + 爬取质量警告 + 可验证断言清单
        (claims 是 verify.py G1/G2 的消费对象,写入 claims-manifest.json)
    """
    name = scraped["name"]
    home_md = scraped["raw_markdown"].get("home", "")
    feat_md = scraped["raw_markdown"].get("features", "")
    pricing_md = scraped["raw_markdown"].get("pricing", "")
    docs_md = scraped["raw_markdown"].get("docs", "")
    about_md = scraped["raw_markdown"].get("about", "")
    # 功能来源 = features 页 + home 页 + pricing 页(套餐对比表逐档列功能,
    # 是最密的功能清单 —— 历史缺陷:pricing 页只用于价格,功能白白浪费;
    # 3/5 家 features 页 404 时 feature_catalog 薄到 canonical 矩阵全是 ?)
    # docs 是 API 文档,webhook 事件名/端点表会被误当功能,仅兜底补充
    features_base_md = "\n".join(m for m in (feat_md, home_md) if m)
    page_features = _extract_features_from_page(features_base_md)
    # pricing 页单独通道:套餐卡功能清单是独立短行形态,通用提取吃不到
    pricing_feature_pool = [pricing_md] + list(
        scraped.get("pricing_all_markdowns") or []
    )
    pricing_features = []
    _pf_seen = set()
    for _pmd in pricing_feature_pool:
        for _pf in _extract_pricing_tier_features(_pmd):
            if _pf.lower() not in _pf_seen:
                _pf_seen.add(_pf.lower())
                pricing_features.append(_pf)
    page_features = page_features + [
        {"category": _classify_feature(f), "name": f, "desc": ""}
        for f in pricing_features
    ]
    if len(page_features) < 5 and docs_md:
        page_features = _extract_features_from_page(
            features_base_md + "\n" + docs_md
        )
    # 逐条归因:功能文本在哪个页面出现,source 就指向哪个页面的 URL
    # (历史缺陷:全部归到 features 页/首页 —— 从 pricing 页提取的功能
    # 指向了 404 的 features URL,读者点开找不到证据)
    page_candidates = [
        (feat_md, scraped.get("page_urls", {}).get("features") or ""),
        (home_md, scraped["url"]),
        (pricing_md, scraped.get("page_urls", {}).get("pricing") or ""),
        (docs_md, scraped.get("page_urls", {}).get("docs") or ""),
    ]
    enriched_features = []
    for f in page_features:
        ftxt = (f.get("name") or "").strip()
        src = ""
        for md, u in page_candidates:
            if md and u and ftxt and ftxt[:30] in md:
                src = u
                break
        # F4:定位不到出处 → source 留空(render 已兼容空 source),
        # 不再默认挂 default_src —— 挂错页比不挂更误导
        enriched_features.append({**f, "source": src})

    # 构造本地 c 字典,用于 _derive_*_from_features 类函数
    c = {
        "name": name,
        "url": scraped["url"],
        "stage": "—",
        "feature_catalog": {name: enriched_features},
    }

    # F3 公司信息行级归属:about 页优先,逐页提取并记录命中页 + quote
    # (历史缺陷:页级归属 —— 定价页命中的年份被标成官网来源)
    company_pages = [
        (md, u)
        for md, u in (
            (about_md, scraped.get("page_urls", {}).get("about") or ""),
            (home_md, scraped["url"]),
            (feat_md, scraped.get("page_urls", {}).get("features") or ""),
            (pricing_md, scraped.get("page_urls", {}).get("pricing") or ""),
        )
        if md and u
    ]

    warnings = []

    # 检测爬取质量 — 逐页列出失败模块(about/docs 失败曾静默吞掉,
    # founded/HQ/团队规模悄悄降级到首页猜测,读者完全不知情)
    failed_pages = [
        kind
        for kind, md in (
            ("home", home_md), ("features", feat_md),
            ("pricing", pricing_md), ("about", about_md), ("docs", docs_md),
        )
        if scraped.get("page_urls", {}).get(kind) and _looks_like_only_js_or_404(md)
    ]
    if failed_pages:
        warnings.append(f"页面爬取失败(JS/404/空): {', '.join(failed_pages)}")

    # 启发式提取(带 quote 证据;提取不到就留 "—",绝不 fallback 到静态库)
    # tagline 优先级:站点 <title>(官方一句话定位)> meta description >
    # H1/H2 > 散文段落。title 来自 trafilatura frontmatter / jina,干净可靠
    site_title = _clean_tagline_text(scraped.get("site_title", ""))
    site_desc = _clean_tagline_text(scraped.get("site_meta_description", ""))
    if site_title and len(site_title) >= 8 and not _TAGLINE_JUNK_RX.search(site_title):
        tagline = site_title
    elif site_desc and len(site_desc) >= 12 and not _TAGLINE_JUNK_RX.search(site_desc):
        tagline = site_desc
    else:
        tagline = _extract_tagline(home_md)
    founded, founded_src, founded_quote = _extract_company_field(
        company_pages, _extract_founded, _COMPANY_CTX_RX["founded"])
    location, hq_src, hq_quote = _extract_company_field(
        company_pages, _extract_location, _COMPANY_CTX_RX["headquarters"])
    team_size, team_src, team_quote = _extract_company_field(
        company_pages, _extract_team_size, _COMPANY_CTX_RX["team_size"])
    pricing = _extract_price(pricing_md) or _extract_price(home_md)

    # 定价:优先用跨引擎投票结果(带验证标记 + 来源 + 时间戳)
    pricing_ev = scraped.get("pricing_evidence") or {}
    _pricing_starved_note = ""
    _pcache = _load_pricing_cache()
    _ckey = name
    if not pricing_ev.get("vote_detail"):
        # 全引擎 0 价格行:反爬/区域变体页。回退上次成功抓取的缓存
        # (≤14 天,带原时间戳如实标注)—— 消除运行间不稳定
        cached = _pcache.get(_ckey)
        # F1: TTL 生效 —— 过期缓存视为 miss(此前永不过期,陈旧价永久 verified)
        if cached and _has_real_prices(cached.get("tiers")) and _cache_fresh(cached):
            pricing_ev = dict(cached)
            _pricing_starved_note = (
                f"本次爬取遇反爬/区域变体页,定价为上次成功抓取"
                f"({cached.get('scraped_at', '?')})的已验证数据,请以官网为准"
            )
        else:
            _pricing_starved_note = (
                "本次爬取未提取到数字价格(页面可能返回了反爬/区域变体),"
                "请以官网定价页为准 —— 官网未必未公示"
            )
    elif _has_real_prices(pricing_ev.get("tiers")):
        # 本轮成功 → 写缓存供下轮反爬时回退
        _pcache[_ckey] = {
            k: pricing_ev.get(k)
            for k in ("pricing", "verified", "engines", "tiers",
                      "vote_detail", "source_url", "scraped_at")
        }
        _save_pricing_cache(_pcache)
    _pricing_blob = "\n".join(
        [pricing_md] + list(scraped.get("pricing_all_markdowns") or [])
    )
    # 两级 per-user 判定(历史缺陷:WATI 的 "Additional Users @ $24/user/month"
    # 加购条款和 Meetbot 的 "/user-agreement" 链接都让整站被误标 per user):
    #   强证据 = 主价行文本明确 "per user/month" 类计价词 → tier 标注
    #   弱证据 = 仅加购条款("additional users at $X") → 只加提示,不断言
    _strong_rx = re.compile(
        r"per\s+(?:user|seat|agent)\s*/?\s*(?:per\s*)?(?:month|mo|year|yr)|每位用户|按坐席计费",
        re.I,
    )
    _addon_rx = re.compile(
        r"additional\s+users?\s*(?:@|at)\s*\$?\d|/user/(?![\w-])|加购用户|额外坐席",
        re.I,
    )
    _main_price_lines = "\n".join(
        v["line"] for v in (pricing_ev.get("vote_detail") or [])
    )
    _PER_USER = bool(_strong_rx.search(_main_price_lines) or _strong_rx.search(_pricing_blob))
    _PER_USER_ADDON = (not _PER_USER) and bool(_addon_rx.search(_pricing_blob))
    if pricing_ev.get("pricing") and pricing_ev["pricing"] != "—":
        pricing = pricing_ev["pricing"]
    if pricing == "—":
        warnings.append("pricing 未能从官网提取 —— 报告将标「未验证」,请人工核对官网")
    entry = {
        "name": name,
        "url": scraped["url"],
        "tagline": tagline or "—",
        "founded": founded or "—",
        "founded_quote": founded_quote,
        "stage": "—",
        "headquarters": location or "—",
        "headquarters_quote": hq_quote,
        "team_size": team_size or "—",
        "team_size_quote": team_quote,
        "funding": "—",
        "pricing": pricing,
        "pricing_verified": bool(pricing_ev.get("verified")),
        # 定价是否来自缓存回退(反爬 starved 时)—— 报告端如实展示
        "pricing_from_cache": bool(_pricing_starved_note and pricing_ev.get("verified")),
        # per-user 计价检测:respond.io "Additional Users at $" / 官网
        # "per user/month" —— 缺了 /user 语义,$79/month 就是错的
        "pricing_unit": "per user" if _PER_USER else "",
        "pricing_addon_note": (
            "套餐含基础用户数,加购用户另计费(见官网)" if _PER_USER_ADDON else ""
        ),
        "pricing_currency": _detect_currency(
            pricing, [t.get("price", "") for t in pricing_ev.get("tiers", [])]
        ),
        "pricing_tiers": [
            {
                **t,
                "billing_period": (
                    f'per user · {t["billing_period"]}'
                    if _PER_USER
                    and t.get("billing_period")
                    and "per user" not in t["billing_period"]
                    else t.get("billing_period", "—")
                ),
            }
            for t in pricing_ev.get("tiers", [])
        ],
        "pricing_engines": pricing_ev.get("engines", []),
        # F2:定价来源优先投票证据的 source_url;稀松回退(_extract_price
        # 从首页提到价)时如实指向首页;两者皆无 → 空(绝不用猜测 URL 兜底)
        "pricing_source": (
            pricing_ev.get("source_url")
            or (scraped["url"]
                if (pricing != "—" and not pricing_ev.get("pricing")
                    and _extract_price(pricing_md)) else "")
        ),
        "pricing_scraped_at": pricing_ev.get("scraped_at", ""),
        "pricing_vote_detail": pricing_ev.get("vote_detail", []),
        **(
            {"pricing_crawl_note": _pricing_starved_note}
            if _pricing_starved_note
            else {}
        ),
        "target_users": _derive_target_users(c),
        "core_features": _derive_core_features(c),
        "feature_catalog": {
            name: enriched_features
        },
        # strengths 种子:官网自述的量化事实(客户数/认证/官方身份/GTM 信号)
        # —— 每条带原文 quote + source_url,读者可点开核对。这不是伪造:
        # 官网说 "Trusted by 8000+ teams" 就是可引用的自述事实。
        # Step 3 LLM 应在此基础上补第三方视角(G2/Reddit)并替换。
        "strengths": [
            {
                "point": ev["name"],
                "evidence": f"官网原文: \"{ev['quote']}\"",
                "score": 0,
                "source": ev["source"],
            }
            for ev in (_derive_moat_evidence(
                home_md, about_md, pricing_md,
                scraped["url"],
                scraped.get("page_urls", {}).get("about", ""),
                scraped.get("page_urls", {}).get("pricing", ""),
            ) + _derive_gtm_evidence(
                home_md, about_md, scraped["url"],
                scraped.get("page_urls", {}).get("about", ""),
            ))[:3]
        ],
        "weaknesses": [],  # 弱点 = 负面评价,官网不会自述,必须 Step 3 从
        #                   第三方(G2/Reddit/评测)提取 —— 脚本绝不编造
        "differentiators": _derive_differentiators(name, pricing, features_str_for_diff(name, feat_md)),
        # GTM/护城河:基于 home+about 原文证据推导(每条带 quote+source,
        # 模板渲染为可点引用;区别于历史"待 Step 3"空占位 —— 读者现在
        # 就能看到有据可查的商业模式信号)
        "gtm_evidence": _derive_gtm_evidence(
            home_md, about_md, scraped["url"],
            scraped.get("page_urls", {}).get("about", ""),
        ),
        "moat_evidence": _derive_moat_evidence(
            home_md, about_md, pricing_md,
            scraped["url"],
            scraped.get("page_urls", {}).get("about", ""),
            scraped.get("page_urls", {}).get("pricing", ""),
        ),
        "tech_signals": _derive_tech_signals(
            docs_md, scraped.get("page_urls", {}).get("docs", "")
        ),
        "scores": {
            "feature_richness": 5,
            "ux": 5,
            "pricing_value": 5,
            "integration": 5,
            "ai_capability": 5,
            "momentum": 5,
        },
        # 告诉渲染端/读者:这是占位分,不是真实评估(LLM Step 3 应基于证据重评)
        "score_basis": "insufficient_evidence",
    }
    # 给所有字段加 source
    for k in ("pricing",):
        if entry.get(k):
            entry[f"{k}_source"] = scraped.get(f"{k}_source", scraped["url"])
    entry["tagline_source"] = scraped.get("tagline_source", scraped["url"])
    # F3:公司信息来源 = 行级归属的命中页(抓不到就是空,不再兜底官网)
    entry["founded_source"] = founded_src
    entry["headquarters_source"] = hq_src
    entry["team_size_source"] = team_src

    if not warnings:
        warnings.append("✓ 全部页面爬取成功,启发式提取 OK")

    # F8 claims:本竞品全部可验证断言(verify.py G1/G2 的消费对象)
    claims = []

    def _claim(field, value, source_url, quote="", engine="", verified_by=None,
               from_cache=False, scraped_at=""):
        claims.append({
            "field": f"competitors[{idx}].{field}", "value": str(value),
            "source_url": source_url, "quote": (quote or "")[:120],
            "engine": engine, "verified_by": verified_by or [],
            "from_cache": from_cache, "scraped_at": scraped_at,
        })

    _purl = entry.get("pricing_source") or ""
    _psat = entry.get("pricing_scraped_at") or ""
    for j, t in enumerate(entry.get("pricing_tiers") or []):
        if t.get("price") and "未能提取" not in str(t.get("price")):
            _claim(f"pricing_tiers[{j}].price", t["price"],
                   t.get("source_url") or _purl, quote="", scraped_at=_psat,
                   verified_by=entry.get("pricing_engines") or [],
                   from_cache=bool(entry.get("pricing_from_cache")))
    for k, v in enumerate(entry.get("pricing_vote_detail") or []):
        _claim(f"pricing_vote_detail[{k}].line", v.get("line", ""),
               _purl, quote=v.get("raw_line") or v.get("line", ""),
               scraped_at=_psat,
               verified_by=v.get("engines") or [],
               from_cache=bool(entry.get("pricing_from_cache")))
    for key in ("gtm_evidence", "moat_evidence"):
        for k, ev in enumerate(entry.get(key) or []):
            _claim(f"{key}[{k}]", ev.get("name", ""), ev.get("source", ""),
                   quote=ev.get("quote", ""))
    for k, s in enumerate(entry.get("strengths") or []):
        m = re.search(r'官网原文:\s*"(.+?)"', s.get("evidence") or "")
        _claim(f"strengths[{k}]", s.get("point", ""), s.get("source", ""),
               quote=m.group(1) if m else "")
    if entry.get("tagline") and entry["tagline"] != "—":
        _claim("tagline", entry["tagline"], entry.get("tagline_source", ""),
               quote=entry["tagline"])
    for fld in ("founded", "headquarters", "team_size"):
        if entry.get(fld) and entry[fld] != "—":
            _claim(fld, entry[fld], entry.get(f"{fld}_source", ""),
                   quote=entry.get(f"{fld}_quote", ""))
    return entry, warnings, claims


def _auto_detect_feature_aliases(competitors):
    """自动检测同名功能(同 feature 名出现在 ≥2 家)。"""
    from collections import defaultdict

    name_to_vendors: dict = defaultdict(set)
    for c in competitors:
        fc = c.get("feature_catalog", {}).get(c["name"], [])
        for f in fc:
            fname = (f.get("name", "") or "").strip()
            if fname:
                name_to_vendors[fname].add(c["name"])

    aliases = {}
    for name, vendors in name_to_vendors.items():
        if len(vendors) >= 2:
            aliases[name] = {
                "aliases": [name],
                "rationale": f'自动检测: "{name}" 在 {len(vendors)} 家出现。',
                "_auto_detected": True,
            }
    return aliases


def crawl_and_build(names: List[str], topic: str, timeout: int = 30,
                    manifest_path=None, raw_dir=None) -> Dict:
    """主入口:接收名称列表,返回完整分析 JSON。

    manifest_path/raw_dir 给定时,F8 证据包(claims-manifest.json +
    02-raw/<name>.engines.json)同步落盘,供 verify.py 验证。
    """
    run_started_at = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    print(f"🔍 解析 {len(names)} 个竞品名称...")
    resolved = resolve_competitors(names)

    found = [k for k, v in resolved.items() if v.get("confidence", 0) > 0]
    missing = [k for k, v in resolved.items() if v.get("error")]
    print(f"  ✓ 找到: {len(found)} 个 ({', '.join(found)})")
    if missing:
        print(f"  ⚠ 未找到: {len(missing)} 个 ({', '.join(missing)})")
        print(
            "    提示: 这些需要先补充到 adapters/competitor_resolver.py 的 _BUILTIN_COMPETITORS"
        )
        print("    或使用完整 URL: --competitors 'https://example.com,...'")

    if not found:
        print("\n✗ 无有效竞品可爬取")
        sys.exit(1)

    print(f"\n🌐 并行爬取 {len(found)} 个竞品...")
    competitors = []
    all_warnings = []
    all_claims = []
    manifest_fetched: Dict = {}
    manifest_engines: Dict = {}
    manifest_failures: List[Dict] = []
    for ci, name in enumerate(found):
        info = resolved[name]
        print(f"\n  📡 抓取 {name} ({info['url']})")
        # F7:domain-guess 解析置信度低(0.4),显式提示人工核对
        if info.get("source") != "builtin":
            print(f"     ⚠ URL 为域名猜测(confidence={info.get('confidence')}),请核对")
        scraped = _scrape_one(info, timeout=timeout)
        entry, warnings, claims = _build_competitor_entry(scraped, idx=ci)
        entry["url_resolution"] = info.get("source", "")
        competitors.append(entry)
        all_claims.extend(claims)
        for w in warnings:
            print(f"     ⚠ {w}")
            all_warnings.append(f"[{name}] {w}")
        # 合并该竞品的证据包记录(同 URL 后写覆盖)
        m = scraped.get("_manifest") or {}
        manifest_fetched.update(m.get("fetched") or {})
        manifest_failures.extend(m.get("failures") or [])
        manifest_engines[name] = m.get("engines_by_url") or {}

    # 自动检测别名
    auto_aliases = _auto_detect_feature_aliases(competitors)

    # 生成市场细分、空白、机会 — 让报告完整可用
    segments = _derive_market_segments(competitors)
    gaps = _derive_market_gaps(competitors)
    opportunities = _derive_opportunities(competitors, gaps, topic)
    other_competitors = _derive_other_competitors(competitors, topic)

    # 合并成最终 JSON(包含 §5.2/§8 所需的所有字段)
    n_verified = sum(1 for c in competitors if c.get("pricing_verified"))
    analysis = {
        "topic": topic,
        "subtitle": f"{topic} — 基于 {len(competitors)} 家竞品实时爬取的深度分析",
        "date": time.strftime("%Y-%m-%d"),
        "competitors": competitors,
        "feature_aliases": auto_aliases,
        "market_segments": segments,
        "gaps": gaps,
        "opportunities": opportunities,
        "other_competitors": other_competitors,
        "executive_summary": (
            f"基于 {len(competitors)} 家竞品实时爬取:{len(gaps)} 条候选市场空白"
            f"(待 Step 3 复核)。定价 {n_verified}/{len(competitors)} 家经 ≥2 引擎交叉验证,"
            "其余标记「未验证」。SWOT/机会清单由 LLM 基于证据补全。"
        ),
        "background": (
            f"{topic} 赛道扫描 — 基于 {len(competitors)} 家竞品实时爬取。"
            f"涵盖 {sum(len(c.get('feature_catalog', {}).get(c['name'], [])) for c in competitors)} 项核心功能。"
            "每个功能条目带来源 URL。"
        ),
        "goals": [
            "看清各家功能差异化与共同能力",
            "识别市场空白（所有竞品都没做好的痛点）",
            "梳理定价模型与市场分层",
            "挖掘颠覆性产品机会",
        ],
    }
    print(
        f"\n✅ 完成: {len(competitors)} 家竞品,自动检测到 {len(auto_aliases)} 组同义合并,"
        f"生成 {len(gaps)} 条空白、{len(opportunities)} 个机会"
    )

    # F8 证据包落盘(analysis 的每条可验证断言 + 本轮全部抓取记录)
    if manifest_path:
        manifest = {
            "run": {
                "topic": topic,
                "started_at": run_started_at,
                "finished_at": time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()),
                "pipeline_version": "2.0",
            },
            "fetched": manifest_fetched,
            "claims": all_claims,
            "failures": manifest_failures,
        }
        mpath = Path(manifest_path)
        mpath.parent.mkdir(parents=True, exist_ok=True)
        mpath.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
        if raw_dir:
            rd = Path(raw_dir)
            rd.mkdir(parents=True, exist_ok=True)
            for name_key, engines_by_url in manifest_engines.items():
                safe = re.sub(r"[^\w.-]", "_", name_key)
                (rd / f"{safe}.engines.json").write_text(
                    json.dumps(engines_by_url, ensure_ascii=False, indent=1),
                    encoding="utf-8")
        print(f"💾 证据包: {mpath}")
    return analysis


def _derive_market_segments(competitors):
    """根据竞品 stage + features 自动推导市场细分。

    按 BSP / SaaS 工具 / 广告投放 等聚类。
    """
    from collections import defaultdict
    by_stage = defaultdict(list)
    for c in competitors:
        stage = c.get("stage", "未知")
        by_stage[stage].append(c["name"])

    segment_templates = {
        "巨头": {"label": "电信级平台 / BSP", "desc": "Twilio/Infobip 等大型 CPaaS 厂商,做底层 API + 大客户合规"},
        "成长期": {"label": "SaaS 工具(中小客户)", "desc": "中小 SaaS 厂商,瞄准中小企业,开箱即用"},
        "早期": {"label": "新势力 / 早期产品", "desc": "较新的厂商,灵活度更高,功能可能不完整"},
        "未知": {"label": "其他 / 待识别", "desc": "暂未分类"},
    }
    # 每个 stage 一个 segment(不截断,多 stage 多细分)
    segments = [
        {**segment_templates.get(stage, segment_templates["未知"]),
         "players": players, "source": ""}
        for stage, players in by_stage.items()
    ]
    # 同一 stage 人太多时,按定价模式二次细分(自助服务 vs 企业销售)——证据来自 pricing 字段
    if len(segments) == 1 and len(segments[0]["players"]) >= 4:
        by_pricing = {"自助服务(免费/低价起步)": [], "企业销售(高客单/定制)": []}
        for c in competitors:
            p = c.get("pricing", "")
            m = re.search(r"\$(\d+)", p)
            low_entry = ("免费" in p or "Free" in p or (m and int(m.group(1)) <= 49))
            key = "自助服务(免费/低价起步)" if low_entry else "企业销售(高客单/定制)"
            by_pricing[key].append(c["name"])
        if by_pricing["自助服务(免费/低价起步)"] and by_pricing["企业销售(高客单/定制)"]:
            segments = [
                {
                    "label": label,
                    "desc": desc,
                    "players": players,
                    "source": "",
                }
                for label, players in by_pricing.items()
                if players
                for desc in [
                    "免费版/低门槛定价获客,产品驱动增长,中小客户自助开通"
                    if "自助" in label
                    else "面向中大客户,销售驱动,高客单 + 定制交付"
                ]
            ]
    return segments


# gap → 检测别名(中英文),gap 推导与机会推导共用,避免"把 omnichannel 玩家
# 误判成缺多渠道"的假空白
_GAP_KEYWORDS: Dict[str, List[str]] = {
    "Webhook 事件推送": ["webhook"],
    "AI 转人工升级": ["human hand", "转人工", "escalat", "ai agent", "ai chatbot"],
    "多渠道接入 (Omnichannel Aggregation)": [
        "omnichannel", "multi-channel", "multichannel", "channels in one",
        "one thread", "all channels", "every channel", "多渠道", "全渠道",
    ],
    "数据导出 (Data Export)": ["export", "导出"],
    "权限与团队管理 (RBAC)": ["rbac", "role-based", "team member", "权限", "坐席"],
    "审计日志 (Audit Logs)": ["audit log", "审计日志"],
    "真正端到端 ROI 闭环": ["roi closed-loop", "端到端"],
    "中小客户友好的无代码 AI 配置": ["no-code ai", "无代码"],
    "AI Agent 主动外呼": ["proactive", "主动外呼"],
}


def _derive_market_gaps(competitors):
    """从各竞品的 feature_catalog 检测「全行业缺失」的能力(候选 gap)。

    只输出有爬取证据支撑的候选:该能力关键词在所有已爬文本中均未出现。
    每条列出「检查了哪些家、各自的证据来源」供 LLM Step 3 复核 ——
    关键词未命中 ≠ 真空白(可能是同义表述),最终判定由 LLM 完成。
    不再使用硬编码兜底 gap(历史版本曾塞入"真正端到端 ROI 闭环"等
    与实际爬取内容无关的模板空白)。
    """
    vendor_features = {}
    for c in competitors:
        feats = {f["name"] for f in c.get("feature_catalog", {}).get(c["name"], [])}
        feats.add(c.get("tagline", "") or "")
        vendor_features[c["name"]] = feats
    all_text = " || ".join(f for v in vendor_features.values() for f in v).lower()

    expected_core = [
        ("Webhook 事件推送", "Webhook 是开发者集成基础,缺失说明产品还没做开发者生态"),
        ("AI 转人工升级", "AI 自动升级到人工客服是基本能力"),
        ("多渠道接入 (Omnichannel Aggregation)", "多渠道整合是行业标配,缺失说明产品单一渠道"),
        ("数据导出 (Data Export)", "数据可移植性是合规基本要求"),
        ("权限与团队管理 (RBAC)", "RBAC 是企业级必备"),
        ("审计日志 (Audit Logs)", "审计日志是企业合规必备"),
    ]
    gaps = []
    for feat_name, rationale in expected_core:
        keywords = _GAP_KEYWORDS.get(feat_name, [feat_name.lower().split(" ")[0]])
        if any(k in all_text for k in keywords):
            continue
        gaps.append({
            "gap": feat_name,
            "rationale": rationale,
            "severity": "medium",
            "candidates": True,  # 标记:脚本级候选,需 LLM Step 3 复核后定稿
            "checked_vendors": [
                {"name": c["name"], "evidence_url": c.get("url", "")}
                for c in competitors
            ],
            "source": "",  # 证据 = 各家官网爬取文本本身,不伪造第三方 URL
        })
    return gaps[:6]


def _derive_opportunities(competitors, gaps, topic):
    """颠覆性机会由 LLM 在 Step 3 基于证据生成(见 analysis-framework.md prompt)。

    历史教训:这里曾是模板拼接("颠覆机会 #1: {gap 名}" + 通用差异化话术),
    输出看似完整实为空洞的正确废话。脚本层不再生成。
    """
    return []


def _derive_other_competitors(competitors, topic):
    """§8 其他竞品 — 由 LLM 在 Step 1(搜索发现)阶段填充。

    历史教训:这里曾是 11 家 WhatsApp 赛道竞品的硬编码池(含从未爬取的
    price_hint,如 "Chatfuel Pro $15/月")—— 跑任何主题都会把这些过期
    数据渲染进报告。删除;数据必须来自本次搜索/爬取。
    """
    return []


def main():
    ap = argparse.ArgumentParser(description="批量爬取指定竞品 → 生成分析 JSON")
    ap.add_argument(
        "--competitors",
        required=True,
        help="竞品名称列表(逗号分隔),如 'ycloud,sleekflow,wati,respond.io,meetbot'",
    )
    ap.add_argument("--topic", default="竞品分析", help="报告主题")
    ap.add_argument("--output", required=True, help="输出分析 JSON 路径")
    ap.add_argument("--timeout", type=int, default=30, help="每个爬虫超时秒")
    args = ap.parse_args()

    names = [n.strip() for n in args.competitors.split(",") if n.strip()]
    if not names:
        print("✗ --competitors 为空")
        sys.exit(1)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    analysis = crawl_and_build(
        names, args.topic, timeout=args.timeout,
        manifest_path=out.parent / "claims-manifest.json",
        raw_dir=out.parent / "02-raw",
    )

    out.write_text(json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n💾 已写入: {out}")
    print(f"   下一步: python3 render.py --input {out} --output report.html")
    print(
        f"   验证:   python3 verify.py --analysis {out} "
        f"--manifest {out.parent / 'claims-manifest.json'} "
        f"--raw-dir {out.parent / '02-raw'}"
    )


if __name__ == "__main__":
    main()
