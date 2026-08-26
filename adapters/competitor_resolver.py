# -*- coding: utf-8 -*-
"""
Competitor Resolver — 竞品名称 → URL + 爬取路径解析

支持两种模式:
1. 内置映射表（最可靠，无需网络）
2. WebSearch 回退（处理未知名称）

输入: 竞品名称字符串 ("ycloud", "wati", "Sleekflow" 等,大小写不敏感)
输出: {
    "name": str,
    "canonical_name": str,         # 规范化名
    "url": str,                    # 官网
    "features_url": str,            # 功能页
    "pricing_url": str,             # 定价页
    "docs_url": Optional[str],      # 文档/API 文档
    "source": str,                  # 解析来源 ("builtin" / "websearch")
    "confidence": float,            # 0-1,可信度
}
"""

import re
from typing import Dict, List, Optional


# 内置竞品库 — 主流 WhatsApp / 客服 / 营销 SaaS
# 字段:
#   url:        官网
#   features:   功能页(可选,fallback 到 url)
#   pricing:    定价页
#   docs:       文档页(可选)
_BUILTIN_COMPETITORS: Dict[str, Dict] = {
    # WhatsApp BSP / SaaS
    "wati": {
        "name": "WATI",
        "url": "https://www.wati.io",
        "features": "https://www.wati.io/platform/",
        "pricing": "https://www.wati.io/pricing",
        "docs": "https://docs.wati.io",
    },
    "respond.io": {
        "name": "Respond.io",
        "url": "https://respond.io",
        "features": "https://respond.io/features",
        "pricing": "https://respond.io/pricing",
    },
    "manychat": {
        "name": "ManyChat",
        "url": "https://manychat.com",
        "features": "https://manychat.com/features",
        "pricing": "https://manychat.com/pricing",
    },
    "tidio": {
        "name": "Tidio",
        "url": "https://www.tidio.com",
        "features": "https://www.tidio.com/features/",
        "pricing": "https://www.tidio.com/pricing/",
    },
    "twilio": {
        "name": "Twilio",
        "url": "https://www.twilio.com",
        "features": "https://www.twilio.com/features",
        "pricing": "https://www.twilio.com/en-us/messaging/pricing",
        "docs": "https://www.twilio.com/docs",
    },
    "infobip": {
        "name": "Infobip",
        "url": "https://www.infobip.com",
        "features": "https://www.infobip.com/products",
        "pricing": "https://www.infobip.com/pricing",
        "docs": "https://www.infobip.com/docs",
    },
    "messagebird": {
        "name": "MessageBird",
        "url": "https://www.messagebird.com",
        "pricing": "https://www.messagebird.com/pricing",
    },
    "360dialog": {
        "name": "360dialog",
        "url": "https://www.360dialog.com",
        "pricing": "https://www.360dialog.com/pricing",
    },
    "vonage": {
        "name": "Vonage",
        "url": "https://www.vonage.com",
        "pricing": "https://www.vonage.com/unified-communications/pricing/",
    },
    "gupshup": {
        "name": "Gupshup",
        "url": "https://www.gupshup.io",
        "pricing": "https://www.gupshup.io/pricing",
    },
    "sleekflow": {
        "name": "Sleekflow",
        "url": "https://sleekflow.io",
        "features": "https://sleekflow.io/features",
        "pricing": "https://sleekflow.io/pricing",
    },
    "ycloud": {
        "name": "YCloud",
        "url": "https://www.ycloud.com",
        "features": "https://www.ycloud.com/features",
        "pricing": "https://www.ycloud.com/pricing",
    },
    "gallabox": {
        "name": "Gallabox",
        "url": "https://www.gallabox.com",
        "features": "https://www.gallabox.com/features",
        "pricing": "https://www.gallabox.com/pricing",
    },
    "aisensy": {
        "name": "AiSensy",
        "url": "https://aisensy.com",
        "pricing": "https://aisensy.com/pricing",
    },
    "interakt": {
        "name": "Interakt",
        "url": "https://www.interakt.ai",
        "pricing": "https://www.interakt.ai/pricing",
    },
    "meetbot": {
        "name": "Meetbot",
        "url": "https://meetbot.com",
        "features": "https://meetbot.com/features",
        "pricing": "https://meetbot.com/pricing",
    },
    "intercom": {
        "name": "Intercom",
        "url": "https://www.intercom.com",
        "pricing": "https://www.intercom.com/pricing",
    },
    "zendesk": {
        "name": "Zendesk",
        "url": "https://www.zendesk.com",
        "pricing": "https://www.zendesk.com/pricing",
    },
    "freshdesk": {
        "name": "Freshdesk",
        "url": "https://www.freshworks.com/freshdesk",
    },
    "ada": {
        "name": "Ada",
        "url": "https://www.ada.cx",
    },
    "yellow.ai": {
        "name": "Yellow.ai",
        "url": "https://www.yellow.ai",
    },
    "sierra": {
        "name": "Sierra AI",
        "url": "https://sierra.ai",
    },
    "decagon": {
        "name": "Decagon",
        "url": "https://decagon.ai",
    },
    "chatfuel": {
        "name": "Chatfuel",
        "url": "https://chatfuel.com",
    },
    "mobilemonkey": {
        "name": "MobileMonkey",
        "url": "https://mobilemonkey.com",
    },
    "engati": {
        "name": "Engati",
        "url": "https://www.engati.com",
    },
    "botmother": {
        "name": "Botmother",
        "url": "https://botmother.com",
    },
    "sleekflow.io": {
        "name": "Sleekflow",
        "url": "https://sleekflow.io",
    },
}


def _normalize(name: str) -> str:
    """小写、去空格、统一别名。"""
    n = name.strip().lower()
    # 去 .io .com .ai .co 等后缀
    for suffix in [".io", ".com", ".ai", ".co", ".app", ".cloud"]:
        if n.endswith(suffix):
            n = n[: -len(suffix)]
    # 去空格 + 特殊字符
    n = re.sub(r"[\s\-_]+", "", n)
    return n


def resolve_competitor(name: str) -> Optional[Dict]:
    """解析单个竞品名 → URL 配置。

    Returns None if not found in builtin.
    """
    norm = _normalize(name)
    for key, info in _BUILTIN_COMPETITORS.items():
        if _normalize(key) == norm or _normalize(info.get("name", "")) == norm:
            return {
                "name": info["name"],
                "canonical_name": info["name"],
                "url": info["url"],
                "features_url": info.get("features") or info["url"],
                "pricing_url": info.get("pricing") or info["url"],
                "docs_url": info.get("docs"),
                "source": "builtin",
                "confidence": 0.95,
            }
    # 域名/URL 直通:非内置竞品按域名构造(历史缺陷:AI编程助手等主题
    # 全部不在内置表 → 整个脚本路径 exit(1),被迫走手工爬取的 buggy 路径)
    n = name.strip()
    looks_like_domain = bool(
        re.match(r"^(https?://)?[\w-]+(\.[\w-]+)+(/.*)?$", n)
    ) and "." in n and " " not in n
    if looks_like_domain:
        url = n if n.startswith("http") else f"https://{n}"
        domain = re.sub(r"^https?://", "", url).split("/")[0]
        base = f"https://{domain}"
        return {
            "name": domain,
            "canonical_name": domain,
            "url": base,
            "features_url": f"{base}/features",
            "pricing_url": f"{base}/pricing",
            "docs_url": f"{base}/docs",
            "source": "domain-guess",
            "confidence": 0.4,
            "note": "非内置竞品:pricing/features/docs 为常规路径猜测,404 时由导航发现兜底",
        }
    return None


def resolve_competitors(names: List[str]) -> Dict[str, Dict]:
    """批量解析。返回 {original_name: resolved_info | {'error': 'not_found'}}。"""
    result = {}
    for n in names:
        n = n.strip()
        if not n:
            continue
        info = resolve_competitor(n)
        if info:
            result[n] = info
        else:
            result[n] = {
                "name": n,
                "canonical_name": n,
                "url": "",
                "features_url": "",
                "pricing_url": "",
                "docs_url": None,
                "source": "unknown",
                "confidence": 0.0,
                "error": f"未找到 '{n}',请补充到 _BUILTIN_COMPETITORS",
            }
    return result


def list_known_competitors() -> List[str]:
    """返回所有内置竞品名(按字母排序)。"""
    return sorted({info["name"] for info in _BUILTIN_COMPETITORS.values()})


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        names = sys.argv[1].split(",")
        result = resolve_competitors(names)
        import json as _json

        print(_json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(
            "用法: python3 competitor_resolver.py 'ycloud,sleekflow,wati,respond.io,meetbot'"
        )
        print()
        print(f"内置竞品 ({len(list_known_competitors())} 个):")
        for n in list_known_competitors():
            print(f"  - {n}")
