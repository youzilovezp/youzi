#!/usr/bin/env python3
"""
crawl_summarize · 把 02-raw markdown 自动转成结构化数据

输入：02-raw/{name}.md 文件
输出：JSON（合并到现有分析 JSON 或独立输出）

提取字段（基于关键词 + 简单正则）：
- pricing_keywords        ($xx/月 / 免费 / 报价 / trial)
- pricing_tier_count      tier 数量
- feature_keywords        (API / integration / AI / automation 等)
- target_user_hints       (enterprise / SMB / startup 等)
- tech_keywords           (Python / Node / K8s / React 等)
- content_stats           (字数 / 段落数 / 链接数)
- raw_urls                提取到的所有 URL
- headings                H1/H2/H3 标题列表

用法：
    python3 scripts/crawl_summarize.py \
        --raw-dir /tmp/youzi-out/whatsapp-ads/02-raw \
        --output /tmp/whatsapp-crawl-summary.json

    # 或合并到现有分析 JSON：
    python3 scripts/crawl_summarize.py \
        --raw-dir /tmp/youzi-out/whatsapp-ads/02-raw \
        --merge-into examples/whatsapp-advertising-demo.json \
        --output examples/whatsapp-advertising-augmented.json
"""

import argparse
import json
import re
import sys
from pathlib import Path

# 关键词词典
PRICING_PATTERNS = [
    r"\$\d+(?:\.\d+)?(?:/mo|/month|/yr|/year|月|年)?",
    r"€\d+(?:\.\d+)?",
    r"¥\d+(?:\.\d+)?",
    r"￥\d+",
    r"免费|Free",
    r"(?:联系|Contact)\s*(?:销售|sales)",
    r"trial|免费试用|Quote|报价",
    r"Pro\s*\$\d+|Enterprise\s*\$\d+|Business\s*\$\d+",
    r"按.*计费|按.*付费|包月|订阅",
]
FEATURE_KEYWORDS = [
    "API",
    "integration",
    "AI",
    "automation",
    "workflow",
    "chatbot",
    "analytics",
    "CDP",
    "SDK",
    "dashboard",
    "automation",
    "broadcast",
    "template",
    "broadcast",
    "SDK",
    "webhook",
    "REST",
    "GraphQL",
    "OAuth",
    "machine learning",
    "real-time",
    "no-code",
    "low-code",
    "AI Agent",
    "GPT",
    "multi-channel",
    "omnichannel",
    "Shopify",
    "Salesforce",
    "HubSpot",
    "Zendesk",
]
USER_HINTS = {
    "enterprise": [
        "enterprise",
        "Fortune 500",
        "Global 2000",
        "大企业",
        "B2B",
        "金融机构",
    ],
    "smb": [
        "SMB",
        "中小企业",
        "small business",
        "startup",
        "中小商家",
        "growing business",
    ],
    "developer": ["developer", "API", "SDK", "工程师", "devs", "编程"],
    "ecommerce": ["ecommerce", "电商", "Shopify", "WooCommerce", "online store", "DTC"],
    "agency": ["agency", "代运营", "marketing agency"],
    "consumer": ["consumer", "C 端", "用户", "个人"],
    "enterprise_it": ["IT", "DevOps", "system admin"],
}
TECH_KEYWORDS = [
    "Python",
    "Node.js",
    "Java",
    "Go",
    "Ruby",
    ".NET",
    "PHP",
    "React",
    "Vue",
    "Angular",
    "Kubernetes",
    "K8s",
    "Docker",
    "PostgreSQL",
    "MongoDB",
    "Redis",
    "MySQL",
    "AWS",
    "Azure",
    "GCP",
    "阿里云",
    "腾讯云",
    "GraphQL",
    "REST",
    "Webhook",
    "gRPC",
    "WebSocket",
    "TLS",
    "OAuth",
    "JWT",
    "GPT-4",
    "Claude",
    "LLM",
    "RAG",
    "CRDT",
    "OpenAPI",
    "SDK",
    "Twilio",
    "Segment",
    "Shopify",
]


def extract_pricing(text: str) -> list:
    """从 markdown 文本里提取定价关键词。"""
    hits = set()
    for pat in PRICING_PATTERNS:
        for m in re.finditer(pat, text, re.IGNORECASE):
            hits.add(m.group(0).strip())
    return sorted(hits)[:10]


def extract_feature_keywords(text: str) -> list:
    """提取功能关键词命中数（按降序排序，≥2 次出现才保留）。"""
    counts: dict = {}
    for kw in FEATURE_KEYWORDS:
        n = len(re.findall(rf"\b{re.escape(kw)}\b", text, re.IGNORECASE))
        if n >= 2:
            counts[kw] = n
    return sorted(counts.items(), key=lambda x: -x[1])


def detect_target_users(text: str) -> list:
    """基于关键词命中数推断目标用户。"""
    hits = []
    for seg, kws in USER_HINTS.items():
        score = sum(
            len(re.findall(rf"\b{re.escape(k)}\b", text, re.IGNORECASE))
            for kw in kws
            for k in [kw]
        )
        if score > 0:
            hits.append((seg, score))
    return [seg for seg, _ in sorted(hits, key=lambda x: -x[1])[:5]]


def extract_tech(text: str) -> list:
    """提取技术栈关键词。"""
    hits = set()
    for kw in TECH_KEYWORDS:
        if re.search(rf"\b{re.escape(kw)}\b", text, re.IGNORECASE):
            hits.add(kw)
    return sorted(hits)


def extract_headings(text: str) -> list:
    """提取 H1/H2/H3 标题。"""
    heads = []
    for m in re.finditer(r"^(#{1,3})\s+(.+)$", text, re.MULTILINE):
        level = len(m.group(1))
        title = m.group(2).strip()
        if len(title) > 4 and len(title) < 100:
            heads.append({"level": level, "title": title})
    return heads[:30]


def extract_urls(text: str) -> list:
    """提取所有 URL（去重）。"""
    urls = set()
    for m in re.finditer(r"https?://[^\s\)\]\"<>]+", text):
        urls.add(m.group(0).rstrip(".,;:!?"))
    return sorted(urls)[:40]


def _filter_noise(text: str) -> str:
    """去掉 cookie banner、语言切换、footer 等噪音，保留真实正文。"""
    import re as _re

    # 去掉常见噪音段
    noise_patterns = [
        r"About Cookies.*?(?=\n\n[A-Z#])",
        r"We use cookies.*?(?=\n\n[A-Z#])",
        r"Functional Functional.*?(?=\n\n[A-Z#])",
        r"Preferences Preferences.*?(?=\n\n[A-Z#])",
        r"Analytics Analytics.*?(?=\n\n[A-Z#])",
        r"Marketing Marketing.*?(?=\n\n[A-Z#])",
        r"\[ Português \].*?\[ عربي \]",
        r"Manage options.*?(?=\n)",
        r"Read more about these purposes.*?(?=\n)",
    ]
    for pat in noise_patterns:
        text = _re.sub(pat, "", text, flags=_re.DOTALL | _re.IGNORECASE)
    return text


def extract_features_from_text(text: str, max_features: int = 30) -> list:
    """从正文中提取功能候选。

    策略：
    1. 加粗的标题性短语（**xxx** 或 **xxx：**）
    2. H2/H3 后的短描述
    3. 链接文本中带 "Feature / 功能 / Solution / Capability" 关键词的
    """
    import re as _re

    text = _filter_noise(text)
    features = []
    seen = set()

    # 1. 加粗短语 (含功能名)
    for m in _re.finditer(r"\*\*([^*\n]{3,60})\*\*", text):
        f = m.group(1).strip()
        # 过滤常见噪音
        skip_words = [
            "Solutions",
            "Teams",
            "Industries",
            "Resources",
            "Log in",
            "Sign up",
            "Get Started",
            "See all",
        ]
        if any(s.lower() in f.lower() for s in skip_words):
            continue
        if len(f) < 4 or len(f) > 60:
            continue
        if not _re.search(r"[a-zA-Z一-鿿]", f):
            continue
        if f not in seen:
            seen.add(f)
            features.append({"name": f, "desc": "", "source": "加粗短语"})

    # 2. H2/H3 后的短描述（功能分类标题）
    for m in _re.finditer(
        r"^#{2,4}\s+([^\n]{4,80})\n+([^\n#]{10,200})",
        text,
        _re.MULTILINE,
    ):
        title = m.group(1).strip()
        desc = m.group(2).strip()
        # 过滤
        if len(title) < 4 or len(desc) < 10:
            continue
        skip_phrases = [
            "Get Started",
            "Pricing",
            "Contact",
            "Login",
            "Sign",
            "Try",
            "Demo",
            "Watch",
            "Read",
        ]
        if any(s in title for s in skip_phrases):
            continue
        # 截断描述到 100 字
        if len(desc) > 100:
            desc = desc[:100].rsplit(" ", 1)[0] + "..."
        if title not in seen:
            seen.add(title)
            features.append({"name": title, "desc": desc, "source": "H2/H3 标题"})

    return features[:max_features]


def summarize_file(path: Path) -> dict:
    """汇总一个 markdown 文件的结构化信息。"""
    text = path.read_text(encoding="utf-8", errors="ignore")
    filtered = _filter_noise(text)
    return {
        "file": path.name,
        "size_kb": round(len(text.encode("utf-8")) / 1024, 1),
        "char_count": len(text),
        "char_count_filtered": len(filtered),
        "paragraph_count": len([p for p in filtered.split("\n\n") if p.strip()]),
        "link_count": len(re.findall(r"https?://", text)),
        "headings": extract_headings(text),
        "pricing_keywords": extract_pricing(text),
        "feature_keywords": [kw for kw, _ in extract_feature_keywords(text)][:15],
        "extracted_features": extract_features_from_text(text),
        "target_user_hints": detect_target_users(text),
        "tech_keywords": extract_tech(text),
        "urls_found": extract_urls(text),
    }


def merge_into_analysis(analysis: dict, summaries: dict) -> dict:
    """把爬取摘要合并到分析 JSON 的对应 competitor。"""
    # 建立 name → summary 的映射（按文件名前缀匹配）
    name_to_summary = {}
    for name, summary in summaries.items():
        name_to_summary[name.lower()] = summary

    for c in analysis.get("competitors", []):
        key = c["name"].lower().replace(".", "").replace(" ", "").replace("_", "")
        matched = None
        for k, v in name_to_summary.items():
            if k == key or key.startswith(k) or k.startswith(key):
                matched = v
                break
        if matched:
            # 不覆盖已有，只补充缺失字段
            c.setdefault("_crawl_summary", matched)
            # 补充 pricing_tiers（如果 competitors[i].pricing 没切好）
            if not c.get("pricing_tiers") and matched["pricing_keywords"]:
                c["pricing_tiers"] = matched["pricing_keywords"]
            # 补充 target_users_crawl
            if matched["target_user_hints"]:
                c.setdefault("target_users_crawl_hints", matched["target_user_hints"])
            # 补充 tech_signals_crawl
            if matched["tech_keywords"]:
                c.setdefault("tech_signals_crawl", matched["tech_keywords"])
    return analysis


def main():
    ap = argparse.ArgumentParser(description="crawl_summarize")
    ap.add_argument("--raw-dir", required=True, help="02-raw 目录")
    ap.add_argument("--output", help="输出 JSON 路径")
    ap.add_argument("--merge-into", help="合并到现有分析 JSON")
    args = ap.parse_args()

    raw_dir = Path(args.raw_dir)
    if not raw_dir.exists():
        print(f"❌ raw-dir not found: {raw_dir}", file=sys.stderr)
        sys.exit(1)

    # 收集所有 .md 文件
    md_files = sorted(raw_dir.glob("*.md"))
    if not md_files:
        print(f"❌ no .md files in {raw_dir}", file=sys.stderr)
        sys.exit(1)

    print(f"📂 汇总 {len(md_files)} 个文件 in {raw_dir}\n")
    summaries = {}
    for f in md_files:
        name = f.stem
        s = summarize_file(f)
        summaries[name] = s
        print(
            f"  ✓ {name:20s} {s['size_kb']:6.1f} KB | "
            f"{s['paragraph_count']:3d} ¶ | "
            f"{len(s['headings']):3d} H | "
            f"{len(s['pricing_keywords']):2d} pricing | "
            f"{len(s['feature_keywords']):2d} feat | "
            f"{len(s['tech_keywords']):2d} tech"
        )

    # 输出
    if args.merge_into:
        # 合并到现有分析 JSON
        analysis_path = Path(args.merge_into)
        analysis = json.loads(analysis_path.read_text(encoding="utf-8"))
        analysis = merge_into_analysis(analysis, summaries)
        if args.output:
            out = Path(args.output)
            out.write_text(
                json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"\n✅ 已合并并写入: {out}")
        else:
            print(f"\n✅ 已合并到: {analysis_path} (in-place)")
    else:
        if args.output:
            out = Path(args.output)
            out.write_text(
                json.dumps(summaries, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"\n✅ 摘要写入: {out}")
        else:
            print(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
