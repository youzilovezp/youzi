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
from typing import Optional

# 货币符号(单字符,无需词边界)
_SYM = r"[$€£¥₹]"
# 货币代码(字母开头,必须词边界防误伤 —— "Hours 39" 里的 "rs" 不算 Rs)
_CODE = r"(?:USD|EUR|GBP|INR|CNY|CAD|AUD)"

# 统一价格 token 正则:
#   前缀路: (?<![.\d]) 防 "1.5$" 里截出 "5$";US$/S$ 整体优先于裸 $
#   后缀路: 欧陆 "39 €" / "1.068 €" / "39 元";代码结尾要求非字母数字
PRICE_TOKEN_RX = re.compile(
    rf"(?<![.\d])(?:\bUS\$|\bS\$|{_SYM}|\b{_CODE}\b|\bRs\.?)\s?"
    rf"\d[\d,]*(?:\.\d+)?"
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


def is_price_like(text: str) -> Optional[re.Match]:
    """文本是否含价格 token(语义糖,兼做 fullmatch 场景的入口)。"""
    return PRICE_TOKEN_RX.search(text or "")
