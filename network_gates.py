#!/usr/bin/env python3
"""verify 的网络门禁(N1/N2)。仅 --network 时启用。

ponytail: urllib + 串行限速(~1 req/s)足够 —— N1 的对象是几十个 URL,
并发加速收益 < 反爬风险。N2 用裸 HTML 剥标签做容错匹配,不引引擎。
"""

import html
import re
import ssl
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from gates import _registrable_domain, iter_evidence_urls
from verify import Report, norm_ws

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# python.org 官方构建不带系统 CA(urllib 裸跑 SSL 必失败);requests 系
# 爬虫自带 certifi —— 有就复用,没有退回系统默认
try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = None


def fetch_url(url: str, timeout: int = 10, retries: int = 1) -> dict:
    """GET 一个 URL,返回 {ok, http_status, final_url, error}。

    浏览器 UA + 1 次重试;3xx 跟随(urllib 默认),final_url 记录落点。
    """
    last = {"ok": False, "http_status": 0, "final_url": "", "error": ""}
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _UA})
            kw = {"context": _SSL_CTX} if _SSL_CTX else {}
            with urllib.request.urlopen(req, timeout=timeout, **kw) as resp:
                resp.read(65536)  # 只确认可达,不全量下载
                return {
                    "ok": 200 <= resp.status < 300,
                    "http_status": resp.status,
                    "final_url": resp.geturl(),
                    "error": "",
                }
        except urllib.error.HTTPError as e:
            last = {"ok": False, "http_status": e.code, "final_url": url,
                    "error": f"HTTP {e.code}"}
        except Exception as e:
            last = {"ok": False, "http_status": 0, "final_url": url,
                    "error": f"{type(e).__name__}: {e}"}
        if attempt < retries:
            time.sleep(1.0)
    return last


def _collect_urls(analysis, manifest) -> list:
    """被交付 claim 实际引用的证据 URL(去重保序)。"""
    urls = []
    for competitor in analysis.get("competitors") or []:
        for u in iter_evidence_urls(competitor):
            if u not in urls:
                urls.append(u)
    return urls


def run_all(analysis, manifest, rep: Report, sample=None):
    urls = _collect_urls(analysis, manifest)
    if sample is not None and sample >= 0:
        urls = urls[: max(sample, 0)]
    rep.counters["urls_checked"] = len(urls)

    last_t = 0.0
    for url in urls:
        # 限速 ~1 req/s
        dt = time.time() - last_t
        if dt < 1.0:
            time.sleep(1.0 - dt)
        last_t = time.time()

        r = fetch_url(url)
        if not r["ok"]:
            rep.hard(
                "N1", "source_url", url,
                f"回访不可达({r['error'] or r['http_status']})",
                "来源已失效:删除该字段或换可达来源;诚实标注「未验证」可交付",
            )
            continue
        src_domain = _registrable_domain(urlparse(url).hostname or "")
        final_domain = _registrable_domain(urlparse(r["final_url"]).hostname or "")
        if src_domain and final_domain and src_domain != final_domain:
            rep.warn("N1", "source_url",
                     f"{url} 跨域重定向到 {r['final_url']}(内容归属需人工确认)")

    # N2 quote 实时复核:只对 gtm/moat 的 quote 抽查(页面漂移常态,
    # 权威比对是离线 G2,故 N2 仅警告)
    n2_checked = 0
    for competitor in analysis.get("competitors") or []:
        name = competitor.get("name", "?")
        for key in ("gtm_evidence", "moat_evidence"):
            for i, ev in enumerate(competitor.get(key) or []):
                if n2_checked >= 5:  # ponytail: 固定抽查 5 条,够信号
                    break
                if not (isinstance(ev, dict) and ev.get("quote") and ev.get("source")):
                    continue
                n2_checked += 1
                quote, src = ev["quote"], ev["source"]
                try:
                    req = urllib.request.Request(src, headers={"User-Agent": _UA})
                    kw2 = {"context": _SSL_CTX} if _SSL_CTX else {}
                    with urllib.request.urlopen(req, timeout=10, **kw2) as resp:
                        body = resp.read(200000).decode("utf-8", "ignore")
                except Exception:
                    continue  # 可达性已由 N1 报告
                text = norm_ws(html.unescape(re.sub(r"<[^>]+>", " ", body)))
                if norm_ws(quote) not in text:
                    rep.warn("N2", f"competitors[{name}].{key}[{i}]",
                             f"quote 在当前页面已不可见(可能已更新): “{quote[:50]}…”")
