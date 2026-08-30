#!/usr/bin/env python3
"""tests 全局隔离:fetch 的进程级副作用不得泄漏进真实 storage/网络。

事故(2026-08-29 自查发现):旧 fetch 测试用 mock 的 wati 定价页触发
_cache_put,把【假数据】写进真实 storage/pricing-cache.json —— 之后
真实运行 wati 全灭时会回退到这份假缓存,违反「绝不伪造」原则。
_robots_allowed 同理会在离线测试环境发真实网络请求(5s 超时拖慢套件)。
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_fetch_side_effects(monkeypatch, tmp_path):
    from scripts import fetch as fetch_mod

    # 定价缓存 → 测试沙箱(真实 storage/pricing-cache.json 不被触碰)
    monkeypatch.setattr(
        fetch_mod, "_PRICING_CACHE_PATH", tmp_path / "pricing-cache.json"
    )
    # robots:默认按允许处理,不发网络(具体行为测试自行覆盖 _robots_disallows)
    monkeypatch.setattr(fetch_mod, "_ROBOTS_CACHE", {})
    monkeypatch.setattr(fetch_mod, "_robots_disallows", lambda url: [])
