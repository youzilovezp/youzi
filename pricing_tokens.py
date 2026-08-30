# -*- coding: utf-8 -*-
"""价格 token 单一事实源 —— fetch(投票)/sufficiency(评估)/audit(审计)/
playwright(价格等待) 四处共用同一正则,消灭三套口径并存的历史问题。

历史事故(2026-08-29 审计实测):
  - fetch.py 投票正则缺 ₹/Rs./后缀€/USD 代码 → 印度系竞品(AiSensy/
    Interakt/Gupshup,内置表在列)playwright 抓到 ₹999 却投不出票 →
    判不充分 → 升级梯空转烧预算,台账记「价格仅 0 引擎见到」;
    而 audit 用的 sufficiency 正则看得到 ₹ → 两层结论矛盾。
  - sufficiency `US?\\$\\s*\\d` 缺多位数量词 → "US$59" 截断成 "US$5" →
    audit_price_votes 的 digits 比对(59 vs 5)永远假阴性。

设计:前缀(符号/代码在前)+ 后缀(欧陆数字在前)两路交替;
投票键归一化到 (货币, 数字) —— "US$59"/"$59"/"59 USD" 是同一票。
"""

import re

# 货币符号(单字符,无需词边界)
_SYM = r"[$€£¥₹]"
# 货币代码(字母开头,必须词边界防误伤 —— "Hours 39" 里的 "rs" 不算 Rs)
_CODE = r"(?:USD|EUR|GBP|INR|CNY|CAD|AUD)"

# 统一价格 token 正则:
#   前缀路: (?<![.\d]) 防 "1.5$" 里截出 "5$";US$/S$ 整体优先于裸 $;
#           数字允许无前导零小数($.012/$.99 —— PAYG 计费页写法,
#           2026-08-30 审计:原正则漏检,而 gates._PRICING_SEMANTICS_RX
#           显式支持 \.\d+,两套口径不一致)
#   后缀路: 欧陆 "39 €" / "1.068 €" / "39 元";代码结尾要求非字母数字
PRICE_TOKEN_RX = re.compile(
    rf"(?<![.\d])(?:\bUS\$|\bS\$|{_SYM}|\b{_CODE}\b|\bRs\.?)\s?"
    rf"(?:\d[\d,]*(?:\.\d+)?|\.\d+)"
    rf"|\d[\d,]*(?:\.\d+)?\s?(?:{_SYM}|\b{_CODE}\b|元)",
    re.I,
)

# 货币归一映射:US$/S$ → $;代码 → 符号;Rs → ₹(投票键用)
_CURRENCY_NORM = {
    "us$": "$",
    "s$": "$",
    "usd": "$",
    "eur": "€",
    "gbp": "£",
    "inr": "₹",
    "rs": "₹",
    "cny": "¥",
    "cad": "$",
    "aud": "$",
    "元": "¥",
}


def price_currency(token: str) -> str:
    """从 token 提取归一化货币("$"/"€"/"£"/"¥"/"₹");无货币返回空串。"""
    low = (token or "").lower()
    for k in sorted(_CURRENCY_NORM, key=len, reverse=True):
        if k in low:
            return _CURRENCY_NORM[k]
    for ch in low:
        if ch in "$€£¥₹":
            return ch
    return ""


def price_digits(token: str) -> str:
    """价格数字部分:"$1,068"→"1068";"Rs. 999"→"999"(句点不属于数字)。

    用正则提取数字段而非全局剥离 —— "Rs." 的点会污染朴素实现。
    """
    m = re.search(r"\d[\d,]*(?:\.\d+)?", token or "")
    return m.group(0).replace(",", "") if m else ""


def price_vote_key(token: str) -> str:
    """跨引擎投票键 = 归一货币 + 数字。

    "US$59" / "$59" / "59 USD" → "$|59"(同一票);"₹999" → "₹|999"。
    写法差异拆散交叉验证是历史 bug 的根因之一。
    """
    cur = price_currency(token)
    dig = price_digits(token)
    return f"{cur}|{dig}" if dig else ""


# 2026-08-30 清理:删除 is_price_like(语义糖)—— 全库零调用方(grep 证实),
# 调用方一律直接用 PRICE_TOKEN_RX.search()。


# ── 计费周期归一化(gates G8 与 render 共用的单一事实源) ──
# 历史:render 内联归一化容忍老数据("month"/"billed annually"),G8 第 17 轮
# 复刻了一份 —— 两处规则必然漂移(G8 曾比 render 严而误拦 "/month")。
# 第 19 轮收口:逻辑移到本模块,两侧 import。
#
# 2026-08-30 契约对齐修复(用户反馈「报告质量不如之前」复盘):
#   - 单位限定词(/user /seat /人 /席位)不是周期 —— sufficiency 对
#     free/custom 档容忍 "/user"/"/seat",但归一化原样放行 → G8 hard
#     → run_youzi 不交付。剥离后再匹配,裸 "/user" 归 "" 恰好落入
#     无周期通道,两侧契约对齐。
#   - 中文周期词(按月/月付/每年/包年…)此前原样放行 → 同样被 G8 拦死。
#   - month+year 同现("monthly billed annually"/"monthly (annual
#     discount)")是年结算月价 → billed 通道(此前 month 分支被 year
#     否决后误入 /yr,定价卡通道错位)。

_PERIOD_UNIT_RX = re.compile(
    r"\s*(?:/\s*|per\s+)(?:users?|seats?|人|席位|坐席)(?![a-z])", re.I
)
_PERIOD_MONTH_RX = re.compile(r"month|/mo|月付|按月|每月|包月|月度", re.I)
_PERIOD_YEAR_RX = re.compile(r"year|\byr\b|annual|/yr|年付|按年|每年|包年|年度", re.I)
_PERIOD_BILLED_RX = re.compile(r"billed|结算|discount|折扣|打折|优惠", re.I)
VALID_PERIODS = ("/mo", "billed", "/yr", "—", "")


def normalize_billing_period(per: str) -> str:
    """把原始周期文本归一到三通道+无周期;未知值原样返回(由 G8 拦截)。

    单位限定词先剥离:"$19/user/mo"→"/mo"、"/user"→""。
    "/month"/"Monthly"/"按月" → "/mo";"billed annually"/"按月年结算" → "billed";
    "per year"/"/yr"/"包年" → "/yr";"—"/"" → 原样;其他(如"一次性") → 原样。
    """
    per = _PERIOD_UNIT_RX.sub("", (per or "")).strip()
    has_m = bool(_PERIOD_MONTH_RX.search(per))
    has_y = bool(_PERIOD_YEAR_RX.search(per))
    if has_m and has_y:
        return "billed"  # 月价 × 年语义 = 年结算月价
    if has_m:
        return "/mo"
    if _PERIOD_BILLED_RX.search(per):
        return "billed"
    if has_y:
        return "/yr"
    return per
