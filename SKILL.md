---
name: youzi
description: 竞品深度分析与颠覆性产品灵感挖掘。当用户想要分析某个主题/赛道的竞品、对比差异、寻找超越竞品的颠覆性产品机会时触发。触发短语：「/youzi 主题」「竞品分析」「竞争格局」「做一下竞品调研」「我想做一个XX，先看看市面上有什么」。
allowed-tools: WebSearch, WebFetch, Bash, Read, Write, Edit, Agent, mcp__web-reader__webReader, mcp__web-search-prime__web_search_prime, mcp__zai-mcp-server__analyze_image, mcp__zai-mcp-server__understand_technical_diagram, mcp__zai-mcp-server__ui_to_artifact, mcp__zai-mcp-server__extract_text_from_screenshot, mcp__zai-mcp-server__analyze_video, mcp__zai-mcp-server__ui_diff_check, mcp__zread__get_repo_structure, mcp__zread__search_doc, firecrawl, agent-reach, playwright, mcp__playwright
---

# /youzi · 竞品颠覆性分析

> 一句话：输入一个主题，自动挖出该赛道**所有顶尖竞品**，逐一深度拆解，输出**可直接用来做产品决策的精美 HTML 报告**，并给出**超越竞品的颠覆性产品机会清单**。

## 🔌 工具栈（按优先级使用）

**1️⃣ 搜索发现（WebSearch + Prime MCP + Agent-Reach）**
- `WebSearch`（内置）：通用搜索
- `mcp__web-search-prime__web_search_prime`：**带 domain/recency 过滤**，适合精确定位（如 `search_domain_filter=site:g2.com`）
- **Agent-Reach**（推荐安装）：内置 **Exa 语义搜索**（无 API key 免费）、GitHub search、V2EX、雪球 — 适合中文竞品调研

**2️⃣ 网页爬取（V2 白名单 4+1 引擎，按信息类型自动路由）**

本地引擎仅保留实测能打的（engine-stats n=700+ 校准）：

- **playwright** — JS 页王者（定价/首页/功能页主力；唯一能登录交互）
- **trafilatura** — 静态正文王者（docs/about/changelog 主力）
- **newspaper3k** — 文章型（blog/customer/testimonials 主力）
- **jina** — 第三方渲染，交叉验证第二票（免 key）
- **firecrawl** — 商业最强；检测到 `FIRECRAWL_API_KEY` 时自动插入优先位，无 key 不尝试

⭐ **统一入口**：`python3 scripts/fetch.py --competitors "wati,respond.io" --out-dir OUT`
- 按 URL 类型自动路由引擎组合；定价页 JS 组 + 静态对照双通道
- 定价 ≥2 独立引擎看到相同价格才 sufficient；不达标沿升级梯自动换引擎重爬
- 全灭时搜索发现官方定价页兜底；预算(300s/竞品)耗尽诚实标「未验证」
- 台账 `claims-manifest.json` + 每引擎原文 `02-raw/*.engines.json`（verify.py 消费）
- **fetch.py 只取证不做语义提取** —— 套餐/功能/定位的提取全部是你的工作（Step 3）

📍 **轻量 fallback**
- `mcp__web-reader__webReader`：**URL → LLM-friendly markdown**，轻量替代
- `WebFetch`（内置）：仅作最终 fallback

🔁 **充分性闭环（准 > 快，fetch.py 内建，契约见 `scripts/sufficiency.py`）**：
- 定价：≥2 独立引擎一致（价格投票统一 `pricing_tokens.py`，₹/Rs./US$/后缀€ 全覆盖）+ 周期/货币完整 + Free/Custom 档无周期 + 月/年配对 → 不达标沿**引擎升级梯**换未用引擎重爬（预算 5 分钟/竞品，全页面共享 deadline）→ 仍不达标回退 ≤14 天已验证缓存（`storage/pricing-cache.json`，带每引擎原文，台账标 `from_cache`）→ 全灭时搜索发现官方定价页
- tech_signals：必须锚定 docs **具体子页**（非栏目首页）→ `python3 <skill-root>/scripts/deep_link.py tech <域名> "<关键词>"` 用 site: 搜索定位具体文档页 + 附原文 quote（搜索通道：Jina Reader + DDG lite，免 key）
- user_feedback：官网 testimonials 没抓到 → `python3 <skill-root>/scripts/deep_link.py feedback <产品名>`（G2/Trustpilot 具体评论页，多数被封 → Reddit JSON 兜底）
- 预算耗尽/通道全封 → 诚实标「未验证」，绝不伪造

**3️⃣ 视觉理解（zai MCP 套件）**
- `mcp__zai-mcp-server__analyze_image`：竞品 logo / UI 截图 / Hero image 视觉分析
- `mcp__zai-mcp-server__understand_technical_diagram`：从架构图 / 流程图提取技术栈信号
- `mcp__zai-mcp-server__ui_to_artifact`：把竞品 UI 截图 → 设计规格（颜色 / 字号 / 组件）
- `mcp__zai-mcp-server__ui_diff_check`：两个竞品 UI 截图 → 视觉差异报告
- `mcp__zai-mcp-server__extract_text_from_screenshot`：截图 OCR（含代码截图）
- `mcp__zai-mcp-server__analyze_video`：产品演示视频分析（YouTube 链接 → 功能拆解）

**4️⃣ 代码仓库（zread MCP + Agent-Reach GitHub）**
- `mcp__zread__get_repo_structure`：开源竞品的仓库结构
- `mcp__zread__search_doc`：搜索 README / 文档 / issues
- **Agent-Reach GitHub channel**：更深入的仓库搜索（无需 zread MCP）

**5️⃣ 社交平台（Agent-Reach 独家）**
- **Agent-Reach** 提供：YouTube 字幕、X/Twitter、小红书、微博、B站、LinkedIn
- 适合抓竞品的**用户口碑、社交信号、CEO 言论**

## Usage

```
/youzi 主题                                              # 基础用法：分析该主题的竞品
/youzi 主题 --depth deep                                 # 深度模式：每个竞品抓更多页（定价/客户案例/技术博客）
/youzi 主题 --count 12                                   # 指定竞品数量（默认 9）
/youzi 主题 --region cn                                  # 限定区域（cn/us/global，默认 global）
/youzi 主题 --focus "中小企业 / 移动端"                   # 限定分析焦点
/youzi 主题 --include "Slack,钉钉"                        # 强制包含指定竞品
/youzi 主题 --exclude "Worktile"                          # 排除指定竞品
/youzi <path/to/saved.json>                              # 用已保存的数据重新渲染报告（不必重爬）
```

## 工作流（7 步）

### Step 0 · 解析参数 + 工作目录

```
TOPIC = 用户传入的主题（去掉 /youzi 前缀和 flags）
DEPTH = shallow | deep (默认 shallow)
COUNT = 数字（默认 9）
REGION = cn | us | global (默认 global)
FOCUS = 自由文本（可空）
INCLUDE / EXCLUDE = 列表（可空）
OUT_DIR = ~/youzi-out/<topic>-<YYYY-MM-DD>/
```

向用户**一行回显**：`/youzi 在线协作工具 → count=9, depth=shallow, region=global, focus=无, out=~/youzi-out/在线协作工具-2026-08-15/`

如果 `--include`/`--exclude` 给了名字，先尝试模糊匹配到主流产品名（"飞书" → "Lark/飞书"），匹配不到就让用户确认。

### Step 1 · 发现竞品（WebSearch）

用 **多个不同角度** 搜索 TOPIC 相关的竞品，**最少 6 个查询**（并行 WebSearch）：

1. `top <TOPIC> companies <YEAR>` — 全球头部
2. `<TOPIC> market leaders` — 市场份额视角
3. `<TOPIC> alternatives` — 替代品视角
4. `<TOPIC> G2 / Capterra / ProductHunt top rated` — 用户评分视角
5. `<TOPIC> 开源 / github.com topics` — 技术视角
6. `<TOPIC> 比较 review site:<权威媒体>` — 编辑视角
7. （region=cn 时追加）`<TOPIC> 国内 排行 36氪 / 虎嗅`
8. （focus 给定时追加）`<TOPIC> <FOCUS> 推荐`

合并去重，按"出现次数 × 知名度"排序，**取 Top COUNT 个**。如果 `--include` 给了名字，把它插到列表最前面。

把这份清单写到 `OUT_DIR/01-competitors-list.json`：
```json
{
  "topic": "...",
  "competitors": [
    {"name": "Notion", "url": "https://notion.so", "source": "G2 top rated"},
    ...
  ]
}
```

向用户**打印一行**：`Found 10 competitors: Notion, Lark, Coda, ...`

### Step 2 · 深度爬取（fetch.py 一次完成 · 只取证）

对整批竞品**一次调用**统一取证入口（不要逐 URL 手动 scrape_smart）：

```bash
python3 <skill-root>/scripts/fetch.py \
  --competitors "wati,respond.io,landbot" \
  --out-dir "$OUT_DIR" \
  --budget 300        # 可选：每竞品墙钟预算秒数（默认 300）
```

fetch.py（V2 取证层，职责全部列完）：
1. **URL 发现** — resolver 猜常规路径（pricing/features/docs/about/testimonials/blog）+ **多引擎**首页导航发现并集（链接证据不依赖单一 primary；同站判定到可注册域，docs 子域可发现）；任意域名直通，不要求先补内置表
2. **爬取** — scrape_smart 智能路由（5 引擎白名单，按 URL 类型自动选组合；定价页 JS 组 + 静态对照双通道）；**页面级并行**（homepage 先行，其余页面错峰并发）+ **竞品级并行**（3 并发）；robots.txt disallow 的页面诚实跳过
3. **落盘** — `02-raw/<name>.md`（合并视图）+ `02-raw/<name>.engines.json`（每引擎原文独立，verify.py 消费；头尾截断保正文尾部）
4. **台账** — `claims-manifest.json` 记录每条 URL × 引擎 × 内容哈希 × 时间；`kinds` 多值（home_as_pricing 时首页同时是 homepage+pricing，不互相覆盖）

**充分性内建**：定价 ≥2 独立引擎看到相同价格才 sufficient；不达标沿升级梯自动换引擎重爬；全灭时搜索发现官方定价页兜底；预算耗尽诚实标 insufficient。

**取证规则（不可违反）：**
- 定价页**每个引擎的原文独立保留**，绝不跨引擎拼接后提取
- 某竞品全灭（0 页，exit code 1）→ 用 WebSearch 找该竞品的 Wikipedia/Crunchbase/G2 评测页作为**替代证据源**，并如实标注来源切换

### Step 2.5 · 情报自我审计（爬取后必跑 · 驱动同会话补爬）

```bash
python3 <skill-root>/audit.py \
  --manifest "$OUT_DIR/claims-manifest.json" \
  --raw-dir "$OUT_DIR/02-raw"
```

三层审计（详细契约见 audit.py 头注释）：
1. **L1 覆盖率**：页面类型齐全度 / 定价深度（月付·年付·Free·Custom 配对 + 每引擎价格 token 数）/ 字段完整度（带 --analysis 时）
2. **L2 准确性**：分析价格值跨引擎投票 / quote 逐字回查（抽样）/ pricing_verified 一致性
3. **L3 反哺**：next_actions（可执行补采动作）+ **storage/intel-lessons.json 跨会话经验沉淀**

**状态分类（核心）**：`ok / partial / gap / not-published / n-a` —— `not-published` 是**终态情报**（厂商确实不公示，如 0 价格 token×全引擎 + 替代路径已探测），记为已解决而非失败；只有 `gap` 才是采集缺口。

**闭环规则**：
- exit 1（存在 gap）→ 按 next_actions 补爬（同会话）→ 重跑 audit，直到 gap 清零或转为 not-published/partial
- audit 自动读写 `storage/intel-lessons.json`（按域名沉淀：不公示结论、单引擎定价、替代证据源、深挖抓手）——**下次运行同域名竞品时先读 lessons，跳过已确认的死路，把预算让给真正的缺口**（自我进化）
- 定价数据必须区分**月付/年付**两个周期（pricing_tiers 用 `billing_period: "/mo" | "billed"(年付结算月价) | "/yr"` 三通道，render 自动配对双栏展示）；只有单周期时如实标注，audit 会标 partial 并给深挖动作
- **定价 toggle 交互取证**：官网只有单周期价时，playwright 抓定价页会自动尝试点击「Monthly/Yearly」切换并把另一态价格以 `<!-- annual-billing variant (toggled) -->` 段附加进该引擎原文（playwright_scraper 内建，无需单独脚本）；引用帮助中心页脚等**死链线索**（如 meetbot.com/charge 404）也算穷尽探测的证据
- **隐藏入口挖掘**：定价/产品数据抓不到时，依次试 help center 页脚链接、web 搜索 `site:` 定位 changelog/roadmap 独立站（canny 类）、第三方报道（带日期的上线新闻可作 momentum 证据）

### Step 3 · 结构化分析（LLM 基于证据提取 —— 每字段可追溯）

**这一步是 LLM（你）的工作，不是脚本的**：fetch.py 只取证（V2 已删除全部脚本侧语义提取），你必须**逐字段从 02-raw 证据中提取、并核对原文**。

按 references/analysis-framework.md 提取 13 个字段。**铁律：**

1. **每个字段必须带证据三元组**：`{值, source_url, quote}`（quote = 原文逐字引文，≤100 字）
2. **quote 必须能在该 source_url 的 02-raw markdown 里逐字找到**（写完后自查一遍 grep）
3. **证据里没有的内容写「未验证」**，绝不编造、绝不脑补、绝不用训练记忆里的"常识"填充
4. **定价必须交叉验证**：≥2 个独立引擎看到相同价格 → `pricing_verified: true`；只有 1 个引擎 → `pricing_verified: false`（报告自动显示 ⚠ 未验证徽章）；0 个 → 「未能获取，请核对官网」
5. strengths / weaknesses / scores / differentiators / tech_signals / gaps / opportunities **全部由你基于证据生成**（脚本不再模板化伪造）—— 每条 strengths/weaknesses 附 `{point, evidence(quote), score, source_url}`；每条 differentiators 附 `{point, quote, source_url}`（dict 结构，与 strengths 同构，G2/G7 会查）
6. scores 基于证据打分并在报告中可辩护（功能数、集成数、定价结构都是证据）；证据不足的维度给低置信标注
7. **写 claim**：你从 02-raw 提取/改写的每个字段，同步追加到 `OUT_DIR/claims-manifest.json` 的 `claims` 数组（schema 见 docs/superpowers/specs/2026-08-26-production-quality-loop-design.md §3.1）—— verify.py 会拒收任何没有抓取记录支撑的 source_url 和 grep 不到的 quote
8. **溯源优先级（G7 强制）**：功能/技术/差异化类字段（core_features / differentiators / tech_signals / feature_catalog）的 source **必须优先锚定 docs/features 具体子页** > about/customers > 首页 > pricing。锚定域名根或 /pricing 路径 → verify 直接 hard fail（唯一豁免：quote 本身是定价陈述，含货币符号+价格数字）。信息只出现在低优先级页面时，优先找更高优先级页面上的对应表述重新锚定；实在抓不到权威锚点的条目**宁可删除也不留低质锚点**

1. `name` — 产品名
2. `url` — 官网
3. `tagline` — 一句话定位（≤ 25 字，quote 原文）
4. `founded` — 成立年份（可空）
5. `stage` — 阶段（早期 / 成长期 / 成熟期 / 巨头）
6. `target_users` — 目标用户（数组）
7. `core_features` — 核心功能（≥ 12 条，每条 ≤ 12 字；**必须扫该竞品的 docs 证据页**——台账 kind=docs 的 URL 对应段落——逐条提取，不得只抄 pricing 页套餐清单）
7.5. `feature_catalog` — **§5.2.1 厂商功能矩阵的唯一数据源，漏写 = 矩阵空白（历史事故）**。结构：`{"<竞品名>": [{"name": "团队共享收件箱", "category": "收件箱与协作", "desc": "厂商原文描述(可选)", "source": "<docs/features 具体子页 URL>"}]}`。跨厂商用**一致的 category 分类**（如 渠道接入/收件箱与协作/营销获客/自动化/AI 能力/电商变现/数据与集成/安全合规）+ 同能力同名（如各家都叫"营销群发"），矩阵才能自动对齐合并；source 逐条锚定（锚不到的留空字符串，gates 允许）。只写 core_features 字符串数组时 render.py 会降级合成（source 为空），但逐条溯源会丢失 —— **规范做法是两个都写**
8. `pricing` — 定价摘要（"免费 + 付费 $X/月起" + `pricing_verified` + `pricing_source` + `pricing_scraped_at`）
9. `strengths` — 3 个最强项（各带 quote + URL）
10. `weaknesses` — 3 个最弱项（各带 quote + URL）
11. `differentiators` — 1-3 个差异化杀手锏，**dict 结构** `[{point, quote(≤100字逐字), source_url}]`（与 strengths 同构；source_url 必须锚定 docs/features 具体子页，quote 可在该页 grep 到）
12. `tech_signals` — 技术栈线索（来自 docs/blog/changelog，各带 URL；source 必须是 docs 具体子页而非栏目首页/pricing，附 quote 逐字原文）
13. `scores` — **6 个维度 1-10 分**（feature_richness / ux / pricing_value / integration / ai_capability / momentum）
14. `user_feedback` — 客户口碑（`[{quote, source, who}]`，quote=逐字引文；**§7 反馈区第一数据源**，每家 2-4 条，量化数字优先；抓不到 testimonials 时用应用市场评论/Reddit 兜底）
15. `product_momentum` — 产品迭代信号（`[{title, when, source}]`，**title 必须原文逐字**（G2 回查），when=YYYY-MM-DD 或「未注日期」；公开 changelog/roadmap 优先，如 roadmap.respond.io 类 canny 站点；**§6 产品数据分析的第一数据源**）

合并所有竞品后**额外产出**：

- `market_segments` — 市场细分（谁在做哪一块）
- `comparison_matrix` — 横轴竞品 × 纵轴 6 个评分维度的矩阵（用于功能重叠热力图）
- `feature_overlap` — 功能重叠热力图（哪些功能是标配、哪些是独家）
- `gaps` — **市场空白**：所有竞品都没做好的痛点（≥ 3 条）
- `opportunities` — **颠覆性机会**：基于 gaps + 技术趋势，列出 5-8 个潜在的产品方向（每个含：灵感来源 / 目标用户 / 差异化点 / 验证方式）
- `executive_summary` — 200 字内的 TL;DR

写到 `OUT_DIR/03-analysis.json`（schema 见 references/analysis-framework.md 末尾）。

### Step 4 · 渲染精美 HTML（render.py · Jinja2）

直接执行（`<skill-root>` = skill 根目录：Claude Code `~/.claude/skills/youzi` · opencode `~/.config/opencode/skills/youzi`）：

```bash
mkdir -p "$OUT_DIR"
python3 <skill-root>/render.py \
  --input "$OUT_DIR/03-analysis.json" \
  --output "$OUT_DIR/report.html"
```

`render.py` 用 **Jinja2**（业界标准、经过充分测试）解析 `templates/report.html` 里的 `{{var}}` 和 `{% for %}` 占位符。生成的 HTML：

- **单文件**，无任何外部依赖（CSS/JS/SVG 全部 inline）
- **响应式**（桌面 / 平板 / 手机）
- **主题感知**（跟随系统的浅色 / 深色，可手动切换并记住）
- **可视化**：竞品卡片网格、定价卡片（月付/年付分栏，无年付/无月付数据时自动降级为单列 + 年付省%徽章 + 折算月价 + Free/Custom 语义档）、功能重叠热力图、机会卡片网格
- **可读性**：CJK 友好字体栈、合理的字号 / 行距 / 间距、章节锚点、返回顶部

输出路径向用户回显：`✓ Report: ~/youzi-out/<topic>-.../report.html`

### Step 5 · 自检 + 验证（双门禁）

`render.py` 退出后会打印**自检报告**，逐项核对：

- [ ] 热力图渲染（功能重叠矩阵齐全）
- [ ] **§5.2.1 功能矩阵非空（行 ≥ 1）** —— 空矩阵 = feature_catalog 没写，Step 3 必须补（render.py 已加硬失败自检）
- [ ] 竞品卡片 ≥ 3 张
- [ ] opportunities ≥ 3 条
- [ ] 浅色 / 深色 主题都看过（CSS token 完整）
- [ ] 单文件 < 1.5MB（inline SVG / 不含 base64 大图）

**证据硬门禁（不过 = exit 2，HTML 仍生成但顶部带红色警告横幅）**：

- [ ] `source_count > 0` —— 03-analysis.json 里一个 URL 都没有 = Step 3 没按证据三元组写，**必须重做 Step 3**，不要交付
- [ ] 每个竞品 `pricing_verified` 有值（缺失按 `false` 处理 → 报告显示 ⚠ 未验证徽章）
- [ ] 无 Python repr 泄漏（`['xxx']` / `{'name': ...}` 字面量出现在 HTML = 乱码回归）
- [ ] 占位符（"待补充"）不得混入启发点/机会点派生板块

任意一项不过 → 定位是数据问题就修 03-analysis.json（补 source/quote），是模板问题就修 `templates/report.html` 或 `render.py`，重跑 Step 4。**绝不允许删掉自检项来"通过"**。

**证据硬门禁（verify.py，与渲染自检同级 —— 不过 = 不交付）：**

```bash
python3 <skill-root>/verify.py \
  --analysis "$OUT_DIR/03-analysis.json" \
  --manifest "$OUT_DIR/claims-manifest.json" \
  --raw-dir "$OUT_DIR/02-raw" \
  --json "$OUT_DIR/verify-report.json"
```

- exit 2 → 读 verify-report.json 的 `{gate, field, hint}`，修 03-analysis.json 或重爬，再验 —— **修复回路，不是绕过**
- 离线门禁（必跑，<1s）：G1 来源可回溯 / G2 quote 回查 / G3 定价独立性+TTL / G4 缺失诚实 / G5 反伪造 / G6 URL 卫生 / G7 溯源权威性
- 网络复核（可选，慢）：加 `--network --sample 10`（N1 死链硬失败 / N2 quote 漂移警告）
- 新发现的坏数据形状 → 冻结成离线 fixture 进 tests/（延续 test_pricing_extract.py 模式）

**终审审计（audit.py 带 --analysis，交付前跑）**：

```bash
python3 <skill-root>/audit.py \
  --manifest "$OUT_DIR/claims-manifest.json" \
  --raw-dir "$OUT_DIR/02-raw" \
  --analysis "$OUT_DIR/03-analysis.json"
```

产出 `04-audit.json`（覆盖率×准确性矩阵 + next_actions），并刷新 lessons。交付话术里向用户摘要：每家竞品的审计状态、不公示终态清单、遗留 partial（如「仅年付价」）——**审计结论本身是情报**。

### Step 6 · 交付

1. 用 `open <report.html>` 在浏览器打开（macOS）
2. **给用户 1 段话总结**：
   - 你分析了 X 个竞品
   - 报告路径
   - **一句话点出最大机会**（取 opportunities[0]）
3. 提示用户：**如果想换主题 / 加竞品 / 调整 focus，直接说就行**；只重渲染（复用已有 03-analysis.json）是秒级，重爬默认全量（每竞品 ≤300s 预算，定价命中 ≤14 天已验证缓存时更快）

## 重要原则

- **绝对不伪造（最高优先级）**：任何字段抓不到就标「未验证」，绝不用硬编码数据/模板话术/训练记忆冒充爬取结果。历史上因此删掉了：内置静态价格库、关键词模板 differentiators/tech_signals、伪造 G2 引文的 SWOT、硬编码 other_competitors 池 —— 这些都曾让报告"看起来完整"实际全是错误数据。
- **每处内容可追溯**：值 + source_url + 原文 quote（+ 引擎 + 时间戳）。定价额外要求 ≥2 引擎一致才 verified。
- **智能引擎路由**：fetch.py → scrape_smart 按页面类型选组合（定价页 = JS 组 + 静态对照），5 引擎白名单（playwright/trafilatura/newspaper3k/jina/firecrawl），引擎历史表现自动学习（`storage/engine-stats.json`）。
- **不重复造轮子**：模板/CSS/可视化都参考 graphify（知识图谱可视化）、ai-radar（仪表盘）、artifact-design（HTML 美学）、dataviz（图表配色）这些已有 skill 的成熟方案。
- **诚实**：strengths / weaknesses / scores 必须基于实际抓到的证据（带 URL + 引文片段），不写"AMAZING" / "BEST" 这种空话。
- **§5.2 功能矩阵**：默认 vendor 模式（行 = 本次实爬功能并集，每行必有 ✓，全部可溯源）；`_CANONICAL_FEATURES_WHATSAPP`（28 项 × 10 类）为人工参考清单，仅在 analysis.json 显式 `feature_canonical.enabled=true` 时启用（? 刻 = 实爬未命中，不代表厂商不支持）。可用 `feature_canonical.evidence_notes` 手动覆盖自动判定。
- **可重入（准确语义）**：`OUT_DIR/03-analysis.json` 存在时 `render.py` 直接渲染（秒级）；`02-raw/` 是证据缓存 —— manifest 会增量合并、定价有 ≤14 天已验证缓存回退，但**页面默认全量重爬**（证据新鲜度优先）。只重渲染不重爬 = 跳过 Step 2 直接给 `--analysis`。
- **不强制联网**：用户给了本地 JSON 时跳过 Step 1-3。
- **双门禁交付**：render.py exit 0 且 verify.py exit 0 才算交付；verify-report.json 是修复回路的输入，不是可选日志。

## 参考文档（必读）

- `references/crawl-strategy.md` — 爬取策略（**智能路由 + 引擎组合 + 交叉验证**）
- `references/analysis-framework.md` — 13 字段提取 + 证据三元组规范 + opportunities 生成 prompt
- `audit.py` — 情报自我审计器（覆盖率×准确性×反哺；Step 2.5 驱动补爬，Step 5 终审 + lessons 沉淀）
- `templates/report.html` — HTML 模板（render.py 解析的）
- `render.py` — Jinja2 渲染器（内置防伪造自检）

## 反模式（不要做）

- ❌ 用 Tailwind / Chart.js CDN（违反 CSP，且离线不能看）
- ❌ 把所有竞品塞成一张大表（信息密度太低）
- ❌ opportunities 里全是"加强 AI 能力"这种正确的废话
- ❌ strengths / weaknesses 不带证据来源（具体到 URL + 引文片段）
- ❌ 绕过 fetch.py 逐 URL 手动爬（升级梯/台账/充分性闭环都在 fetch.py 里，手动跑会丢 verify.py 依赖的台账）
- ❌ 只写 core_features 字符串数组、不写 feature_catalog —— §5.2.1 厂商功能矩阵只消费后者，会渲染成空壳（render.py 现有降级合成兜底，但逐条溯源会丢）
- ❌ **任何形式的伪造**：硬编码价格兜底、关键词模板生成的"差异化/技术栈"、从未访问过的 URL 当 source、训练记忆里的"已知信息"冒充爬取结果
- ❌ 重新引入脚本侧语义提取（正则套餐/功能/翻译对齐）—— 165KB 单体的历史教训，提取是 LLM 的活
