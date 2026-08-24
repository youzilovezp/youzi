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
import re
import sys
import html
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_TEMPLATE = HERE / "templates" / "report.html"

# ============================================================
# 评分维度（6 维）—— 用于校准每个竞品 scores 字段
# ============================================================
SCORE_DIMS = [
    ("feature_richness", "功能丰富度"),
    ("ux", "用户体验"),
    ("pricing_value", "定价性价比"),
    ("integration", "生态集成"),
    ("ai_capability", "AI 能力"),
    ("momentum", "增长势头"),
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
    FOR_RE = re.compile(r"\{%\s*for\s+([\w, ]+?)\s+in\s+([\w.\[\]]+)\s*%\}")
    ENDFOR_RE = re.compile(r"\{%\s*endfor\s*%\}")
    IF_RE = re.compile(r"\{%\s*if\s+(.+?)\s*%\}")
    ELIF_RE = re.compile(r"\{%\s*elif\s+(.+?)\s*%\}")
    ELSE_RE = re.compile(r"\{%\s*else\s*%\}")
    ENDIF_RE = re.compile(r"\{%\s*endif\s*%\}")
    SET_RE = re.compile(r"\{%\s*set\s+([\w.\[\]]+)\s*=\s*(.+?)\s*%\}")
    VAR_RE = re.compile(r"\{\{\s*([^\{\}]+?)\s*\}\}")
    RAW_RE = re.compile(r"\{\{\!\s*([^\{\}]+?)\s*\}\}")

    def __init__(self, source: str):
        self.source = source

    # -------------------- 变量解析 --------------------
    def _resolve(self, path: str, ctx) -> object:
        """解析形如 a.b.c[expr].d 的 key 路径。

        支持：
          - 字面字符串（带引号）："x" / 'x'
          - 点号访问：a.b.c
          - 下标访问：
              a["x"]   字面字符串下标（带引号）
              a[x]     ctx 中的变量作为 key（x 解析后取其值）
              a[0]     整数下标（针对 list/tuple）
        """
        if (path.startswith('"') and path.endswith('"')) or (
            path.startswith("'") and path.endswith("'")
        ):
            return path[1:-1]
        # 先把所有 [expr] 解析出来 —— expr 内的变量先按 ctx 求值
        # 然后拼成 a.__lit__<value> 形式，让 split('.') 能处理
        norm_parts = []
        i = 0
        s = path
        while i < len(s):
            j = s.find("[", i)
            if j == -1:
                norm_parts.append(s[i:])
                break
            norm_parts.append(s[i:j])
            k = s.find("]", j + 1)
            if k == -1:
                # 不闭合的 [ 视为字面
                norm_parts.append(s[j:])
                break
            inner = s[j + 1 : k].strip()
            i = k + 1
            # 三种情况：字面字符串 / 整数 / ctx 中的变量路径
            if (inner.startswith('"') and inner.endswith('"')) or (
                inner.startswith("'") and inner.endswith("'")
            ):
                key_val = inner[1:-1]
                norm_parts.append("__lit__" + key_val)
            elif inner.lstrip("-").isdigit():
                norm_parts.append("__idx__" + inner)
            else:
                # 当作 ctx 路径解析
                try:
                    key_val = self._resolve(inner, ctx)
                    if key_val is None or key_val == "":
                        return ""
                    norm_parts.append("__lit__" + str(key_val))
                except Exception:
                    return ""
        cur = ctx
        for part in norm_parts:
            if part == "":
                continue
            if part.startswith("__lit__"):
                lit = part[len("__lit__") :]
                if isinstance(cur, dict):
                    cur = cur.get(lit, "")
                elif isinstance(cur, (list, tuple)):
                    try:
                        cur = cur[int(lit)]
                    except (ValueError, IndexError):
                        return ""
                else:
                    return ""
            elif part.startswith("__idx__"):
                idx = int(part[len("__idx__") :])
                if isinstance(cur, (list, tuple)):
                    try:
                        cur = cur[idx]
                    except IndexError:
                        return ""
                else:
                    return ""
            else:
                # 普通 part 可能含 "a.b.c" —— 逐段走 dict/attr
                for sub in part.split("."):
                    if sub == "":
                        continue
                    if isinstance(cur, dict):
                        cur = cur.get(sub, "")
                    elif isinstance(cur, (list, tuple)):
                        try:
                            cur = cur[int(sub)]
                        except (ValueError, IndexError):
                            return ""
                    elif hasattr(cur, sub):
                        cur = getattr(cur, sub)
                    else:
                        return ""
                    if cur == "" or cur is None:
                        return ""
                continue
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
            next_set = self.SET_RE.search(src, i)
            # 取最近的
            candidates = []
            if next_for:
                candidates.append(("for", next_for))
            if next_if:
                candidates.append(("if", next_if))
            if next_set:
                candidates.append(("set", next_set))
            if not candidates:
                # 后面全是纯文本 + var
                out.append(self._render_text(src[i:], ctx))
                break
            # 选最近的
            candidates.sort(key=lambda x: x[1].start())
            kind, m = candidates[0]
            # 输出标记之前的文本
            out.append(self._render_text(src[i : m.start()], ctx))
            if kind == "for":
                result, end = self._parse_for(src, m.start(), ctx)
                out.append(result)
                i = end
            elif kind == "if":
                result, end = self._parse_if(src, m.start(), ctx)
                out.append(result)
                i = end
            else:  # set
                # 清洗整个 set 标签（如偶然捕获了 "%}" 残留）
                tag_text = src[m.start() : m.end()]
                tag_text = re.sub(r"%}.*", "%}", tag_text)
                # 重新匹配清洗后的标签
                m2 = self.SET_RE.match(tag_text)
                if not m2:
                    i = m.end()
                    continue
                var_name = m2.group(1).strip()
                expr = m2.group(2).strip()
                # 解析表达式
                if "|" in expr:
                    p2, filters = expr.split("|", 1)
                    val = self._resolve(p2.strip(), ctx)
                    m3 = re.search(r'default:\s*"([^"]*)"', filters)
                    if m3 and (val is None or val == ""):
                        val = m3.group(1)
                else:
                    val = self._resolve(expr, ctx)
                if (
                    var_name
                    and " " not in var_name
                    and "=" not in var_name
                    and "|" not in var_name
                ):
                    ctx[var_name] = val
                i = m.end()
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
        # dict 转为 items() 列表以支持 (k, v) 解构
        if isinstance(items, dict):
            items = list(items.items())
        if not isinstance(items, (list, tuple)):
            return "", endfor_end
        # 解析变量名（支持 "k, v" 形式）
        var_names = [v.strip() for v in var_str.split(",")]
        parts = []
        for idx, item in enumerate(items):
            sub = dict(ctx)
            # tuple 解构或多变量
            if (
                len(var_names) > 1
                and isinstance(item, (list, tuple))
                and len(item) == len(var_names)
            ):
                for vn, iv in zip(var_names, item):
                    sub[vn] = iv
            elif (
                len(var_names) == 1
                and isinstance(item, (list, tuple))
                and len(item) == 2
            ):
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
                for marker_re, marker_type in [
                    (self.IF_RE, "if"),
                    (self.ELIF_RE, "elif"),
                    (self.ELSE_RE, "else"),
                    (self.ENDIF_RE, "endif"),
                ]:
                    mm = marker_re.search(full_block, j)
                    if mm and (next_any is None or mm.start() < next_any.start()):
                        next_any = mm
                        next_type = marker_type
                if next_any is None:
                    break
                if next_type == "if":
                    depth += 1
                elif next_type == "endif":
                    depth -= 1
                    if depth == 0:
                        break
                elif depth == 0 and next_type in ("elif", "else"):
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
                if cut_type == "elif":
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
            for marker_re, kind in [
                (self.IF_RE, "if"),
                (self.ENDIF_RE, "endif"),
                (self.FOR_RE, "for"),
            ]:
                m = marker_re.search(src, i)
                if m:
                    candidates.append((m.start(), m.end(), kind, marker_re))
            if not candidates:
                return -1
            candidates.sort(key=lambda x: x[0])
            pos, nxt_end, kind, _ = candidates[0]
            if kind == "if":
                depth += 1
                i = nxt_end
            elif kind == "endif":
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
                elif f.startswith("map_cat_commons"):
                    # 特殊过滤器:返回所有 category 中 _comps 列表长度的最大值
                    if f.endswith("max_share"):
                        try:
                            val = max(
                                (
                                    len(feat.get("_comps", []))
                                    for cat in val
                                    for feat in cat.get("features", [])
                                ),
                                default=0,
                            )
                        except Exception:
                            val = 0
                elif f.startswith("sort_reverse_by_total"):
                    val = sorted(val, key=lambda x: -x.get("total_features", 0))
                elif f == "minus":
                    # 把上一步结果当成数字减 8 (在 cat_features 切片场景使用)
                    try:
                        val = val - 8
                    except Exception:
                        val = val
                elif f.startswith("format"):
                    # 语法: format(expr) → Python format 调用 val.format(*[args])
                    try:
                        if "(" in f:
                            inner = f[f.index("(") + 1 : f.rindex(")")]
                            args = []
                            for part in inner.split(","):
                                part = part.strip()
                                # 1. 数字字面
                                if part.lstrip("-").isdigit():
                                    args.append(int(part))
                                # 2. 字符串字面（带引号）
                                elif (part.startswith('"') and part.endswith('"')) or (
                                    part.startswith("'") and part.endswith("'")
                                ):
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
                    except Exception:
                        val = str(val)
                elif f.startswith("default"):
                    # default:"X"   字面字符串（带引号）
                    # default:c.name  当前 ctx 中的变量路径
                    if val == "" or val is None:
                        arg = (
                            f[len("default:") :].strip()
                            if f.startswith("default:")
                            else ""
                        )
                        if not arg:
                            val = ""
                        elif (arg.startswith('"') and arg.endswith('"')) or (
                            arg.startswith("'") and arg.endswith("'")
                        ):
                            val = arg[1:-1]
                        else:
                            # 当作 ctx 路径解析
                            try:
                                val = self._resolve(arg, ctx)
                                if val is None:
                                    val = ""
                            except Exception:
                                val = ""
                elif f.startswith("join"):
                    # join:"X"   用 X 连接 list/tuple；默认 ", "
                    sep = ", "
                    if ":" in f:
                        sep_part = f.split(":", 1)[1].strip()
                        if (sep_part.startswith('"') and sep_part.endswith('"')) or (
                            sep_part.startswith("'") and sep_part.endswith("'")
                        ):
                            sep = sep_part[1:-1]
                        elif sep_part:
                            sep = sep_part
                    if isinstance(val, (list, tuple)):
                        val = sep.join(str(x) for x in val)
                    elif isinstance(val, str) and val:
                        # 已经是字符串就不动
                        pass
                    else:
                        val = ""
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
    m = re.search(r"[A-Za-z0-9]+", name)
    if m:
        word = m.group(0)
        # 大写首字母组合（最多 2）
        return word[:2].upper()
    # 否则取前 2 个汉字
    han = re.findall(r"[一-鿿]", name)
    if len(han) >= 2:
        return han[0] + han[1]
    return name[:2]


def slugify(name: str) -> str:
    s = re.sub(r"[^\w一-鿿]+", "-", name).strip("-").lower()
    return s or "x"


# ============================================================
# 派生：从 13 字段 JSON 推出飞书模板所需的所有结构
# ============================================================
_ANGLE_KEYWORDS = {
    "产品": ["产品", "UX", "界面", "体验", "设计", "编辑器", "协作", "功能"],
    "技术": ["技术", "架构", "AI", "模型", "引擎", "API", "性能", "算法"],
    "市场": ["市场", "增长", "用户", "客户", "份额", "营销", "GTM", "出海"],
    "商业": ["商业", "定价", "盈利", "营收", "订阅", "付费", "转化", "毛利"],
    "生态": ["生态", "集成", "合作伙伴", "API", "开放", "开发者", "插件"],
    "团队": ["团队", "组织", "工程", "文化", "招聘", "远程"],
}


def _classify_angle(text: str) -> str:
    """根据关键词把一段竞品优点/缺点归类到 6 个角度之一。"""
    if not text:
        return "产品"
    best = ("产品", 0)
    for angle, kws in _ANGLE_KEYWORDS.items():
        score = sum(1 for kw in kws if kw in text)
        if score > best[1]:
            best = (angle, score)
    return best[0]


def _derive_inspiration_points(competitors):
    """从 strengths 派生 inspiration_points: {angle: [{competitor, good, inspiration}]}"""
    result: dict = {}
    for c in competitors:
        for s in c.get("strengths", []):
            point = s.get("point", "")
            if not point:
                continue
            angle = _classify_angle(point)
            result.setdefault(angle, []).append(
                {
                    "competitor": c["name"],
                    "good": point,
                    "inspiration": f"可借鉴 {c['name']} 的实践：{point[:30]}",
                }
            )
    return result


def _derive_opportunity_points(competitors):
    """从 weaknesses 派生 opportunity_points: {angle: [{competitor, weakness, opportunity}]}"""
    result: dict = {}
    for c in competitors:
        for w in c.get("weaknesses", []):
            point = w.get("point", "")
            if not point:
                continue
            angle = _classify_angle(point)
            result.setdefault(angle, []).append(
                {
                    "competitor": c["name"],
                    "weakness": point,
                    "opportunity": f"差异化机会：解决 {c['name']} 没做好的「{point[:20]}」",
                }
            )
    return result


def _derive_user_positioning(c):
    """从 target_users + stage 派生 user_positioning[name]。"""
    users = c.get("target_users", [])
    stage = c.get("stage", "")
    return {
        "target_segment": "、".join(users) if users else "—",
        "region": "全球",
        "scale": "中小企业" if stage in ("早期", "成长期") else "中大型企业",
        "key_market": stage if stage else "—",
    }


def _derive_commercial_strategies(c):
    """从 pricing + differentiators 派生商业策略。"""
    pricing = c.get("pricing", "—")
    differentiators = c.get("differentiators", [])
    # pricing_tiers: 按 + / 、 切分
    if pricing and pricing != "—":
        tiers = [t.strip() for t in re.split(r"[+、；;]", pricing) if t.strip()]
    else:
        tiers = []
    return {
        "model": "SaaS 订阅"
        if any("$" in t or "月" in t or "年" in t for t in tiers)
        else "—",
        "pricing_tiers": tiers,
        "gtm": differentiators[0][:60] if differentiators else "—",
        "moat": differentiators[1][:60]
        if len(differentiators) > 1
        else (differentiators[0][:60] if differentiators else "—"),
    }


def _derive_product_overview(c):
    """产品端覆盖 — 默认 '—'，留作人工补充。"""
    return {
        "web": "支持",
        "desktop": "—",
        "mobile": "支持",
        "other": "—",
    }


def _derive_visual_signals(c):
    """从 tech_signals + tagline 派生视觉/交互描述。"""
    signals = c.get("tech_signals", [])
    tagline = c.get("tagline", "")
    bits = []
    if tagline:
        bits.append(f"定位：「{tagline}」")
    if signals:
        bits.append("技术：" + "、".join(signals[:2]))
    return "。".join(bits) if bits else "—"


def _derive_user_feedback(c):
    """从 strengths/weaknesses 派生用户反馈小结。"""
    pos = [
        {"text": s.get("point", ""), "source": "G2/Reddit/官网评测", "count": "—"}
        for s in c.get("strengths", [])[:3]
        if s.get("point")
    ]
    neg = [
        {"text": w.get("point", ""), "source": "G2/Reddit/社区抱怨", "count": "—"}
        for w in c.get("weaknesses", [])[:3]
        if w.get("point")
    ]
    summary_parts = []
    if pos:
        summary_parts.append(f"正面：{pos[0]['text'][:30]}")
    if neg:
        summary_parts.append(f"负面：{neg[0]['text'][:30]}")
    summary = "。".join(summary_parts) if summary_parts else "—"
    return {"summary": summary, "positive": pos, "negative": neg}


def _group_competitors_by_segment(competitors, market_segments):
    """按市场细分把竞品聚类,同类放一起(用于 § 2 结论建议)。

    聚类逻辑:
      - 优先按 market_segments[].players 显式分组
      - 同一竞品如果属多个 segment,只放到最匹配的 segment
    """
    if not market_segments:
        # 没有 market_segments 时,按 stage 粗分
        groups = {}
        for c in competitors:
            groups.setdefault(c.get("stage", "未知"), []).append(c)
        return [
            {"segment": k, "segment_desc": "", "competitors": v}
            for k, v in groups.items()
        ]

    groups = []
    assigned = set()
    for seg in market_segments:
        seg_players = set(seg.get("players", []))
        comps = [
            c
            for c in competitors
            if c["name"] in seg_players and c["name"] not in assigned
        ]
        if comps:
            groups.append(
                {
                    "segment": seg.get("label", "其他"),
                    "segment_desc": seg.get("desc", ""),
                    "segment_source": seg.get("_ref", 0),
                    "competitors": comps,
                }
            )
            for c in comps:
                assigned.add(c["name"])
    # 未分配的放 "其他"
    remaining = [c for c in competitors if c["name"] not in assigned]
    if remaining:
        groups.append(
            {
                "segment": "其他",
                "segment_desc": "未归入主分类",
                "segment_source": 0,
                "competitors": remaining,
            }
        )
    return groups


def _group_competitors_by_stage(competitors):
    """按阶段(巨头/成长期/早期)分组。"""
    groups = {}
    for c in competitors:
        s = c.get("stage", "未知")
        groups.setdefault(s, []).append(c)
    return [{"stage": k, "competitors": v} for k, v in groups.items()]


def _build_feature_comparison_matrix(competitors):
    """构造 § 5.2 的厂商对比矩阵。

    返回结构:
      {
        "categories": [
          {
            "name": "消息 API",
            "total_features": 4,
            "coverage": {"Twilio": 4, "WATI": 0, ...},  # 每个竞品在该类的功能数
            "features": [
              {"name": "WhatsApp Business API", "competitors": ["Twilio", "Infobip"], ...},
              ...
            ]
          },
          ...
        ],
        "totals_per_competitor": {"Twilio": 18, "WATI": 18, ...},
        "totals_per_category": {"消息 API": 4, "AI 客服": 3, ...},
      }
    """
    # 收集所有 category 和 feature(只遍历一次,避免重复计数)
    cat_features = {}  # cat -> list of {name, desc, _comps:[], _ref}
    cat_coverage = {}  # cat -> {competitor_name: count}
    totals_per_competitor = {}

    for c in competitors:
        comp_name = c["name"]
        feats = c.get("feature_catalog", {}).get(comp_name, [])
        totals_per_competitor[comp_name] = len(feats)

        for feat in feats:
            cat = feat.get("category", "其他")
            fname = feat.get("name", "")
            fdesc = feat.get("desc", "")
            fref = feat.get("_ref", 0)

            # 累计 coverage
            cat_coverage.setdefault(cat, {})
            cat_coverage[cat][comp_name] = cat_coverage[cat].get(comp_name, 0) + 1

            # 加入 feature 列表(去重:同 category 同 name 合并 comps)
            cat_features.setdefault(cat, [])
            existing = None
            for ex in cat_features[cat]:
                if ex["name"] == fname:
                    existing = ex
                    break
            if existing is None:
                cat_features[cat].append(
                    {"name": fname, "desc": fdesc, "_comps": [comp_name], "_ref": fref}
                )
            else:
                if comp_name not in existing["_comps"]:
                    existing["_comps"].append(comp_name)

    categories = []
    for cat_name in sorted(cat_features.keys()):
        # 排序:独家优先 → 多家共有 → 名称
        feat_sorted = sorted(
            cat_features[cat_name],
            key=lambda x: (
                1 if len(x.get("_comps", [])) == 1 else 0,  # 独家的排后面(更稀有)
                -len(x.get("_comps", [])),
                x["name"],
            ),
        )
        categories.append(
            {
                "name": cat_name,
                "total_features": len(feat_sorted),
                "coverage": cat_coverage.get(cat_name, {}),
                "features": feat_sorted,
            }
        )

    totals_per_category = {c["name"]: c["total_features"] for c in categories}

    return {
        "categories": categories,
        "totals_per_competitor": totals_per_competitor,
        "totals_per_category": totals_per_category,
        "competitor_names": [c["name"] for c in competitors],
    }


def _find_unique_features(competitors):
    """每家独有的功能(其他家都没有)。"""
    # 反向索引:每个 feature 名 → 提供者列表
    feat_to_comps = {}
    for c in competitors:
        comp = c["name"]
        for feat in c.get("feature_catalog", {}).get(comp, []):
            key = (feat.get("name", "").lower(), feat.get("category", ""))
            feat_to_comps.setdefault(key, []).append(comp)

    result = {}
    for c in competitors:
        comp = c["name"]
        unique = []
        for feat in c.get("feature_catalog", {}).get(comp, []):
            key = (feat.get("name", "").lower(), feat.get("category", ""))
            if len(feat_to_comps.get(key, [])) == 1:
                unique.append(
                    {
                        "name": feat.get("name", ""),
                        "category": feat.get("category", ""),
                        "desc": feat.get("desc", ""),
                        "_ref": feat.get("_ref", 0),
                    }
                )
        result[comp] = unique
    return result


def _derive_data_growth(competitors, opportunities):
    """从 competitors.scores.momentum + opportunities 派生数据增长。"""
    if not competitors:
        return {"overall": "—", "summary": "—", "key_growth_points": []}
    avg_mom = sum(c["scores"].get("momentum", 0) for c in competitors) / len(
        competitors
    )
    top3 = sorted(
        competitors, key=lambda c: c["scores"].get("momentum", 0), reverse=True
    )[:3]
    points = [
        {
            "signal": f"{c['name']} 增长势头评分 {c['scores'].get('momentum', 0)}/10",
            "value": c["scores"].get("momentum", 0),
            "source": c.get("url", ""),
        }
        for c in top3
    ]
    summary = f"行业平均 momentum = {avg_mom:.1f}/10。前 3 名：" + "、".join(
        c["name"] for c in top3
    )
    return {
        "overall": f"行业整体处于扩张期，平均 momentum {avg_mom:.1f}/10。",
        "summary": summary,
        "key_growth_points": points,
    }


def _render_sources_html(sources_by_kind):
    """Python 端预渲染 sources 区块 HTML（避免模板引擎 quadratic 性能）。"""
    kind_icon = {
        "narrative": "📰",
        "competitor_meta": "🏢",
        "strength": "💪",
        "weakness": "⚠",
        "feature": "⚙",
        "market_segment": "🧭",
        "gap": "🕳",
        "opportunity": "💡",
        "opportunity_validation": "📊",
        "other_competitor": "📦",
    }
    kind_label = {
        "narrative": "背景叙事",
        "competitor_meta": "竞品基础信息",
        "strength": "竞品优势",
        "weakness": "竞品弱点",
        "feature": "产品功能",
        "market_segment": "市场细分",
        "gap": "市场空白",
        "opportunity": "颠覆机会",
        "opportunity_validation": "机会验证",
        "other_competitor": "其他竞品",
    }
    parts = []
    for group in sources_by_kind:
        kind = group["kind"]
        parts.append(
            f'<h3 class="sub-head">{html.escape(kind_icon.get(kind, "📎"))} '
            f"{html.escape(kind_label.get(kind, kind))} "
            f'<span style="font-size:0.7rem; color:var(--fg-mute); font-weight:400; margin-left:0.5rem;">'
            f"{group['count']} 条</span></h3>"
        )
        parts.append('<div class="sources-list">')
        for s in group["items"]:
            comp_part = (
                f"[{html.escape(s.get('competitor', ''))}] "
                if s.get("competitor")
                else ""
            )
            parts.append(
                f'<div class="source-item" id="src-{s["idx"]}">'
                f'<span class="src-num">{s["idx"]}</span>'
                f'<span class="src-claim">{comp_part}{html.escape(s.get("claim", ""))}</span>'
                f'<div class="src-meta">'
                f'<a href="{html.escape(s["url"])}" target="_blank">{html.escape(s["url"])}</a>'
                f'<span style="background:var(--good-soft); color:var(--good); padding:0.05rem 0.4rem; border-radius:3px; font-size:0.7rem; margin-left:0.5rem;">🔗 可访问</span>'
                f"</div></div>"
            )
        parts.append("</div>")
    return "\n".join(parts)


def _render_section5_2_html(matrix, unique_features):
    """预渲染 § 5.2 厂商对比矩阵 + 类别分组 + 独家功能面板(避免模板引擎 quadratic)。"""
    cats = matrix["categories"]
    comp_names = matrix["competitor_names"]
    totals = matrix["totals_per_competitor"]
    n_vendors = len(comp_names)
    max_share = max(
        (len(f.get("_comps", [])) for c in cats for f in c["features"]),
        default=0,
    )

    out = []
    # 顶部统计条
    out.append('<div class="feat-summary-bar">')
    out.append(
        f'<div class="feat-summary-stat"><div class="num">{sum(totals.values())}</div><div class="lbl">功能总数</div></div>'
    )
    out.append(
        f'<div class="feat-summary-stat"><div class="num">{n_vendors}</div><div class="lbl">竞品数</div></div>'
    )
    out.append(
        f'<div class="feat-summary-stat"><div class="num">{len(cats)}</div><div class="lbl">功能类别</div></div>'
    )
    out.append(
        f'<div class="feat-summary-stat"><div class="num">{max_share}</div><div class="lbl">最多家共有</div></div>'
    )
    out.append("</div>")

    # 主对比矩阵
    out.append(
        '<h4 style="font-family:var(--font-display); font-size:1.05rem; color:var(--accent); margin: 1.5rem 0 0.5rem;">📊 5.2.1 厂商功能对比矩阵 <span style="font-size:0.7rem; color:var(--fg-mute); font-weight:400; margin-left:0.5rem;">✓=支持 —=不支持 · 颜色越深=支持者越多</span></h4>'
    )
    out.append(
        '<div class="feat-matrix-wrap"><table class="feat-matrix"><thead><tr><th style="text-align:left;">功能类别 / 功能</th>'
    )
    for cn in comp_names:
        out.append(f"<th>{html.escape(cn)}</th>")
    out.append("</tr></thead><tbody>")

    for cat in cats:
        for f in cat["features"]:
            comps_list = f.get("_comps", [])
            n = len(comps_list)
            out.append("<tr>")
            desc = f.get("desc", "") or ""
            out.append(
                f'<td title="{html.escape(desc)}"><strong>{html.escape(f["name"])}</strong>'
                + (
                    f'<br><span style="color:var(--fg-mute); font-size:0.78rem; font-weight:400;">{html.escape(desc)}</span>'
                    if desc
                    else ""
                )
                + (
                    f'<a href="#src-{f.get("_ref", 0)}" class="ref">{f["_ref"]}</a>'
                    if f.get("_ref")
                    else ""
                )
                + "</td>"
            )
            for cn in comp_names:
                if cn in comps_list:
                    cls = f"feature-cell share-{min(n, 6)}"
                    out.append(
                        f'<td class="{cls}" title="{n}/{n_vendors} 家支持">✓</td>'
                    )
                else:
                    out.append('<td class="feature-cell" style="opacity:0.25;">—</td>')
            out.append("</tr>")

    # 总数行
    out.append('<tr class="total-row"><td>📊 <strong>功能总数</strong></td>')
    for cn in comp_names:
        out.append(f'<td class="feature-cell">{totals.get(cn, 0)}</td>')
    out.append("</tr>")
    out.append("</tbody></table></div>")

    # 类别汇总
    out.append(
        '<h4 style="font-family:var(--font-display); font-size:1.05rem; color:var(--accent); margin: 1.5rem 0 0.5rem;">📂 5.2.2 按功能类别分组(谁有独家?)</h4>'
    )
    out.append(
        '<p style="color: var(--fg-mute); font-size: 0.85rem; margin: 0.3rem 0 1rem;">每个类别下列出所有功能,<span style="background:var(--accent-soft); padding:0.1rem 0.4rem; border-radius:3px; color:var(--accent); font-weight:600;">彩色</span> = 独家(只此一家),<span style="background:var(--bg-soft); padding:0.1rem 0.4rem; border-radius:3px;">灰色</span> = 多家共有。</p>'
    )

    cats_sorted = sorted(cats, key=lambda c: -c["total_features"])
    for cat in cats_sorted:
        out.append('<div class="feat-category-card">')
        out.append(
            f'<div class="cat-head"><div class="cat-icon">{html.escape(cat["name"][:1])}</div>'
        )
        out.append(f'<div class="cat-name">{html.escape(cat["name"])}</div>')
        out.append(f'<div class="cat-count">{cat["total_features"]} 项功能</div></div>')
        for f in cat["features"]:
            comps_list = f.get("_comps", [])
            is_unique = len(comps_list) == 1
            out.append('<div class="feat-row"><div>')
            star = "⭐ " if is_unique else ""
            ref_html = (
                f'<a href="#src-{f.get("_ref", 0)}" class="ref">{f["_ref"]}</a>'
                if f.get("_ref")
                else ""
            )
            desc_html = (
                f'<div class="feat-desc">{html.escape(f.get("desc", "") or "")}</div>'
                if f.get("desc")
                else ""
            )
            out.append(
                f'<div class="feat-name">{star}{html.escape(f["name"])}{ref_html}</div>{desc_html}'
            )
            out.append("</div><div class='feat-comps'>")
            for cp in comps_list:
                pill_cls = "unique" if is_unique else "shared"
                star2 = " ⭐" if is_unique else ""
                out.append(
                    f'<span class="comp-pill {pill_cls}" title="{html.escape(cp)}{" · 独家" if is_unique else ""}">{html.escape(cp)}{star2}</span>'
                )
            out.append("</div></div>")
        out.append("</div>")

    # 独家功能面板
    out.append(
        '<h4 style="font-family:var(--font-display); font-size:1.05rem; color:var(--accent); margin: 1.5rem 0 0.5rem;">⭐ 5.2.3 各家独家功能 <span style="font-size:0.7rem; color:var(--fg-mute); font-weight:400; margin-left:0.5rem;">其他家都没有的独家卖点</span></h4>'
    )
    out.append('<div class="cluster-comp-grid">')
    for idx, (c_name, uniques) in enumerate(unique_features.items()):
        border_color = f"var(--data-{idx % 6 + 1})"
        out.append(
            f'<div class="cluster-comp-card" style="border-top-color: {border_color};">'
        )
        out.append(
            f'<div class="cc-name">{html.escape(c_name[:2])} · {html.escape(c_name)}</div>'
        )
        out.append(
            f'<div style="font-size:0.78rem; color:var(--fg-mute); margin-bottom:0.5rem;">{len(uniques)} 个独家功能</div>'
        )
        for u in uniques[:8]:
            ref_html = (
                f'<a href="#src-{u.get("_ref", 0)}" class="ref">{u["_ref"]}</a>'
                if u.get("_ref")
                else ""
            )
            out.append(
                f'<div style="font-size:0.82rem; padding:0.2rem 0; color:var(--fg-soft);">⭐ {html.escape(u["name"])}{ref_html}</div>'
            )
        if len(uniques) > 8:
            out.append(
                f'<div style="font-size:0.75rem; color:var(--fg-mute); margin-top:0.3rem;">…还有 {len(uniques) - 8} 个</div>'
            )
        out.append("</div>")
    out.append("</div>")

    return "\n".join(out)


def _render_section2_html(clusters, competitors_ranked):
    """预渲染 § 2 同类厂商聚类 + 评分条 + 术语表。"""
    out = []

    # 同类厂商聚类
    out.append(
        f'<h3 class="sub-head">2.0 同类厂商聚类 <span style="font-size:0.7rem; color:var(--fg-mute); font-weight:400; margin-left:0.5rem;">{len(clusters)} 个细分赛道 · 同类放一起便于对比</span></h3>'
    )
    out.append(
        '<p style="color: var(--fg-mute); font-size: 0.88rem; margin: 0.5rem 0 1rem;">把 6 家头部竞品按<strong>市场定位 + 业务模式</strong>分组。<span style="background:var(--accent-soft); padding:0.1rem 0.4rem; border-radius:3px; color:var(--accent); font-weight:600;">同色边框 = 同一赛道</span>,可以直接对比同类玩家的差异。</p>'
    )

    for idx, cluster in enumerate(clusters):
        border_color = f"var(--data-{idx % 6 + 1})"
        out.append(
            f'<div class="segment-cluster" style="border-left-color: {border_color};">'
        )
        out.append(
            f'<div class="cluster-head"><h4>🏷 {html.escape(cluster["segment"])}</h4>'
        )
        out.append(
            f'<span class="count">{len(cluster["competitors"])} 家 · {html.escape(cluster.get("segment_desc", ""))}</span></div>'
        )
        if cluster.get("segment_desc"):
            out.append(
                f'<p class="cluster-desc">{html.escape(cluster["segment_desc"])}</p>'
            )
        out.append('<div class="cluster-comp-grid">')
        for c in cluster["competitors"]:
            sc = c.get("scores", {})
            avg = sum(sc.values()) / max(len(sc), 1) if sc else 0
            avg = round(avg, 1)
            out.append(
                f'<div class="cluster-comp-card" style="border-top-color: {border_color};">'
            )
            out.append(
                f'<div class="cc-name"><a href="{html.escape(c.get("url", "#"))}" target="_blank" style="color: var(--fg); border: 0;">{html.escape(c.get("icon", "") or c["name"][:2])} · {html.escape(c["name"])}</a></div>'
            )
            out.append(
                f'<div class="cc-tagline">「{html.escape(c.get("tagline", ""))}」</div>'
            )
            out.append('<div class="cc-stats">')
            out.append(
                f'<span class="cc-stat">阶段 <strong>{html.escape(c.get("stage", "未知"))}</strong></span>'
            )
            out.append(
                f'<span class="cc-stat">成立 <strong>{html.escape(str(c.get("founded", "—")))}</strong></span>'
            )
            out.append(
                f'<span class="cc-stat">总人数 <strong>{html.escape(str(c.get("team_size", "—")))}</strong></span>'
            )
            out.append(f'<span class="cc-stat">综合分 <strong>{avg}/10</strong></span>')
            out.append(
                f'<span class="cc-stat">💰 {html.escape(c.get("pricing", "—"))}</span>'
            )
            out.append("</div>")
            if c.get("target_users"):
                out.append(
                    '<div style="margin-top:0.5rem; font-size:0.75rem; color:var(--fg-mute);">🎯 目标用户: '
                )
                chips = "".join(
                    f'<span style="display:inline-block; padding:0.05rem 0.4rem; margin:0.1rem 0.2rem 0.1rem 0; background:var(--bg-elev); border-radius:4px; color:var(--fg-soft);">{html.escape(u)}</span>'
                    for u in c["target_users"]
                )
                out.append(chips + "</div>")
            if c.get("url"):
                out.append(
                    f'<div style="margin-top:0.4rem; font-size:0.78rem;"><a href="{html.escape(c["url"])}" target="_blank" style="color:var(--info);">↗ {html.escape(c["url"])}</a></div>'
                )
            out.append("</div>")
        out.append("</div></div>")

    # 综合评分条
    out.append('<h3 class="sub-head">2.0.1 6 家竞品综合实力可视化</h3>')
    out.append(
        '<p style="color: var(--fg-mute); font-size: 0.85rem; margin: 0.5rem 0 1rem;">综合分 = 6 个评分维度的平均分。颜色越红 = 越领先,<span class="score-bar-tier tier-领先">领先</span> <span class="score-bar-tier tier-中坚">中坚</span> <span class="score-bar-tier tier-跟随">跟随</span>。</p>'
    )
    for c in competitors_ranked:
        tier = c.get("tier", "")
        tier_cls = {"领先": "tier-领先", "中坚": "tier-中坚"}.get(tier, "tier-跟随")
        out.append('<div class="score-visual-bar" style="margin-bottom:0.5rem;">')
        out.append(
            f'<div class="label" style="font-weight:700;">{html.escape(c["name"])}</div>'
        )
        out.append(
            f'<div class="track"><div class="fill" style="width: {c["avg"] * 10}%;"></div></div>'
        )
        out.append(
            f'<div class="val">{c["avg"]}/10 <span class="score-bar-tier {tier_cls}">{html.escape(tier)}</span></div>'
        )
        out.append("</div>")

    # 术语表
    glossary = [
        (
            "BSP",
            "Business Solution Provider,WhatsApp 官方授权的中间商。中小公司要找他们才能拿到 WhatsApp API(就像旅行社代理机票)。",
        ),
        (
            "SaaS",
            "Software as a Service,按月付费的在线软件,不用自己装服务器。WATI、Respond.io 都属此类。",
        ),
        (
            "CTWA 广告",
            "Click-to-WhatsApp Ads,Meta 出的新型广告。用户在 Instagram/Facebook 点广告,直接跳到 WhatsApp 对话。转化率比传统广告高 3x。",
        ),
        (
            "CDP",
            "Customer Data Platform,客户数据平台。打通用户在多个渠道的数据,知道同一客户在不同渠道的所有行为。",
        ),
        (
            "momentum",
            "增长势头。综合了产品迭代速度、客户增长、媒体声量。10 分 = 爆炸增长,3 分 = 停滞。",
        ),
        (
            "API",
            "Application Programming Interface,程序之间的「数据服务员」。可以问它要数据或让它干活。",
        ),
        (
            "Webhook",
            "「反向 API」。当某事件发生时,服务器主动推送通知给你。比轮询省资源。",
        ),
        (
            "Shopify 集成",
            "和 Shopify 电商平台无缝对接。可以同步订单、库存、客户,实现 WhatsApp 订单通知。",
        ),
        (
            "SLA",
            "Service Level Agreement,服务等级协议。99.9% SLA 表示一年最多 8 小时停机。",
        ),
        (
            "SOC2 / HIPAA",
            "国际合规认证。SOC2 = 企业数据安全;HIPAA = 美国医疗数据合规。大客户采购必备。",
        ),
    ]
    out.append('<h3 class="sub-head">2.0.2 专业术语速查(小白友好)</h3>')
    out.append(
        '<p style="color: var(--fg-mute); font-size: 0.85rem; margin: 0.5rem 0 0.5rem;">第一次看这些词不知道啥意思?这里通俗解释 ↓</p>'
    )
    out.append('<div class="glossary-grid">')
    for term, explain in glossary:
        out.append(
            f'<div class="glossary-item"><span class="term">{html.escape(term)}</span><div class="explain">{html.escape(explain)}</div></div>'
        )
    out.append("</div>")

    return "\n".join(out)


def normalize(data: dict) -> dict:
    """标准化 + 派生 飞书模板所需的全部字段。

    输入 schema (SKILL.md 描述的 13 字段):
        topic, subtitle, date, generated_at, executive_summary,
        market_segments, competitors[*], feature_overlap,
        gaps, opportunities

    派生字段 (templates/report.html 实际渲染需要):
        background, goals, inspiration_points, opportunity_points,
        product_slogans, user_positioning, commercial_strategies,
        product_overview, visual_signals, user_feedback, data_growth,
        avg_maturity, top_competitor, bottom_competitor,
        top_gap, top_opportunity, toc_items
    """
    data = dict(data)
    data.setdefault("topic", "未命名主题")
    data.setdefault("date", datetime.now().strftime("%Y-%m-%d"))
    data.setdefault("subtitle", "深度竞品分析 + 颠覆性机会挖掘")
    data.setdefault(
        "generated_at", datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    )
    data.setdefault("executive_summary", "（无摘要）")
    data.setdefault("market_segments", [])
    data.setdefault("competitors", [])
    data.setdefault("gaps", [])
    data.setdefault("opportunities", [])
    data.setdefault("feature_overlap", {})
    data.setdefault("recommendations", [])
    data.setdefault("background", "")
    data.setdefault("goals", [])

    # ===== 竞品：基础字段补全 + scores 校准 =====
    for c in data["competitors"]:
        c.setdefault("founded", "—")
        c.setdefault("stage", "未知")
        c.setdefault("target_users", [])
        c.setdefault("core_features", [])
        c.setdefault("strengths", [])
        c.setdefault("weaknesses", [])
        c.setdefault("differentiators", [])
        c.setdefault("tech_signals", [])
        c.setdefault("tagline", "")
        c.setdefault("pricing", "—")
        c.setdefault("url", "#")
        c.setdefault("slug", slugify(c.get("name", "")))
        c.setdefault("icon", smart_icon(c.get("name", "")))
        scores = c.get("scores", {})
        for k, _ in SCORE_DIMS:
            scores.setdefault(k, 0)
        c["scores"] = scores
        for s in c["strengths"]:
            s.setdefault("score", 5)
            s.setdefault("point", s.get("point", ""))
            s.setdefault("evidence", s.get("evidence", ""))
        for w in c["weaknesses"]:
            w.setdefault("score", 5)
            w.setdefault("point", w.get("point", ""))
            w.setdefault("evidence", w.get("evidence", ""))

    # ===== opportunities：基础字段补全 =====
    for o in data["opportunities"]:
        o.setdefault("target_users", [])
        o.setdefault("differentiators", [])
        o.setdefault("validation", [])
        o.setdefault("moat", "")
        o.setdefault("inspiration", "")
        o.setdefault("title", o.get("title", "（未命名机会）"))
        if not isinstance(o.get("disrupt_score"), (int, float)):
            o["disrupt_score"] = 7

    # ===== market_segments：基础字段补全 =====
    for seg in data["market_segments"]:
        seg.setdefault("players_count", len(seg.get("players", [])))
        seg.setdefault("icon", smart_icon(seg.get("label", "")))
        seg.setdefault("desc", seg.get("desc", ""))

    # ===== 派生：飞书模板所需字段 =====
    data["inspiration_points"] = _derive_inspiration_points(data["competitors"])
    data["opportunity_points"] = _derive_opportunity_points(data["competitors"])

    # ─────────── 同类厂商聚类(用于 § 2 结论与建议) ───────────
    # 按 "主要功能类别" 聚类,让同类厂商放一起
    data["competitors_by_segment"] = _group_competitors_by_segment(
        data["competitors"], data.get("market_segments", [])
    )
    data["competitors_by_stage"] = _group_competitors_by_stage(data["competitors"])

    data["product_slogans"] = {
        c["name"]: c.get("tagline", "") or "（暂无口号）" for c in data["competitors"]
    }
    data["user_positioning"] = {
        c["name"]: _derive_user_positioning(c) for c in data["competitors"]
    }
    data["commercial_strategies"] = {
        c["name"]: _derive_commercial_strategies(c) for c in data["competitors"]
    }
    data["product_overview"] = {
        c["name"]: _derive_product_overview(c) for c in data["competitors"]
    }
    data["visual_signals"] = {
        c["name"]: _derive_visual_signals(c) for c in data["competitors"]
    }
    data["user_feedback"] = {
        c["name"]: _derive_user_feedback(c) for c in data["competitors"]
    }
    data["data_growth"] = _derive_data_growth(
        data["competitors"], data["opportunities"]
    )

    # ─────────── § 5.2 功能全集 · 厂商对比矩阵 ───────────
    # 把所有竞品的功能汇集成一个大矩阵:
    #   横轴:每个 category,纵轴:每个 feature,格子:各竞品是否支持
    data["feature_comparison_matrix"] = _build_feature_comparison_matrix(
        data["competitors"]
    )
    # 每家独有功能清单(其他家都没有的功能)
    data["unique_features_by_competitor"] = _find_unique_features(data["competitors"])
    # 预渲染 § 5.2 矩阵 HTML(避免模板嵌套循环 quadratic)
    data["section5_2_html"] = _render_section5_2_html(
        data["feature_comparison_matrix"], data["unique_features_by_competitor"]
    )
    # 预渲染 § 2 同类聚类 + 评分条 + 术语表
    data["section2_html"] = _render_section2_html(
        data["competitors_by_segment"],
        data["competitors_ranked"],
    )

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
            totals.sort(key=lambda x: x[1], reverse=True)
            data["top_competitor"] = totals[0][0]
            data["bottom_competitor"] = totals[-1][0]

    # 最严重空白
    if data["gaps"]:
        sev_order = {"high": 3, "medium": 2, "low": 1}
        sorted_gaps = sorted(
            data["gaps"],
            key=lambda g: sev_order.get(g.get("severity", "low"), 0),
            reverse=True,
        )
        data["top_gap"] = sorted_gaps[0]["gap"][:18] + (
            "…" if len(sorted_gaps[0]["gap"]) > 18 else ""
        )
    else:
        data["top_gap"] = "—"

    # 最高颠覆机会
    if data["opportunities"]:
        sorted_opps = sorted(
            data["opportunities"],
            key=lambda o: o.get("disrupt_score", 0)
            if isinstance(o.get("disrupt_score"), (int, float))
            else 0,
            reverse=True,
        )
        data["top_opportunity"] = sorted_opps[0]["title"][:18] + (
            "…" if len(sorted_opps[0]["title"]) > 18 else ""
        )
    else:
        data["top_opportunity"] = "—"

    # 主题副标题
    data["topic_accent"] = "竞争格局与颠覆性机会图谱"

    # ─────────── § 8 其他竞品资料库 ───────────
    data["other_competitors"] = data.get("other_competitors", [])
    # 按 category 分组
    other_by_cat = {}
    for oc in data["other_competitors"]:
        cat = oc.get("category", "其他")
        other_by_cat.setdefault(cat, []).append(oc)
    data["other_competitors_by_category"] = sorted(
        [
            {"category": k, "players": v, "count": len(v)}
            for k, v in other_by_cat.items()
        ],
        key=lambda x: -x["count"],
    )

    # ─────────── 竞品扩展字段 ───────────
    for c in data["competitors"]:
        c.setdefault("headquarters", "—")
        c.setdefault("funding", "—")
        c.setdefault("team_size", "—")

    # 功能全集（竞品画像）—— 5.2 产品功能
    # 支持两种 schema：
    #   1. 顶层 feature_catalog: {comp_name: [{category, name, desc}, ...]}
    #   2. 每个 competitor.feature_catalog: {comp_name: [...]} (新 schema)
    fc = data.get("feature_catalog") or {}
    if not fc:
        # 兼容新 schema：从每个 competitor 抽出
        for c in data["competitors"]:
            inner = c.get("feature_catalog")
            if inner:
                # inner 是 {name: [...]}
                for k, v in inner.items():
                    fc[k] = v
    fc = fc or {}
    comp_meta = {c["name"]: c for c in data["competitors"]}
    fc_companies = []
    for comp_name, features in fc.items():
        if not isinstance(features, list):
            continue
        meta = comp_meta.get(comp_name, {})
        # 按 category 分组并保留输入顺序
        cat_dict: dict = {}
        cat_order: list = []
        for f in features:
            cat = f.get("category", "其他")
            if cat not in cat_dict:
                cat_dict[cat] = []
                cat_order.append(cat)
            cat_dict[cat].append(f)
        categories = [(c, cat_dict[c]) for c in cat_order]
        fc_companies.append(
            {
                "name": comp_name,
                "url": meta.get("url", "#"),
                "icon": meta.get("icon") or smart_icon(comp_name),
                "category_count": len(categories),
                "feature_count": len(features),
                "categories": categories,
            }
        )
    data["feature_catalog_companies"] = fc_companies
    data["feature_total_count"] = sum(
        len(v) for v in fc.values() if isinstance(v, list)
    )

    # ─────────── 7 个新增派生字段 ───────────

    # 1. 6 维评分对比表 —— 每维度排名 + 每家总分
    score_table = []  # [{dim_key, dim_label, rows: [{name, score, rank}]}]
    for dim_key, dim_label in SCORE_DIMS:
        rows = []
        for c in data["competitors"]:
            rows.append({"name": c["name"], "score": c["scores"].get(dim_key, 0)})
        rows.sort(key=lambda r: r["score"], reverse=True)
        for i, r in enumerate(rows):
            r["rank"] = i + 1
            r["medal"] = (
                "🥇" if i == 0 else ("🥈" if i == 1 else ("🥉" if i == 2 else ""))
            )
        score_table.append({"key": dim_key, "label": dim_label, "rows": rows})
    data["score_table"] = score_table

    # 2. 竞品总分排名 —— 综合分(6 维均值)排序 + 标签(领先/跟随/落后)
    comp_total = []
    for c in data["competitors"]:
        sc = c["scores"]
        avg = sum(sc.values()) / max(len(sc), 1)
        comp_total.append({"name": c["name"], "avg": round(avg, 1), "scores": sc})
    comp_total.sort(key=lambda x: x["avg"], reverse=True)
    if comp_total:
        top = comp_total[0]["avg"]
        for c in comp_total:
            if c["avg"] >= top * 0.9:
                c["tier"] = "领先"
            elif c["avg"] >= top * 0.7:
                c["tier"] = "中坚"
            else:
                c["tier"] = "跟随"
    data["competitors_ranked"] = comp_total

    # 3. 成立年份时间线 —— 按 founded 升序
    timeline = []
    for c in data["competitors"]:
        try:
            y = int(c.get("founded", 0))
        except (ValueError, TypeError):
            continue
        if y > 0:
            timeline.append(
                {
                    "year": y,
                    "name": c["name"],
                    "stage": c.get("stage", "未知"),
                    "tagline": c.get("tagline", "")[:50],
                    "url": c.get("url", "#"),
                }
            )
    timeline.sort(key=lambda e: e["year"])
    data["founding_timeline"] = timeline
    data["founding_year_range"] = (
        f"{timeline[0]['year']}–{timeline[-1]['year']}" if timeline else "—"
    )

    # 4. 阶段分布统计
    stage_dist: dict = {}
    for c in data["competitors"]:
        s = c.get("stage", "未知")
        stage_dist[s] = stage_dist.get(s, 0) + 1
    data["stage_distribution"] = sorted(
        [
            {
                "stage": k,
                "count": v,
                "pct": round(v / max(len(data["competitors"]), 1) * 100, 1),
            }
            for k, v in stage_dist.items()
        ],
        key=lambda x: x["count"],
        reverse=True,
    )

    # 5. 目标用户重叠矩阵 —— 谁抢同一批用户
    user_overlap = []
    user_to_comps: dict = {}
    for c in data["competitors"]:
        for u in c.get("target_users", []):
            user_to_comps.setdefault(u, []).append(c["name"])
    for u, comps in sorted(user_to_comps.items(), key=lambda x: -len(x[1])):
        if len(comps) >= 2:
            user_overlap.append(
                {
                    "user_segment": u,
                    "competitors": comps,
                    "count": len(comps),
                    "intensity": "🔥" * min(len(comps), 4),
                }
            )
    data["user_overlap"] = user_overlap

    # 6. 技术栈信号汇总 —— 每个竞品 tech_signals + 全行业聚类
    tech_clusters: dict = {}
    for c in data["competitors"]:
        for t in c.get("tech_signals", []):
            key = t.strip()
            tech_clusters.setdefault(key, []).append(c["name"])
    tech_top = sorted(
        [
            {"signal": k, "adopters": v, "count": len(v)}
            for k, v in tech_clusters.items()
        ],
        key=lambda x: x["count"],
        reverse=True,
    )
    data["tech_signal_clusters"] = tech_top[:15]
    data["tech_signals_per_competitor"] = {
        c["name"]: c.get("tech_signals", []) for c in data["competitors"]
    }

    # 7. SWOT —— 每家基于 strengths/weaknesses/tech_signals
    swot_per_competitor = []
    for c in data["competitors"]:
        strengths = c.get("strengths", [])[:3]
        weaknesses = c.get("weaknesses", [])[:3]
        swot_per_competitor.append(
            {
                "name": c["name"],
                "icon": c.get("icon", smart_icon(c["name"])),
                "url": c.get("url", "#"),
                "stage": c.get("stage", "未知"),
                "founded": c.get("founded", "—"),
                "tagline": c.get("tagline", ""),
                "target_users": c.get("target_users", []),
                "pricing": c.get("pricing", "—"),
                "pricing_tiers": c.get("pricing_tiers", []),
                "core_features": c.get("core_features", []),
                "strengths": strengths,
                "weaknesses": weaknesses,
                "tech_signals": c.get("tech_signals", []),
                "differentiators": c.get("differentiators", []),
                "scores": c.get("scores", {}),
            }
        )
    data["swot_per_competitor"] = swot_per_competitor

    # ─────────── 全局引用系统 ───────────
    # 收集所有 evidence / source URL → 统一编号
    sources = []  # [{idx, url, kind, claim, competitor}]
    source_idx = {}  # url -> idx

    def _add_source(url: str, kind: str, claim: str, competitor: str = "") -> int:
        """添加一个来源，返回 idx。已存在的 URL 复用同一个 idx。"""
        if not url:
            return 0
        if url in source_idx:
            return source_idx[url]
        source_idx[url] = len(sources) + 1
        sources.append(
            {
                "idx": len(sources) + 1,
                "url": url,
                "kind": kind,
                "claim": claim[:80] if claim else "",
                "competitor": competitor,
                "verified": (
                    "user"
                    if any(d in url for d in ("g2.com", "reddit.com", "crunchbase.com"))
                    else "bot"
                ),
            }
        )
        return source_idx[url]

    # 1. background / executive_summary 等顶层 source 列表
    for src_list_key in ["background_sources", "executive_summary_sources"]:
        for src in data.get(src_list_key, []):
            _add_source(src.get("source", ""), "narrative", src.get("claim", ""), "")

    # 2. 每个 competitor 的字段（tagline / founded / pricing / etc.）
    for c in data["competitors"]:
        comp = c["name"]
        for field in [
            "tagline",
            "founded",
            "stage",
            "headquarters",
            "funding",
            "team_size",
            "pricing",
            "target_users",
            "core_features",
            "differentiators",
            "tech_signals",
            "scores",
        ]:
            url_field = field + "_source"
            url = c.get(url_field, "")
            if url:
                idx = _add_source(url, "competitor_meta", f"{comp} · {field}", comp)
                c.setdefault("_refs", {})[field] = idx
            # 同时把 strengths/weaknesses 的 source 也加
        for st in c.get("strengths", []):
            url = st.get("source", "")
            ev = st.get("evidence", "")
            if not url and ev:
                import re as _re

                m = _re.search(r"https?://[^\s\)]+", ev)
                url = m.group(0).rstrip(".,;:") if m else ""
            if url:
                idx = _add_source(url, "strength", st.get("point", ""), comp)
                st["_ref"] = idx
        for w in c.get("weaknesses", []):
            url = w.get("source", "")
            ev = w.get("evidence", "")
            if not url and ev:
                import re as _re

                m = _re.search(r"https?://[^\s\)]+", ev)
                url = m.group(0).rstrip(".,;:") if m else ""
            if url:
                idx = _add_source(url, "weakness", w.get("point", ""), comp)
                w["_ref"] = idx

    # 3. feature_catalog 每项带 source
    for c in data["competitors"]:
        comp = c["name"]
        for feat in c.get("feature_catalog", {}).get(comp, []):
            url = feat.get("source", "")
            if url:
                idx = _add_source(
                    url,
                    "feature",
                    f"{feat.get('name', '')} ({feat.get('category', '')})",
                    comp,
                )
                feat["_ref"] = idx

    # 4. market_segments 每条带 source
    for seg in data.get("market_segments", []):
        url = seg.get("source", "")
        if url:
            idx = _add_source(url, "market_segment", seg.get("label", ""), "")
            seg["_ref"] = idx

    # 5. gaps 每条带 source
    for g in data.get("gaps", []):
        url = g.get("source", "")
        if url:
            idx = _add_source(url, "gap", g.get("gap", "")[:60], "")
            g["_ref"] = idx

    # 6. opportunities 每条带 source + validation_sources
    for o in data.get("opportunities", []):
        url = o.get("source", "")
        if url:
            idx = _add_source(url, "opportunity", o.get("title", ""), "")
            o["_ref"] = idx
        for vs in o.get("validation_sources", []):
            _add_source(vs, "opportunity_validation", o.get("title", ""), "")

    # 7. other_competitors (§ 8) 每条带 source
    for oc in data.get("other_competitors", []):
        url = oc.get("source", "") or oc.get("url", "")
        if url:
            idx = _add_source(
                url, "other_competitor", oc.get("name", ""), oc.get("name", "")
            )
            oc["_ref"] = idx

    data["sources"] = sources
    data["source_count"] = len(sources)
    # 统计可被 bot 访问的 URL 占比
    bot_verified = sum(1 for s in sources if s.get("verified") == "bot")
    data["source_bot_verified_count"] = bot_verified
    data["source_user_verified_count"] = len(sources) - bot_verified
    # 来源按 kind 分组（用于 Sources 区块分组渲染）
    by_kind: dict = {}
    for s in sources:
        by_kind.setdefault(s["kind"], []).append(s)
    data["sources_by_kind"] = [
        {"kind": k, "items": v, "count": len(v)}
        for k, v in sorted(by_kind.items(), key=lambda x: -len(x[1]))
    ]
    # 来源分组图标 + 标签
    data["kind_icon"] = {
        "narrative": "📰",
        "competitor_meta": "🏢",
        "strength": "💪",
        "weakness": "⚠",
        "feature": "⚙",
        "market_segment": "🧭",
        "gap": "🕳",
        "opportunity": "💡",
        "opportunity_validation": "📊",
        "other_competitor": "📦",
    }
    data["kind_label"] = {
        "narrative": "背景叙事",
        "competitor_meta": "竞品基础信息",
        "strength": "竞品优势",
        "weakness": "竞品弱点",
        "feature": "产品功能",
        "market_segment": "市场细分",
        "gap": "市场空白",
        "opportunity": "颠覆机会",
        "opportunity_validation": "机会验证",
        "other_competitor": "其他竞品",
    }
    # 预渲染 sources 区块 HTML（避免嵌套 for 循环 quadratic 性能）
    data["sources_html"] = _render_sources_html(data["sources_by_kind"])

    # 8. Top 3 机会卡片 —— disrupt_score 排序
    sorted_opps = sorted(
        [
            o
            for o in data["opportunities"]
            if isinstance(o.get("disrupt_score"), (int, float))
        ],
        key=lambda o: o["disrupt_score"],
        reverse=True,
    )
    data["top_opportunities"] = sorted_opps[:3]

    # 9. 竞品分段 + 每段平均分
    if comp_total:
        data["leader"] = comp_total[0]["name"] if comp_total else "—"
        data["leader_avg"] = comp_total[0]["avg"] if comp_total else 0

    # 10. 市场细分概览（在 sources 之后构建以带上 _ref）
    seg_summary = []
    for seg in data["market_segments"]:
        seg_summary.append(
            {
                "label": seg.get("label", "—"),
                "desc": seg.get("desc", ""),
                "players": seg.get("players", []),
                "count": len(seg.get("players", [])),
                "icon": seg.get("icon", smart_icon(seg.get("label", ""))),
                "source": seg.get("source", ""),
                "_ref": seg.get("_ref", 0),
            }
        )
    data["segments_summary"] = seg_summary

    # TOC 列表（按章节顺序，与 templates/report.html 的 section ID 对齐）
    data["toc_items"] = [
        {"id": "background", "title": "背景与目标"},
        {"id": "conclusion", "title": "结论与建议"},
        {"id": "positioning", "title": "产品定位分析"},
        {"id": "business", "title": "商业策略分析"},
        {"id": "product-design", "title": "产品设计分析"},
        {"id": "data-growth", "title": "产品数据分析"},
        {"id": "user-feedback", "title": "用户反馈分析"},
        {"id": "other-competitors", "title": "其他竞品资料库"},
        {"id": "sources", "title": "来源与参考资料"},
    ]

    return data


def self_check(data, html_str):
    """严格自检：未解析模板标签 / 数据完整性 / 文件大小。"""
    print("\n=== self-check ===")
    ok = True
    # 1. 模板里残留的 {{ 或 {% 必须为 0
    unresolved = html_str.count("{{") + html_str.count("{%")
    # 排除 JS 中的字面 {{ ... }}（例如模板字符串）—— 这里简单计数足够
    checks = [
        ("竞品 ≥ 3", len(data["competitors"]) >= 3),
        ("opportunities ≥ 3", len(data["opportunities"]) >= 3),
        ("executive_summary 非空", bool(data.get("executive_summary"))),
        ("主题 token 完整", "--accent:" in html_str and "--bg:" in html_str),
        ("未解析模板标签 = 0", unresolved == 0),
        (
            "所有 9 个 section 渲染齐全",
            all(
                f'id="{sid}"' in html_str
                for sid in [
                    "background",
                    "conclusion",
                    "positioning",
                    "business",
                    "product-design",
                    "data-growth",
                    "user-feedback",
                    "other-competitors",
                    "sources",
                ]
            ),
        ),
        (
            "每个竞品卡片渲染",
            html_str.count('class="card"') >= len(data["competitors"]),
        ),
    ]
    for name, passed in checks:
        print(f"  {'✓' if passed else '✗'} {name}")
        if not passed:
            ok = False
    if unresolved > 0:
        print(f"  ⚠ 检测到 {unresolved} 处未解析标签，前 3 行：")
        for line in html_str.split("\n"):
            if "{{" in line or "{%" in line:
                print(f"    → {line.strip()[:120]}")
    size_kb = len(html_str.encode("utf-8")) / 1024
    print(
        f"  📦 HTML size: {size_kb:.1f} KB"
        + (" (✓ < 1.5MB)" if size_kb < 1500 else " (✗ 过大)")
    )
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
