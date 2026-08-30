# -*- coding: utf-8 -*-
"""youzi scripts 包(fetch 取证 / deep_link 深链 / run_youzi 编排 / sufficiency 契约)。

2026-08-30 补 __init__:此前靠命名空间包工作,模块可同时以
`sufficiency` 与 `scripts.sufficiency` 两种名字被解析 —— mypy 报
"found twice",且理论上存在双模块实例的状态分裂风险。显式包标记
后模块名唯一(运行时 import 行为不变,直接 `python3 scripts/xxx.py`
仍可用)。
"""
