#!/usr/bin/env python3
"""tests/test_e2e_real.py · 真实网络 e2e 验收(发布前手动跑)。

跑完整 V2 管线:爬取(wati/respond.io/ycloud)→ 证据包 → **证据落地构建
03-analysis.json**(V2 起 Step 3 是 LLM 的工作,本测试用同款铁律的
程序化替身:每条 quote 写入前用 gates._quote_grep 预验证、定价 tier 只
取跨引擎交叉验证票、锚点避开域名根 G7)→ 渲染 → verify 离线门禁 +
网络门禁。全部硬门禁绿灯 = 生产级验收通过。

历史:2026-08-27 V2 重构删除脚本侧语义提取后,本测试仍期望 fetch.py
直接产出 03-analysis.json(V1 契约)→ network 标记默认 deselect 从未
执行,静默腐烂。2026-08-30 第 12 轮审计发现并重写。

运行:
    python3 -m pytest tests/test_e2e_real.py -m network -v

产物固定落在 /tmp/youzi-e2e-acceptance/(便于冻结为离线 fixture)。
"""

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from urllib.parse import urlparse

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

E2E_DIR = Path("/tmp/youzi-e2e-acceptance")


def build_analysis_from_evidence(out_dir: Path, topic: str) -> Path:
    """Step 3 程序化替身:从新鲜 02-raw 构造 gate 合规的 03-analysis.json。

    铁律与 LLM Step 3 相同:quote 逐字可 grep(G2 同款预验证)、定价只取
    交叉验证票、G7 锚点避开域名根。gaps/opportunities 为占位文本
    (验收目标是管线与门禁,不是内容质量)。
    """
    from gates import _quote_grep
    from pricing_tokens import PRICE_TOKEN_RX
    from scripts import fetch as fetch_mod

    manifest = json.loads((out_dir / "claims-manifest.json").read_text())
    fetched = manifest["fetched"]
    engine_index: dict = {}
    for f in sorted((out_dir / "02-raw").glob("*.engines.json")):
        for url, engines in json.loads(f.read_text()).items():
            engine_index.setdefault(url, {}).update(engines or {})

    def kinds_of(url):
        ent = fetched.get(url) or {}
        return ent.get("kinds") or ([ent.get("kind")] if ent.get("kind") else [])

    def url_of(doc, kind, require_path=False):
        cands = [u for u in doc if kind in kinds_of(u)]
        if require_path:
            cands = [u for u in cands if urlparse(u).path not in ("", "/")]
        return cands[0] if cands else None

    def pick_quote(url, lo=40, hi=95):
        for md in (engine_index.get(url) or {}).values():
            for ln in md.split("\n"):
                s = " ".join(ln.strip().split())
                if not (lo <= len(s) <= hi):
                    continue
                if re.search(r"cookie|隐私|privacy|©|login|javascript", s, re.I):
                    continue
                if PRICE_TOKEN_RX.search(s):
                    continue
                if len(re.findall(r"[一-鿿 a-zA-Z]", s)) < 20:
                    continue
                if _quote_grep(s, url, engine_index):
                    return s
        return None

    tier_rx = re.compile(
        r"\b(free|starter|growth|pro|business|enterprise|premium|plus|team)\b", re.I
    )
    comps = []
    for fp in sorted((out_dir / "02-raw").glob("*.engines.json")):
        doc = json.loads(fp.read_text())
        disp = fp.stem.removesuffix(".engines").split("_")[0]
        home = url_of(doc, "homepage") or url_of(doc, "pricing")
        purl = url_of(doc, "pricing")
        if not home:
            continue
        feat = (
            url_of(doc, "features", True)
            or url_of(doc, "docs", True)
            or url_of(doc, "about", True)
        )
        votes = []
        if purl:
            votes = [
                v
                for v in fetch_mod.vote_price_lines(
                    [
                        {"success": True, "scraper": e, "markdown": m}
                        for e, m in (engine_index.get(purl) or {}).items()
                    ]
                )
                if v["independent_votes"] >= 2
            ]
        tiers, vote_detail, used = [], [], set()
        for v in votes[:4]:
            line = None
            for md in (engine_index.get(purl) or {}).values():
                for ln in md.split("\n"):
                    if v["token"] in ln and " ".join(ln.split()) not in used:
                        line = " ".join(ln.split())
                        break
                if line:
                    break
            if not line:
                continue
            used.add(line)
            m = tier_rx.search(line)
            tiers.append(
                {
                    "name": m.group(1).title() if m else f"档位{len(tiers) + 1}",
                    "price": v["token"],
                    "billing_period": "/mo"
                    if re.search(r"/?\s*mo(nth)?|月", line, re.I)
                    else ("/yr" if re.search(r"ye?ar|annu|年", line, re.I) else "—"),
                    "features": [],
                    "source_url": purl,
                }
            )
            vote_detail.append(
                {
                    "line": line[:80],
                    "raw_line": line,
                    "engines": v["engines"],
                    "independent_votes": v["independent_votes"],
                }
            )
        hashes = (
            {
                h.get("content_hash")
                for h in ((fetched.get(purl) or {}).get("engines") or {}).values()
                if h.get("content_hash")
            }
            if purl
            else set()
        )
        strengths = []
        for src in dict.fromkeys(u for u in [feat, url_of(doc, "about", True)] if u):
            q = pick_quote(src)
            if q:
                strengths.append(
                    {
                        "point": q[:24] + "…",
                        "evidence": f'官网原文: "{q}"',
                        "score": 7,
                        "source": src,
                    }
                )
            if len(strengths) >= 2:
                break
        fc = []
        for u in doc:
            if urlparse(u).path in ("", "/"):
                continue
            if not any(k in kinds_of(u) for k in ("features", "docs", "about")):
                continue
            q = pick_quote(u, 8, 30)
            if q:
                fc.append(
                    {"category": "核心能力", "name": q[:16], "desc": "", "source": u}
                )
            if len(fc) >= 2:
                break
        comps.append(
            {
                "name": disp,
                "url": f"https://{urlparse(home).netloc}",
                "tagline": (pick_quote(home, 12, 40) or "竞品官网")[:25],
                "tagline_source": home,
                "stage": "成长期",
                "target_users": ["企业客服团队"],
                "core_features": [x["name"] for x in fc][:6],
                "feature_catalog": {disp: fc},
                "pricing": (
                    f"{tiers[0]['name']} · {tiers[0]['price']} "
                    f"{tiers[0]['billing_period']}"
                    if tiers
                    else "未验证"
                ),
                "pricing_verified": bool(tiers) and len(hashes) >= 2,
                "pricing_source": purl,
                "pricing_scraped_at": (fetched.get(purl) or {}).get("fetched_at", ""),
                "pricing_engines": sorted({e for v in votes for e in v["engines"]}),
                "pricing_tiers": tiers,
                "pricing_vote_detail": vote_detail,
                "strengths": strengths,
                "weaknesses": [],
                "scores": {
                    "feature_richness": 7,
                    "ux": 7,
                    "pricing_value": 7,
                    "integration": 7,
                    "ai_capability": 6,
                    "momentum": 7,
                },
            }
        )
    analysis = {
        "topic": topic,
        "subtitle": "真实网络 e2e 验收",
        "date": "2026-08-30",
        "competitors": comps,
        "market_segments": [{"name": topic, "competitors": [c["name"] for c in comps]}],
        "gaps": [{"title": f"验收占位 {i}", "detail": "e2e 管线测试"} for i in "AB"],
        "opportunities": [
            {
                "title": f"验收机会 {i}",
                "inspiration": "t",
                "target_users": "t",
                "differentiation": "t",
                "validation": "t",
                "disrupt_score": 8 - i,
            }
            for i in range(3)
        ],
        "executive_summary": f"{topic}:真实网络 e2e 验收(取证-渲染-门禁)。",
    }
    out = out_dir / "03-analysis.json"
    out.write_text(json.dumps(analysis, ensure_ascii=False, indent=1), encoding="utf-8")
    return out


@pytest.mark.network
class TestE2EReal(unittest.TestCase):
    """真实数据验收:全管线 + verify --network。耗时 3-6 分钟。"""

    def test_full_pipeline_real_competitors(self):
        from verify import verify_analysis

        if E2E_DIR.exists():
            shutil.rmtree(E2E_DIR)
        E2E_DIR.mkdir(parents=True)

        # Step 2: 爬取 + 证据包(V2:fetch 只取证,不产 03-analysis)
        r = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "fetch.py"),
                "--competitors",
                "wati,respond.io,ycloud",
                "--out-dir",
                str(E2E_DIR),
                "--budget",
                "150",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=600,
        )
        self.assertEqual(r.returncode, 0, f"crawl failed:\n{r.stdout}\n{r.stderr}")
        self.assertTrue((E2E_DIR / "claims-manifest.json").exists(), "证据包未落盘")
        self.assertTrue((E2E_DIR / "02-raw").exists(), "02-raw 未落盘")

        # Step 3(程序化替身):证据落地构建分析
        out = build_analysis_from_evidence(E2E_DIR, "WhatsApp BSP e2e 验收")
        analysis = json.loads(out.read_text(encoding="utf-8"))
        self.assertGreaterEqual(
            len(analysis["competitors"]), 2, "真实爬取至少 2 家成功"
        )

        # Step 4: 渲染
        html = E2E_DIR / "report.html"
        r2 = subprocess.run(
            [
                sys.executable,
                str(ROOT / "render.py"),
                "--input",
                str(out),
                "--output",
                str(html),
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=300,
        )
        self.assertIn(r2.returncode, (0, 2), r2.stdout + r2.stderr)

        # Step 5: verify 离线门禁(必须全绿)
        rep = verify_analysis(
            out,
            E2E_DIR / "claims-manifest.json",
            E2E_DIR / "02-raw",
            report_path=E2E_DIR / "verify-report.json",
        )
        self.assertTrue(
            rep["passed"],
            "离线硬门禁失败:\n"
            + json.dumps(rep["violations"], ensure_ascii=False, indent=1),
        )

        # 网络门禁:N1 可达性(真实回访被引用 URL)
        rep_net = verify_analysis(
            out,
            E2E_DIR / "claims-manifest.json",
            E2E_DIR / "02-raw",
            network=True,
            sample=10,
        )
        self.assertFalse(
            [v for v in rep_net["violations"] if v["gate"] == "N1"],
            "N1 死链:\n"
            + json.dumps(
                [v for v in rep_net["violations"] if v["gate"] == "N1"],
                ensure_ascii=False,
                indent=1,
            ),
        )
