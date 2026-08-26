#!/usr/bin/env python3
"""verify 的离线门禁集合(G1-G6)。每个 gate 函数签名:
    def gX(analysis: dict, manifest: dict, engine_index: dict, rep: Report) -> None
"""

from verify import Report

_GATES = []


def register(fn):
    _GATES.append(fn)
    return fn


def run_all(analysis, manifest, engine_index, rep: Report):
    for g in _GATES:
        g(analysis, manifest, engine_index, rep)
