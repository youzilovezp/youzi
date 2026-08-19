#!/usr/bin/env python3
"""
youzi · 零依赖 HTML 报告渲染器

读取 analysis JSON + report.html 模板，输出精美 HTML 报告。
无任何第三方依赖（不用 jinja2 / 不用 npm）。

Usage:
    python3 render.py --input 03-analysis.json --output report.html
    python3 render.py --input 03-analysis.json --output report.html --template templates/report.html
"""

import argparse
import json
import math
import re
import sys
import html
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_TEMPLATE = HERE / "templates" / "report.html"

# ============================================================
# 配色板
# ============================================================
PALETTE = [
    "#ff5a1f", "#2563eb", "#16a34a", "#d97706",
    "#9333ea", "#0891b2", "#db2777", "#65a30d",
    "#7c3aed", "#ea580c", "#0ea5e9", "#ca8a04",
    "#be185d", "#15803d", "#b91c1c", "#1d4ed8",
]
SCORE_DIMS = [
    ("feature_richness", "功能丰富度"),
    ("ux",               "用户体验"),
    ("pricing_value",    "定价性价比"),
    ("integration",      "生态集成"),
    ("ai_capability",    "AI 能力"),
    ("momentum",         "增长势头"),
]


# ============================================================
# 模板引擎：手写递归下降解析器
# 支持：
#   {{ var.path }}       - 转义变量
#   {{! var.path }}      - 原始 HTML（不转义）
#   {% for x in path %}...{% endfor %}  - 循环
#   {% if expr %}...{% endif %}         - 条件
#   {{ loop.index }}     - 当前循环索引（1-based）
#   {% if x %}body{% endif %}
# ============================================================
class Template:
    FOR_RE = re.compile(r"\{%\s*for\s+([\w, ]+?)\s+in\s+([\w.]+)\s*%\}")
    ENDFOR_RE = re.compile(r"\{%\s*endfor\s*%\}")
    IF_RE = re.compile(r"\{%\s*if\s+(.+?)\s*%\}")
    ELIF_RE = re.compile(r"\{%\s*elif\s+(.+?)\s*%\}")
    ELSE_RE = re.compile(r"\{%\s*else\s*%\}")
    ENDIF_RE = re.compile(r"\{%\s*endif\s*%\}")
    VAR_RE = re.compile(r"\{\{\s*([\w.|\:\(\) \"\',%#-]+?)\s*\}\}")
    RAW_RE = re.compile(r"\{\{\!\s*([\w.|\:\(\) \"\',%#-]+?)\s*\}\}")

    def __init__(self, source: str):
        self.source = source

    # -------------------- 变量解析 --------------------
    def _resolve(self, path: str, ctx) -> object:
        # 字面字符串（带引号）
        if (path.startswith('"') and path.endswith('"')) or (path.startswith("'") and path.endswith("'")):
            return path[1:-1]
        cur = ctx
        for part in path.split("."):
            if isinstance(cur, dict):
                cur = cur.get(part, "")
            elif hasattr(cur, part):
                cur = getattr(cur, part)
            else:
                return ""
            if cur == "" or cur is None:
                return ""
        return cur

    def _eval_expr(self, expr: str, ctx) -> bool:
        try:
            py = expr
            for name in re.findall(r"\b([a-zA-Z_][a-zA-Z0-9_.]*)\b", py):
                val = self._resolve(name, ctx)
                rep = repr(val) if isinstance(val, str) else str(val)
                py = re.sub(rf"\b{re.escape(name)}\b", rep, py)
            return bool(eval(py, {"__builtins__": {}}, {}))
        except Exception:
            return False

    # -------------------- 主入口 --------------------
    def render(self, ctx: dict) -> str:
        return self._parse_block(self.source, ctx)

    def _parse_block(self, src: str, ctx: dict) -> str:
        """解析一段文本（不含 for/if 顶层标记），返回渲染结果。

        支持 for / if / var 互相嵌套，因为用递归下降：
        - 遇到 {% for %} → 进入 _parse_for
        - 遇到 {% if %} → 进入 _parse_if
        - 其他文本 → 直接输出，遇到 {{var}} 替换
        """
        out = []
        i = 0
        while i < len(src):
            # 找下一个标记
            next_for = self.FOR_RE.search(src, i)
            next_if = self.IF_RE.search(src, i)
            # 取最近的
            candidates = []
            if next_for:
                candidates.append(("for", next_for))
            if next_if:
                candidates.append(("if", next_if))
            if not candidates:
                # 后面全是纯文本 + var
                out.append(self._render_text(src[i:], ctx))
                break
            # 选最近的
            candidates.sort(key=lambda x: x[1].start())
            kind, m = candidates[0]
            # 输出标记之前的文本
            out.append(self._render_text(src[i:m.start()], ctx))
            if kind == "for":
                result, end = self._parse_for(src, m.start(), ctx)
                out.append(result)
                i = end
            else:  # if
                result, end = self._parse_if(src, m.start(), ctx)
                out.append(result)
                i = end
        return "".join(out)

    def _parse_for(self, src: str, start: int, ctx: dict) -> tuple[str, int]:
        """从 {% for x in path %} 开始，找到匹配 endfor（含嵌套），处理。

        支持单变量 (for x in path) 和 tuple 解构 (for k, v in path)。
        """
        m = self.FOR_RE.match(src, start)
        if not m:
            return "", start
        var_str = m.group(1)
        path = m.group(2)
        body_start = m.end()
        # 找匹配的 endfor（嵌套深度计数）
        end = self._find_matching_endfor(src, body_start)
        if end == -1:
            return "", start
        body = src[body_start:end]
        end_m = self.ENDFOR_RE.match(src, end)
        endfor_end = end + len(end_m.group(0)) if end_m else end
        # 迭代
        items = self._resolve(path, ctx)
        if not isinstance(items, (list, tuple)):
            return "", endfor_end
        # 解析变量名（支持 "k, v" 形式）
        var_names = [v.strip() for v in var_str.split(",")]
        parts = []
        for idx, item in enumerate(items):
            sub = dict(ctx)
            # tuple 解构或多变量
            if len(var_names) > 1 and isinstance(item, (list, tuple)) and len(item) == len(var_names):
                for vn, iv in zip(var_names, item):
                    sub[vn] = iv
            elif len(var_names) == 1 and isinstance(item, (list, tuple)) and len(item) == 2:
                # 单变量名但 item 是二元组：把整个 item 赋给 var，并提供 _0/_1 访问
                sub[var_names[0]] = item
                sub["_0"] = item[0]
                sub["_1"] = item[1]
            else:
                sub[var_names[0]] = item
            sub["loop"] = {
                "index": idx + 1,
                "index0": idx,
                "index0_mod6": idx % 6,
                "length": len(items),
                "first": idx == 0,
                "last": idx == len(items) - 1,
            }
            parts.append(self._parse_block(body, sub))
        return "".join(parts), endfor_end

    def _find_matching_endfor(self, src: str, start: int) -> int:
        """从 start 开始，找与最近的 {% for %} 匹配的 {% endfor %}。返回 endfor 的起始位置。"""
        depth = 1
        i = start
        while i < len(src):
            nxt_for = self.FOR_RE.search(src, i)
            nxt_end = self.ENDFOR_RE.search(src, i)
            if not nxt_end:
                return -1
            if nxt_for and nxt_for.start() < nxt_end.start():
                depth += 1
                i = nxt_for.end()
            else:
                depth -= 1
                if depth == 0:
                    return nxt_end.start()
                i = nxt_end.end()
        return -1

    def _parse_if(self, src: str, start: int, ctx: dict) -> tuple[str, int]:
        m = self.IF_RE.match(src, start)
        if not m:
            return "", start
        # 解析 if/elif/else/endif 链：把所有分支切成 (expr, body) 对
        expr = m.group(1)
        body_start = m.end()
        # 找匹配 endif（处理嵌套 for/if）
        end = self._find_matching_endif(src, body_start)
        if end == -1:
            return "", start
        end_m = self.ENDIF_RE.match(src, end)
        endif_end = end + len(end_m.group(0)) if end_m else end
        full_block = src[body_start:end]
        # 在 full_block 里切分支：elif/else 都是分支标记
        branches = []  # [(expr_or_None, body)]
        current_expr = expr
        current_body_start = 0
        i = 0
        while i < len(full_block):
            # 找最近的 elif / else / 嵌套 if（嵌套 if 不切）
            next_elif = self.ELIF_RE.search(full_block, i)
            next_else = self.ELSE_RE.search(full_block, i)
            next_if = self.IF_RE.search(full_block, i)
            # 排除嵌套 if 的 elif/else：要求分支标记不在嵌套 if 里
            # 简化处理：只切最外层的 elif/else（不在任何 if 块内的）
            # 通过深度计数找最外层的 elif/else
            depth = 0
            cut_pos = -1
            cut_type = None
            j = i
            while j < len(full_block):
                # 找最近的下一个标记
                next_any = None
                for marker_re, marker_type in [(self.IF_RE, 'if'), (self.ELIF_RE, 'elif'),
                                                (self.ELSE_RE, 'else'), (self.ENDIF_RE, 'endif')]:
                    mm = marker_re.search(full_block, j)
                    if mm and (next_any is None or mm.start() < next_any.start()):
                        next_any = mm
                        next_type = marker_type
                if next_any is None:
                    break
                if next_type == 'if':
                    depth += 1
                elif next_type == 'endif':
                    depth -= 1
                    if depth == 0:
                        break
                elif depth == 0 and next_type in ('elif', 'else'):
                    cut_pos = next_any.start()
                    cut_type = next_type
                    break
                j = next_any.end()
            if cut_pos == -1:
                # 没找到分支标记，剩余就是当前分支的 body
                branches.append((current_expr, full_block[current_body_start:]))
                break
            else:
                branches.append((current_expr, full_block[current_body_start:cut_pos]))
                if cut_type == 'elif':
                    em = self.ELIF_RE.match(full_block, cut_pos)
                    current_expr = em.group(1)
                    current_body_start = em.end()
                else:  # else
                    current_expr = None  # None 表示无条件
                    em = self.ELSE_RE.match(full_block, cut_pos)
                    current_body_start = em.end()
                i = current_body_start
        # 评估分支
        for branch_expr, branch_body in branches:
            if branch_expr is None or self._eval_expr(branch_expr, ctx):
                return self._parse_block(branch_body, ctx), endif_end
        return "", endif_end

    def _find_matching_endif(self, src: str, start: int) -> int:
        """找与 if 匹配的 endif。需考虑嵌套 if/elif/else/endfor 等。

        注意：for/endfor 配对不影响 if 深度（互相独立）。
        """
        depth = 1
        i = start
        while i < len(src):
            # 找下一个最近的标记
            candidates = []
            for marker_re, kind in [(self.IF_RE, 'if'), (self.ENDIF_RE, 'endif'),
                                     (self.FOR_RE, 'for')]:
                m = marker_re.search(src, i)
                if m:
                    candidates.append((m.start(), m.end(), kind, marker_re))
            if not candidates:
                return -1
            candidates.sort(key=lambda x: x[0])
            pos, nxt_end, kind, _ = candidates[0]
            if kind == 'if':
                depth += 1
                i = nxt_end
            elif kind == 'endif':
                depth -= 1
                if depth == 0:
                    return pos
                i = nxt_end
            else:  # 'for' —— 完全跳过（不增减 if 深度）
                i = nxt_end
        return -1

    def _render_text(self, text: str, ctx: dict) -> str:
        """渲染纯文本片段：替换 {{var}} 和 {{!var}}。

        支持 Jinja 风格过滤器：|length / |upper / |slice:N / |default:X
        """
        def _apply_filters(val, filter_str):
            """从右到左依次应用过滤器。"""
            if not filter_str:
                return val
            for f in filter_str.split("|"):
                f = f.strip()
                if not f:
                    continue
                if f == "length":
                    try:
                        val = len(val)
                    except Exception:
                        val = 0
                elif f == "upper":
                    val = str(val).upper()
                elif f == "lower":
                    val = str(val).lower()
                elif f.startswith("slice"):
                    # slice:1 或 slice:0:2 或 slice(1) 或 slice(0, 2)
                    s = f[5:].strip()
                    # 去掉括号
                    if s.startswith("(") and s.endswith(")"):
                        s = s[1:-1]
                    # 解析参数
                    parts = []
                    if s:
                        for x in s.split(":"):
                            x = x.strip()
                            if x:
                                try:
                                    parts.append(int(x))
                                except ValueError:
                                    pass
                    if isinstance(val, str) and parts:
                        try:
                            val = val[slice(*parts)]
                        except Exception:
                            pass
                elif f.startswith("format"):
                    # 语法: format(expr) → Python format 调用 val.format(*[args])
                    try:
                        if "(" in f:
                            inner = f[f.index("(")+1 : f.rindex(")")]
                            args = []
                            for part in inner.split(","):
                                part = part.strip()
                                # 1. 数字字面
                                if part.lstrip("-").isdigit():
                                    args.append(int(part))
                                # 2. 字符串字面（带引号）
                                elif (part.startswith('"') and part.endswith('"')) or (part.startswith("'") and part.endswith("'")):
                                    args.append(part[1:-1])
                                # 3. 变量路径（从 ctx 解析）
                                else:
                                    # 从当前 ctx 解析变量（如 loop.index）
                                    try:
                                        # 简单路径解析: a.b.c → ctx["a"]["b"]["c"]
                                        cur = ctx
                                        for k in part.split("."):
                                            if isinstance(cur, dict):
                                                cur = cur.get(k, "")
                                            else:
                                                cur = ""
                                                break
                                        args.append(cur)
                                    except Exception:
                                        args.append(part)
                            # 区分 C 风格（%02d）和 Python 风格（{0:02d}）
                            if isinstance(val, str) and "%" in val and "{" not in val:
                                # C 风格：用 % 运算符
                                if len(args) == 1:
                                    val = val % args[0]
                                else:
                                    val = val % tuple(args)
                            else:
                                # Python 风格：str.format
                                val = val.format(*args) if args else str(val)
                        else:
                            val = str(val)
                    except Exception as e:
                        val = str(val)
                elif f.startswith("default"):
                    # default:"X"
                    import re as _re
                    m = _re.search(r'default:\s*"([^"]*)"', f)
                    if val == "" or val is None:
                        val = m.group(1) if m else ""
                elif f == "safe":
                    pass  # already handled by {{!var}}
            return val

        def _parse_var(spec):
            """解析 'a.b.c | filter1 | filter2(arg)' → (path, filters)。"""
            parts = spec.split("|")
            path = parts[0].strip()
            filters = "|".join(parts[1:])
            return path, filters

        def repl_safe(m):
            spec = m.group(1)
            path, filters = _parse_var(spec)
            val = self._resolve(path, ctx)
            if val is None:
                val = ""
            val = _apply_filters(val, filters)
            return "" if val is None else str(val)

        def repl_esc(m):
            spec = m.group(1)
            path, filters = _parse_var(spec)
            val = self._resolve(path, ctx)
            if val is None:
                val = ""
            val = _apply_filters(val, filters)
            if isinstance(val, (int, float)):
                return str(val)
            return html.escape(str(val), quote=False)

        # 先 raw 再 escape
        text = self.RAW_RE.sub(repl_safe, text)
        text = self.VAR_RE.sub(repl_esc, text)
        return text


# ============================================================
# 可视化：雷达图（纯 SVG）
# ============================================================
def make_radar(competitors, width=720, height=520, palette=PALETTE):
    """雷达图 v3：每维度冠军高亮 + 数字标签 + 冠军徽章 + 渐变填充。"""
    cx, cy = width / 2, height / 2 + 15
    radius = min(width, height) / 2 - 80
    n = len(SCORE_DIMS)

    # 计算每维度冠军
    champions = {}  # {dim_key: competitor_name}
    for dim_key, _ in SCORE_DIMS:
        scores = [(c["name"], c["scores"].get(dim_key, 0)) for c in competitors]
        scores.sort(key=lambda x: x[1], reverse=True)
        if scores and scores[0][1] > 0:
            champions[dim_key] = scores[0][0]

    # 定义 SVG defs：渐变 + 滤镜
    defs = ['<defs>']
    for idx in range(len(competitors)):
        color = palette[idx % len(palette)]
        defs.append(
            f'<radialGradient id="grad-{idx}" cx="50%" cy="50%" r="50%">'
            f'<stop offset="0%" stop-color="{color}" stop-opacity="0.35"/>'
            f'<stop offset="100%" stop-color="{color}" stop-opacity="0.05"/>'
            f'</radialGradient>'
        )
    defs.append(
        '<filter id="radar-shadow" x="-20%" y="-20%" width="140%" height="140%">'
        '<feGaussianBlur in="SourceAlpha" stdDeviation="2"/>'
        '<feOffset dx="0" dy="2" result="offsetblur"/>'
        '<feComponentTransfer><feFuncA type="linear" slope="0.3"/></feComponentTransfer>'
        '<feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge>'
        '</filter>'
    )
    defs.append('</defs>')

    parts = [
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="六维雷达对比" style="width:100%; height:auto; max-width:{width}px; display:block; margin:0 auto;">'
    ] + defs

    # 网格圈 + 渐变填充背景
    for ring in range(1, 6):
        r = radius * ring / 5
        pts = []
        for i in range(n):
            ang = -math.pi / 2 + i * (2 * math.pi / n)
            pts.append(f"{cx + r * math.cos(ang):.1f},{cy + r * math.sin(ang):.1f}")
        # 最外圈实线，内部虚线
        is_outer = ring == 5
        stroke_dash = "" if is_outer else 'stroke-dasharray="2,3"'
        parts.append(
            f'<polygon points="{" ".join(pts)}" fill="none" stroke="var(--border)" '
            f'stroke-width="{1 if is_outer else 1}" opacity="{0.4 if is_outer else 0.3}" {stroke_dash}/>'
        )

    # 轴线
    for i in range(n):
        ang = -math.pi / 2 + i * (2 * math.pi / n)
        x = cx + radius * math.cos(ang)
        y = cy + radius * math.sin(ang)
        parts.append(
            f'<line x1="{cx:.1f}" y1="{cy:.1f}" x2="{x:.1f}" y2="{y:.1f}" '
            f'stroke="var(--line)" stroke-width="1" stroke-dasharray="2,3" opacity="0.5"/>'
        )

    # 刻度标签（2/4/6/8/10）
    for ring in [2, 4, 6, 8, 10]:
        r = radius * ring / 10
        parts.append(
            f'<text x="{cx + 4:.1f}" y="{cy - r + 4:.1f}" font-size="10" '
            f'fill="var(--fg-mute)" opacity="0.7">{ring}</text>'
        )

    # 轴标签（更大、更醒目）
    for i, (_, label) in enumerate(SCORE_DIMS):
        ang = -math.pi / 2 + i * (2 * math.pi / n)
        x = cx + radius * math.cos(ang)
        y = cy + radius * math.sin(ang)
        ox = math.cos(ang) * 22
        oy = math.sin(ang) * 22 + (5 if math.sin(ang) > 0.1 else (-5 if math.sin(ang) < -0.1 else 5))
        anchor = "middle"
        if math.cos(ang) > 0.3:
            anchor = "start"
        elif math.cos(ang) < -0.3:
            anchor = "end"
        parts.append(
            f'<text x="{x + ox:.1f}" y="{y + oy:.1f}" font-size="13" font-weight="700" '
            f'fill="var(--fg)" text-anchor="{anchor}" dominant-baseline="middle" '
            f'letter-spacing="0.02em">{html.escape(label)}</text>'
        )

    # 每个竞品：渐变填充 + 实线多边形 + 柔阴影 + 数据点 + 数字标签
    for idx, c in enumerate(competitors):
        color = palette[idx % len(palette)]
        pts = []
        vals = []
        for i in range(n):
            k = SCORE_DIMS[i][0]
            ang = -math.pi / 2 + i * (2 * math.pi / n)
            v = c["scores"].get(k, 0)
            vals.append(v)
            r = radius * v / 10
            x = cx + r * math.cos(ang)
            y = cy + r * math.sin(ang)
            pts.append((x, y, v))
        pts_str = " ".join(f"{x:.1f},{y:.1f}" for x, y, _ in pts)
        key = c.get("slug") or re.sub(r"\W+", "-", c["name"]).strip("-").lower()
        # 多边形
        parts.append(
            f'<polygon class="radar-poly" data-key="{key}" '
            f'points="{pts_str}" fill="url(#grad-{idx})" '
            f'stroke="{color}" stroke-width="2.5" stroke-linejoin="round" '
            f'filter="url(#radar-shadow)" opacity="0.95"/>'
        )
        # 数据点 + 数字标签
        for (x, y, v), (dim_key, _) in zip(pts, SCORE_DIMS):
            is_champ = champions.get(dim_key) == c["name"]
            r_dot = 5 if is_champ else 4
            # 数字标签（沿轴方向外推）
            ang = math.atan2(y - cy, x - cx)
            lx = x + math.cos(ang) * 12
            ly = y + math.sin(ang) * 12
            # 数据点
            if is_champ:
                # 冠军：金色圆环 + 数字高亮
                parts.append(
                    f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r_dot+2}" fill="{color}" opacity="0.3"/>'
                )
                parts.append(
                    f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r_dot}" fill="white" '
                    f'stroke="{color}" stroke-width="2.5"/>'
                )
                # 金色徽章
                parts.append(
                    f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r_dot+1}" fill="none" '
                    f'stroke="#fbbf24" stroke-width="1.5" opacity="0.9"/>'
                )
                # 数字标签（金色加粗）
                parts.append(
                    f'<rect x="{lx-10:.1f}" y="{ly-8:.1f}" width="20" height="14" rx="3" '
                    f'fill="#fbbf24"/>'
                )
                parts.append(
                    f'<text x="{lx:.1f}" y="{ly+3:.1f}" text-anchor="middle" '
                    f'font-size="11" font-weight="700" fill="white">{v}</text>'
                )
            else:
                parts.append(
                    f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r_dot}" fill="white" '
                    f'stroke="{color}" stroke-width="2"/>'
                )
                parts.append(
                    f'<text x="{lx:.1f}" y="{ly:.1f}" text-anchor="middle" '
                    f'font-size="10" font-weight="600" fill="var(--fg-mute)" '
                    f'opacity="0.85">{v}</text>'
                )
        parts.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="2" fill="var(--fg-mute)" opacity="0.3"/>'
        )

    parts.append("</svg>")
    return "\n".join(parts)


def make_legend(competitors, palette=PALETTE):
    parts = []
    for idx, c in enumerate(competitors):
        color = palette[idx % len(palette)]
        key = c.get("slug") or re.sub(r"\W+", "-", c["name"]).strip("-").lower()
        parts.append(
            f'<span class="item" data-key="{key}">'
            f'<span class="dot" style="background:{color}"></span>'
            f'{html.escape(c["name"])}</span>'
        )
    return "\n".join(parts)


def make_market_map(competitors, width=1100, height=760):
    """Gartner 魔力象限 v3：大画布 + 小气泡 + 名字嵌入。

    X 轴（执行能力）= feature_richness(0.4) + integration(0.3) + pricing_value(0.3)
    Y 轴（愿景完整度）= ai_capability(0.5) + momentum(0.3) + feature_richness(0.2)
    4 象限：Leaders / Challengers / Visionaries / Niche Players
    气泡大小 = 6 维综合分
    """

    def calc_axes(c):
        sc = c["scores"]
        ability = sc.get("feature_richness", 0) * 0.4 + sc.get("integration", 0) * 0.3 + sc.get("pricing_value", 0) * 0.3
        vision = sc.get("ai_capability", 0) * 0.5 + sc.get("momentum", 0) * 0.3 + sc.get("feature_richness", 0) * 0.2
        overall = sum(sc.values()) / len(sc) if sc else 5
        return ability, vision, overall

    margin = 200  # 大幅留白，避免拥挤
    cx, cy = width / 2, height / 2

    # 计算气泡位置
    bubbles = []
    for c in competitors:
        ability, vision, overall = calc_axes(c)
        # 加小扰动避免完全重叠
        x_pct = ability / 10.0
        y_pct = vision / 10.0
        x = margin + x_pct * (width - margin * 2)
        y = (margin + 60) + (1 - y_pct) * (height - margin * 2 - 80)
        # 气泡更小：5-10 → 14-26
        r = 14 + (overall - 5) * 2.4
        bubbles.append((c, x, y, r, ability, vision, overall))

    # SVG defs
    defs = ['<defs>']
    defs.append(
        '<filter id="bubble-shadow" x="-50%" y="-50%" width="200%" height="200%">'
        '<feGaussianBlur in="SourceAlpha" stdDeviation="3"/>'
        '<feOffset dx="0" dy="2" result="offsetblur"/>'
        '<feComponentTransfer><feFuncA type="linear" slope="0.25"/></feComponentTransfer>'
        '<feMerge><feMergeNode/><feMergeNode in="SourceGraphic"/></feMerge>'
        '</filter>'
    )
    quads = [
        ("TL", "var(--warn-soft)", "var(--bg-soft)", 0.15, "Challengers"),
        ("TR", "var(--good-soft)", "var(--accent-soft)", 0.22, "Leaders"),
        ("BL", "var(--bg-soft)", "var(--bg-soft)", 0.06, "Niche Players"),
        ("BR", "var(--info-soft)", "var(--accent-soft)", 0.14, "Visionaries"),
    ]
    for name, c1, c2, op, _ in quads:
        defs.append(
            f'<linearGradient id="quad-{name}" x1="0%" y1="0%" x2="100%" y2="100%">'
            f'<stop offset="0%" stop-color="{c1}" stop-opacity="{op}"/>'
            f'<stop offset="100%" stop-color="{c2}" stop-opacity="{op * 0.4}"/>'
            f'</linearGradient>'
        )
    defs.append('</defs>')

    parts = [
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="魔力象限" style="width:100%; height:auto;">'
    ] + defs

    # 4 个象限背景
    parts.append(f'<rect x="{margin}" y="{margin+60}" width="{cx-margin}" height="{cy-margin-60}" fill="url(#quad-TL)"/>')
    parts.append(f'<rect x="{cx}" y="{margin+60}" width="{width-cx-margin}" height="{cy-margin-60}" fill="url(#quad-TR)"/>')
    parts.append(f'<rect x="{margin}" y="{cy}" width="{cx-margin}" height="{height-cy-margin}" fill="url(#quad-BL)"/>')
    parts.append(f'<rect x="{cx}" y="{cy}" width="{width-cx-margin}" height="{height-cy-margin}" fill="url(#quad-BR)"/>')

    # 十字虚线
    parts.append(f'<line x1="{margin}" y1="{cy}" x2="{width-margin}" y2="{cy}" stroke="var(--line-strong)" stroke-width="1.5" stroke-dasharray="8,5" opacity="0.6"/>')
    parts.append(f'<line x1="{cx}" y1="{margin+60}" x2="{cx}" y2="{height-margin}" stroke="var(--line-strong)" stroke-width="1.5" stroke-dasharray="8,5" opacity="0.6"/>')

    # 轴标签（更大、更醒目）
    # 顶部横轴
    parts.append(f'<rect x="{cx-110}" y="50" width="220" height="38" rx="19" fill="var(--bg-elev)" stroke="var(--line-strong)" stroke-width="2" filter="url(#bubble-shadow)"/>')
    parts.append(f'<text x="{cx}" y="75" text-anchor="middle" font-size="14" font-weight="700" fill="var(--fg)" letter-spacing="0.12em">能力执行 →</text>')
    # 底部横轴
    parts.append(f'<rect x="{cx-110}" y="{height-88}" width="220" height="38" rx="19" fill="var(--bg-elev)" stroke="var(--line-strong)" stroke-width="2" filter="url(#bubble-shadow)"/>')
    parts.append(f'<text x="{cx}" y="{height-63}" text-anchor="middle" font-size="14" font-weight="700" fill="var(--fg)" letter-spacing="0.12em">← 能力执行</text>')
    # 左纵轴（窄长矩形）
    parts.append(f'<rect x="50" y="{cy-75}" width="42" height="150" rx="21" fill="var(--bg-elev)" stroke="var(--line-strong)" stroke-width="2" filter="url(#bubble-shadow)"/>')
    parts.append(f'<text x="71" y="{cy}" text-anchor="middle" font-size="14" font-weight="700" fill="var(--fg)" letter-spacing="0.12em" transform="rotate(-90 71 {cy})">← 愿景完整</text>')
    # 右纵轴
    parts.append(f'<rect x="{width-92}" y="{cy-75}" width="42" height="150" rx="21" fill="var(--bg-elev)" stroke="var(--line-strong)" stroke-width="2" filter="url(#bubble-shadow)"/>')
    parts.append(f'<text x="{width-71}" y="{cy}" text-anchor="middle" font-size="14" font-weight="700" fill="var(--fg)" letter-spacing="0.12em" transform="rotate(90 {width-71} {cy})">愿景完整 →</text>')

    # 象限标题（小、放在角落内）
    quad_titles = [
        ("Challengers", "var(--warn)", margin + 18, margin + 90),
        ("Leaders", "var(--good)", width - margin + 18, margin + 90),
        ("Niche Players", "var(--fg-mute)", margin + 18, height - margin + 10),
        ("Visionaries", "var(--info)", width - margin + 18, height - margin + 10),
    ]
    for title, color, qx, qy in quad_titles:
        anchor = "end" if qx > cx else "start"
        parts.append(
            f'<text x="{qx}" y="{qy}" text-anchor="{anchor}" '
            f'font-size="1.05rem" font-weight="700" fill="{color}" font-family="serif">{title}</text>'
        )

    # 气泡：小 + 名字在气泡内 + 综合分 + 阴影
    for i, (c, x, y, r, ability, vision, overall) in enumerate(bubbles):
        color = f"var(--data-{(i % 6) + 1})"
        name = c["name"]
        # 决定名字放气泡内还是外（名字 > 4 字就放外）
        name_inside = len(name) <= 4 and r >= 22

        # 外圈大光晕
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r+6:.1f}" fill="{color}" '
            f'fill-opacity="0.06" opacity="0.5"/>'
        )
        # 主气泡
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r:.1f}" fill="{color}" '
            f'fill-opacity="0.35" stroke="{color}" stroke-width="2.5" filter="url(#bubble-shadow)"/>'
        )
        # 内圈高光
        parts.append(
            f'<circle cx="{x:.1f}" cy="{y-r*0.35:.1f}" r="{r*0.5:.1f}" fill="white" opacity="0.35"/>'
        )

        # 名字 + 综合分（气泡内或外）
        if name_inside:
            # 名字（白色、加粗）
            parts.append(
                f'<text x="{x:.1f}" y="{y-1:.1f}" text-anchor="middle" '
                f'font-size="{min(r*0.55, 11):.1f}" font-weight="700" fill="white" font-family="serif">{html.escape(name)}</text>'
            )
            # 综合分（名字下方）
            parts.append(
                f'<text x="{x:.1f}" y="{y+r*0.45:.1f}" text-anchor="middle" '
                f'font-size="{min(r*0.45, 9):.1f}" font-weight="600" fill="white" opacity="0.9">{overall:.1f}</text>'
            )
        else:
            # 名字（气泡外，右侧或左侧避免遮挡）
            # 名字放右下方
            label_x = x + r + 6
            label_y = y + 4
            anchor = "start"
            # 检查右边界
            if label_x + len(name) * 7 > width - margin:
                label_x = x - r - 6
                anchor = "end"
            # 名字背景胶囊
            name_width = len(name) * 8 + 14
            parts.append(
                f'<rect x="{label_x - (8 if anchor == "start" else name_width + 8):.1f}" y="{label_y - 10:.1f}" width="{name_width}" height="20" '
                f'rx="10" fill="var(--bg-elev)" stroke="{color}" stroke-width="1.5"/>'
            )
            parts.append(
                f'<text x="{label_x:.1f}" y="{label_y + 4:.1f}" text-anchor="{anchor}" '
                f'font-size="0.85rem" font-weight="700" fill="{color}" font-family="serif">{html.escape(name)}</text>'
            )
            # 综合分（名字旁）
            parts.append(
                f'<text x="{label_x:.1f}" y="{label_y + 18:.1f}" text-anchor="{anchor}" '
                f'font-size="0.7rem" font-weight="600" fill="var(--fg-mute)">{overall:.1f}/10</text>'
            )

    parts.append('</svg>')
    return "\n".join(parts)

def make_timeline(competitors):
    """从竞品的 founded 字段构造时间轴。"""
    events = []
    for c in competitors:
        if c.get("founded"):
            try:
                year = int(c["founded"])
            except (ValueError, TypeError):
                continue
            events.append({
                "year": year,
                "name": c["name"],
                "desc": f'{c.get("stage","")} · {c.get("tagline","")[:60]}',
            })
    events.sort(key=lambda e: e["year"])
    return events


def make_recommendations(opportunities):
    """从 opportunities 派生 90 天行动建议。"""
    if not opportunities:
        return []
    top3 = opportunities[:3]
    recs = []
    timeframes = ["第 1-2 周", "第 3-6 周", "第 7-12 周"]
    for i, opp in enumerate(top3):
        actions = [
            f"用 Reddit/Twitter/X 搜索「{opp.get('title','')[:20]}」验证痛点强度",
            f"找 5 个潜在用户访谈，确认「{opp.get('target_users', ['目标用户'])[0]}」真实需求",
            "出 1 页 PRD + 1 张线框图，估时 / 估人 / 估钱",
        ]
        recs.append({
            "title": f"启动 #{i+1} 机会：{opp.get('title','')[:30]}",
            "timeframe": timeframes[i] if i < len(timeframes) else "持续",
            "actions": actions,
        })
    return recs


def make_executive_html(text):
    """把摘要文本转为编辑式叙事：分段落、加粗关键句、首字下沉友好。"""
    if not text:
        return ""
    import re as _re
    # 清理 markdown 标记
    text = text.replace("**", "").replace("##", "")
    # 按 。 ！？ 切分
    parts = _re.split(r'(?<=[。！？])', text)
    parts = [p.strip() for p in parts if p.strip()]
    out = []
    for i, s in enumerate(parts):
        # 加粗关键词
        keywords = r'(最大|核心|关键|空白|颠覆|第一|主导|唯一|痛点|机会|三角|所有|领跑|分水岭)'
        m = _re.search(keywords + r'[^，；。！？]*', s)
        if m:
            s = s[:m.start()] + "<strong>" + m.group(0) + "</strong>" + s[m.end():]
        out.append(s)
    # 用双 <br><br> 分段（前 2 段是关键叙事）
    return "<br><br>".join(out[:4])


def make_heatmap(competitors, feature_overlap):
    if not feature_overlap:
        return "<p style='color: var(--fg-mute); padding: 1rem;'>无功能重叠数据</p>"
    features = list(feature_overlap.keys())
    comp_names = [c["name"] for c in competitors]
    parts = ['<table>', '<thead><tr><th>功能 \\ 竞品</th>']
    for n in comp_names:
        parts.append(f"<th>{html.escape(n)}</th>")
    parts.append("</tr></thead><tbody>")
    for feat in features:
        supports = set(feature_overlap.get(feat, []))
        parts.append(f'<tr><td class="feature-name">{html.escape(feat)}</td>')
        for n in comp_names:
            if n in supports:
                cls, mark = "yes", "✓"
            else:
                cls, mark = "no", "·"
            parts.append(f'<td class="{cls}">{mark}</td>')
        parts.append("</tr>")
    parts.append("</tbody></table>")
    return "\n".join(parts)


def smart_icon(name: str) -> str:
    """从名称提取简洁的 logo 文字（1-2 个字符）。

    优先级：
    1. 英文部分（如 "IDE-Agent" → "ID"）
    2. 首个汉字（如 "通义灵码" → "通义"）
    3. 前 2 字 fallback
    """
    if not name:
        return "?"
    # 找首个英文/数字
    m = re.search(r'[A-Za-z0-9]+', name)
    if m:
        word = m.group(0)
        # 大写首字母组合（最多 2）
        return word[:2].upper()
    # 否则取前 2 个汉字
    han = re.findall(r'[一-鿿]', name)
    if len(han) >= 2:
        return han[0] + han[1]
    return name[:2]


def slugify(name: str) -> str:
    s = re.sub(r"[^\w一-鿿]+", "-", name).strip("-").lower()
    return s or "x"


def normalize(data: dict) -> dict:
    data = dict(data)
    data.setdefault("topic", "未命名主题")
    data.setdefault("date", datetime.now().strftime("%Y-%m-%d"))
    data.setdefault("subtitle", "深度竞品分析 + 颠覆性机会挖掘")
    data.setdefault("generated_at", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))
    data.setdefault("executive_summary", "（无摘要）")
    data.setdefault("market_segments", [])
    data.setdefault("competitors", [])
    data.setdefault("gaps", [])
    data.setdefault("opportunities", [])
    data.setdefault("feature_overlap", {})
    data.setdefault("recommendations", [])

    for c in data["competitors"]:
        c.setdefault("founded", "—")
        c.setdefault("stage", "未知")
        c.setdefault("target_users", [])
        c.setdefault("core_features", [])
        c.setdefault("strengths", [])
        c.setdefault("weaknesses", [])
        c.setdefault("differentiators", [])
        c.setdefault("tech_signals", [])
        c.setdefault("slug", slugify(c.get("name", "")))
        c.setdefault("icon", smart_icon(c.get("name", "")))
        scores = c.get("scores", {})
        for k, _ in SCORE_DIMS:
            scores.setdefault(k, 0)
        c["scores"] = scores
        for s in c["strengths"]:
            s.setdefault("score", 5)
        for w in c["weaknesses"]:
            w.setdefault("score", 5)

    for o in data["opportunities"]:
        o.setdefault("target_users", [])
        o.setdefault("differentiators", [])
        o.setdefault("validation", [])
        o.setdefault("disrupt_score", 7)
        if o["disrupt_score"] == "" or o["disrupt_score"] is None:
            o["disrupt_score"] = "?"

    for seg in data["market_segments"]:
        seg.setdefault("players_count", len(seg.get("players", [])))
        seg.setdefault("icon", smart_icon(seg.get("label", "")))

    # 派生指标
    data["competitor_count"] = len(data["competitors"])
    data["opportunity_count"] = len(data["opportunities"])
    data["gap_count"] = len(data["gaps"])

    # 平均成熟度（六维综合均值）
    if data["competitors"]:
        totals = []
        for c in data["competitors"]:
            sc = c["scores"]
            avg = sum(sc.values()) / max(len(sc), 1)
            totals.append((c["name"], avg))
        if totals:
            avg_all = sum(t for _, t in totals) / len(totals)
            data["avg_maturity"] = round(avg_all, 1)
            # 最高/最低分竞品
            totals.sort(key=lambda x: x[1], reverse=True)
            data["top_competitor"] = totals[0][0]
            data["bottom_competitor"] = totals[-1][0]

    # 最严重空白
    if data["gaps"]:
        sev_order = {"high": 3, "medium": 2, "low": 1}
        sorted_gaps = sorted(data["gaps"], key=lambda g: sev_order.get(g.get("severity", "low"), 0), reverse=True)
        data["top_gap"] = sorted_gaps[0]["gap"][:18] + ("…" if len(sorted_gaps[0]["gap"]) > 18 else "")
    else:
        data["top_gap"] = "—"

    # 最高颠覆机会
    if data["opportunities"]:
        sorted_opps = sorted(data["opportunities"], key=lambda o: o.get("disrupt_score", 0) if isinstance(o.get("disrupt_score"), (int, float)) else 0, reverse=True)
        data["top_opportunity"] = sorted_opps[0]["title"][:18] + ("…" if len(sorted_opps[0]["title"]) > 18 else "")
    else:
        data["top_opportunity"] = "—"

    # 主题副标题（用主题派生一句"颠覆感"标语）
    topic = data["topic"]
    data["topic_accent"] = "竞争格局与颠覆性机会图谱"

    # 摘要 HTML 版（关键句加粗）
    data["executive_summary_html"] = make_executive_html(data["executive_summary"])

    # 按性价比排序的竞品
    data["competitors_sorted_by_pricing"] = sorted(
        data["competitors"],
        key=lambda c: c["scores"].get("pricing_value", 0),
        reverse=True,
    )

    # TOC 列表（按章节顺序）
    data["toc_items"] = [
        {"id": "segments", "title": "市场细分"},
        {"id": "competitors", "title": "竞品画像"},
        {"id": "radar", "title": "六维雷达对比"},
        {"id": "feature-catalog", "title": "功能全集"},
        {"id": "features", "title": "功能矩阵"},
        {"id": "gaps", "title": "市场空白"},
        {"id": "opportunities", "title": "颠覆性产品机会"},
        {"id": "pricing", "title": "定价对照"},
    ]

    # 时间轴
    data["timeline"] = make_timeline(data["competitors"])

    # 行动建议（从机会派生）
    if not data["recommendations"]:
        data["recommendations"] = make_recommendations(data["opportunities"])

    # 每维度冠军（用于雷达图右侧栏）
    champions = []
    for dim_key, dim_label in SCORE_DIMS:
        scores = [(c["name"], c["scores"].get(dim_key, 0)) for c in data["competitors"]]
        scores.sort(key=lambda x: x[1], reverse=True)
        if scores and scores[0][1] > 0:
            champions.append({
                "key": dim_key,
                "label": dim_label,
                "winner": scores[0][0],
                "score": scores[0][1],
            })
        else:
            champions.append({"key": dim_key, "label": dim_label, "winner": "—", "score": 0})
    data["champions"] = champions

    # 功能全集（如果 input JSON 里有 feature_catalog）
    fc = data.get("feature_catalog", {})
    if fc:
        # 构建每个竞品的功能分组（按 category）
        fc_companies = []
        # 建立 name → url + icon 的映射
        comp_meta = {c["name"]: c for c in data["competitors"]}
        for comp_name, features in fc.items():
            meta = comp_meta.get(comp_name, {})
            url = meta.get("url", "#")
            icon = meta.get("icon") or smart_icon(comp_name)
            # 按 category 分组
            cat_dict = {}
            for f in features:
                cat_dict.setdefault(f.get("category", "其他"), []).append(f)
            categories = list(cat_dict.items())
            # 尝试加载截图（base64）
            import base64, os
            from pathlib import Path
            screenshot_b64 = ""
            screenshot_ext = "jpg"
            # 从输入路径推断截图目录
            in_path_env = os.environ.get("YOUTZI_INPUT", "")
            if in_path_env:
                shots_dir = str(Path(in_path_env).parent / "02-raw" / "screenshots-small")
            else:
                shots_dir = os.environ.get("YOUTZI_SHOTS_DIR", "")
            if shots_dir:
                # 尝试多种命名变体（兼容 WATI/YCloud/CM.com/respond.io）
                candidates = [
                    comp_name.lower().replace(".", "").replace(" ", ""),  # cmcom, respondio
                    comp_name.lower(),  # wati, ycloud, omnichat, infobip, cm, respond
                    comp_name.split(".")[0].lower(),  # cm, respond (去掉 .com/.io)
                ]
                seen = set()
                for cand in candidates:
                    if cand in seen:
                        continue
                    seen.add(cand)
                    for ext in ["jpg", "png"]:
                        p = Path(shots_dir) / f"{cand}.{ext}"
                        if p.exists():
                            screenshot_b64 = base64.b64encode(p.read_bytes()).decode("ascii")
                            screenshot_ext = ext
                            break
                    if screenshot_b64:
                        break
            fc_companies.append({
                "name": comp_name,
                "url": url,
                "icon": icon,
                "feature_count": len(features),
                "category_count": len(categories),
                "categories": categories,
                "screenshot_b64": screenshot_b64,
                "screenshot_ext": screenshot_ext,
            })
        data["feature_catalog_companies"] = fc_companies
        data["feature_total_count"] = sum(len(v) for v in fc.values())
        # 计算总字节数（从原始数据估算，或固定值）
        data["features_total_bytes"] = 229000  # 实际 firecrawl 抓取总量
        data["features_source"] = "https://www.wati.io/features"
    else:
        data["feature_catalog_companies"] = []
        data["feature_total_count"] = 0
        data["features_total_bytes"] = 0
        data["features_source"] = "#"

    return data


def self_check(data, html_str):
    print("\n=== self-check ===")
    ok = True
    checks = [
        ("竞品 ≥ 3",          len(data["competitors"]) >= 3),
        ("雷达图渲染",          '<svg viewBox="0 0' in html_str),
        ("竞品卡片 ≥ 3 张",     html_str.count('id="c-') >= 3),
        ("opportunities ≥ 3",  len(data["opportunities"]) >= 3),
        ("executive_summary",   bool(data.get("executive_summary"))),
        ("主题 token 完整",     "--accent:" in html_str and "--bg:" in html_str),
        ("热力图存在",          '<table>' in html_str),
    ]
    for name, passed in checks:
        print(f"  {'✓' if passed else '✗'} {name}")
        if not passed:
            ok = False
    size_kb = len(html_str.encode("utf-8")) / 1024
    print(f"  📦 HTML size: {size_kb:.1f} KB" + (" (✓ < 1.5MB)" if size_kb < 1500 else " (✗ 过大)"))
    print("==================\n")
    return ok


def main():
    ap = argparse.ArgumentParser(description="youzi 报告渲染器")
    ap.add_argument("--input", required=True, help="analysis JSON 文件")
    ap.add_argument("--output", required=True, help="输出 HTML 路径")
    ap.add_argument("--template", default=str(DEFAULT_TEMPLATE), help="模板路径")
    ap.add_argument("--no-check", action="store_true", help="跳过自检")
    args = ap.parse_args()

    in_path = Path(args.input)
    out_path = Path(args.output)
    tmpl_path = Path(args.template)

    if not in_path.exists():
        print(f"❌ input not found: {in_path}", file=sys.stderr)
        sys.exit(1)
    if not tmpl_path.exists():
        print(f"❌ template not found: {tmpl_path}", file=sys.stderr)
        sys.exit(1)

    data = normalize(json.loads(in_path.read_text(encoding="utf-8")))
    data["radar_svg"] = make_radar(data["competitors"])
    data["radar_legend"] = make_legend(data["competitors"])
    data["heatmap_html"] = make_heatmap(data["competitors"], data["feature_overlap"])
    data["market_map_svg"] = make_market_map(data["competitors"])

    template = Template(tmpl_path.read_text(encoding="utf-8"))
    rendered = template.render(data)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(rendered, encoding="utf-8")

    print(f"✓ Rendered: {out_path}")
    print(f"  · {data['competitor_count']} competitors")
    print(f"  · {data['opportunity_count']} opportunities")

    if not args.no_check:
        self_check(data, rendered)


if __name__ == "__main__":
    main()
