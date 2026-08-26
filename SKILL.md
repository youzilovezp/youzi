---
name: youzi
description: 竞品深度分析与颠覆性产品灵感挖掘。当用户想要分析某个主题/赛道的竞品、对比差异、寻找超越竞品的颠覆性产品机会时触发。触发短语：「/youzi 主题」「竞品分析」「竞争格局」「做一下竞品调研」「我想做一个XX，先看看市面上有什么」。
allowed-tools: WebSearch, WebFetch, Bash, Read, Write, Edit, Agent, mcp__web-reader__webReader, mcp__web-search-prime__web_search_prime, mcp__zai-mcp-server__analyze_image, mcp__zai-mcp-server__understand_technical_diagram, mcp__zai-mcp-server__ui_to_artifact, mcp__zai-mcp-server__extract_text_from_screenshot, mcp__zai-mcp-server__analyze_video, mcp__zai-mcp-server__ui_diff_check, mcp__zread__get_repo_structure, mcp__zread__search_doc, firecrawl, agent-reach, crawl4ai, playwright, mcp__playwright
---

# /youzi · 竞品颠覆性分析

> 一句话：输入一个主题，自动挖出该赛道**所有顶尖竞品**，逐一深度拆解，输出**可直接用来做产品决策的精美 HTML 报告**，并给出**超越竞品的颠覆性产品机会清单**。

## 🔌 工具栈（按优先级使用）

**1️⃣ 搜索发现（WebSearch + Prime MCP + Agent-Reach）**
- `WebSearch`（内置）：通用搜索
- `mcp__web-search-prime__web_search_prime`：**带 domain/recency 过滤**，适合精确定位（如 `search_domain_filter=site:g2.com`）
- **Agent-Reach**（推荐安装）：内置 **Exa 语义搜索**（无 API key 免费）、GitHub search、V2EX、雪球 — 适合中文竞品调研

**2️⃣ 网页爬取（智能路由 → 按模块自动选引擎组合，详见 `adapters/`）**

`/youzi` 内置 13 个爬虫，**不是全开，而是按页面类型智能路由**（不装也能跑，降级到 WebSearch）：

🔒 **商业 API（需 key）**
- **firecrawl-mcp**（最推荐）：**96% 网页覆盖 + JS 重度 + 结构化 JSON 输出 + Batch** — 业界最强爬虫。安装：`npx -y firecrawl-cli@latest init` 或自托管 Docker
- **Jina Reader**（轻量）：`pip install jina` · 单 URL→MD，零配置（免费 20 req/min）

🆓 **开源 Python 库（本地运行）**
- **Crawl4AI**（开源免费）：LLM-ready markdown，处理 JS 重度。`pip install crawl4ai && crawl4ai-setup`
- **Crawlee**（反爬）：Apify 出品，自带指纹/UA/代理管理。`pip install crawlee`
- **Camoufox**（反 Cloudflare）：Firefox 隐身浏览器。`pip install camoufox && camoufox fetch`
- **Trafilatura**（学术）：web 正文抽取标准。`pip install trafilatura`
- **Newspaper3k**（文章）：`pip install newspaper3k lxml_html_clean`
- **Readability-lxml**（Mozilla）：`pip install readability-lxml`
- **Markdownify / html2text**（fallback）：HTML→MD/纯文本
- **Playwright**（登录/交互）：唯一能填表单、点按钮。`pip install playwright && playwright install chromium`
- **Scrapy**（整站）：Python 工业级框架
- **requests-html**（轻量 JS）：`pip install requests-html`

📍 **轻量 fallback**
- `mcp__web-reader__webReader`：**URL → LLM-friendly markdown**，轻量替代
- `WebFetch`（内置）：仅作最终 fallback

⭐ **统一入口（推荐用这个）**：`from adapters import scrape_smart` — **默认 auto 智能路由**：
- 任意域名竞品可直接爬：非内置表的竞品（如 `cursor.com`）会被 resolver 按域名直通（pricing/features/docs 先猜常规路径，404 由首页导航发现兜底），不再要求先补内置表
- `classify_url()` 识别页面类型（pricing/docs/about/blog/feature…）
- 按类型选引擎组合：**定价页 → JS 渲染组 + 静态对照引擎交叉验证**（价格必须 ≥2 独立引擎一致才算 verified）；文档/功能页 → firecrawl+crawl4ai；about/blog → 轻量正文抽取器
- 引擎历史成功率/质量自动记录到 `storage/engine-stats.json`，路由越用越准
- 定价页**禁止跨引擎拼接正文**（各引擎原文独立保留在 `all_results`，供逐引擎取证）

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

### Step 2 · 深度爬取（智能路由 + 逐页取证）

对每个竞品，用 `scrape_smart(url)`（默认 auto 智能路由）抓以下 URL（按可用性 fallback）：

| 优先级 | URL | 抓什么 | 引擎组合（auto 自动选） |
|--------|-----|--------|------------------------|
| P0 | `<url>` | 落地页：slogan、定位、目标用户、核心卖点 | firecrawl + crawl4ai + jina |
| P0 | `<url>/pricing` 或 `/pricing.html` | 定价模式、套餐结构 | **firecrawl + playwright + crawl4ai + trafilatura**（JS 组 + 静态对照交叉验证） |
| P1 | `<url>/features` 或 `/product` | 功能矩阵 | firecrawl + crawl4ai + trafilatura |
| P1 | `<url>/customers` 或 `/case-studies` | 客户类型、典型场景 | trafilatura + newspaper3k |
| P2 | `<url>/about` 或 `/company` | 公司背景、融资、规模 | trafilatura + readability |
| P2 | `<url>/blog` 最新 1-2 篇 | 近况、技术方向 | trafilatura + newspaper3k |
| P2 | `<url>/changelog` 或 `/release-notes` | 迭代速度信号 | markdownify + trafilatura |
| P3 | `<url>/docs` 或 `/developers` | 技术栈线索、API 能力 | firecrawl + crawl4ai + trafilatura |

每个竞品**实际抓到的内容**写到 `OUT_DIR/02-raw/<name>.md`（已转 markdown）。

**取证规则（不可违反）：**
- 每页落盘时记录 header：`# Source: <url>`、`# Scrapers: <用了哪些引擎>`、`# Time: <时间>`
- 定价页**每个引擎的原文独立保留**（`all_results`），绝不跨引擎拼接后提取
- 抓不到（404/反爬/JS-only）→ 用 WebSearch 找该竞品的 Wikipedia/Crunchbase/G2 评测页作为**替代证据源**，并如实标注来源切换

### Step 3 · 结构化分析（LLM 基于证据提取 —— 每字段可追溯）

**这一步是 LLM（你）的工作，不是脚本的**：脚本只做证据采集（`scripts/crawl_competitors.py` 的启发式结果只是候选），你必须**逐字段从 02-raw 证据中提取、并核对原文**。

按 references/analysis-framework.md 提取 13 个字段。**铁律：**

1. **每个字段必须带证据三元组**：`{值, source_url, quote}`（quote = 原文逐字引文，≤100 字）
2. **quote 必须能在该 source_url 的 02-raw markdown 里逐字找到**（写完后自查一遍 grep）
3. **证据里没有的内容写「未验证」**，绝不编造、绝不脑补、绝不用训练记忆里的"常识"填充
4. **定价必须交叉验证**：≥2 个独立引擎看到相同价格 → `pricing_verified: true`；只有 1 个引擎 → `pricing_verified: false`（报告自动显示 ⚠ 未验证徽章）；0 个 → 「未能获取，请核对官网」
5. strengths / weaknesses / scores / differentiators / tech_signals / gaps / opportunities **全部由你基于证据生成**（脚本不再模板化伪造）—— 每条 strengths/weaknesses 附 `{point, evidence(quote), score, source_url}`
6. scores 基于证据打分并在报告中可辩护（功能数、集成数、定价结构都是证据）；证据不足的维度给低置信标注
7. **写 claim**：你从 02-raw 提取/改写的每个字段，同步追加到 `OUT_DIR/claims-manifest.json` 的 `claims` 数组（schema 见 docs/superpowers/specs/2026-08-26-production-quality-loop-design.md §3.1）—— verify.py 会拒收任何没有抓取记录支撑的 source_url 和 grep 不到的 quote

1. `name` — 产品名
2. `url` — 官网
3. `tagline` — 一句话定位（≤ 25 字，quote 原文）
4. `founded` — 成立年份（可空）
5. `stage` — 阶段（早期 / 成长期 / 成熟期 / 巨头）
6. `target_users` — 目标用户（数组）
7. `core_features` — 核心功能（3-6 条，每条 ≤ 12 字）
8. `pricing` — 定价摘要（"免费 + 付费 $X/月起" + `pricing_verified` + `pricing_source` + `pricing_scraped_at`）
9. `strengths` — 3 个最强项（各带 quote + URL）
10. `weaknesses` — 3 个最弱项（各带 quote + URL）
11. `differentiators` — 1-2 个差异化杀手锏（各带 quote + URL）
12. `tech_signals` — 技术栈线索（来自 docs/blog/changelog，各带 URL）
13. `scores` — **6 个维度 1-10 分**（feature_richness / ux / pricing_value / integration / ai_capability / momentum）

合并所有竞品后**额外产出**：

- `market_segments` — 市场细分（谁在做哪一块）
- `comparison_matrix` — 横轴竞品 × 纵轴 6 个评分维度的矩阵（用于雷达图）
- `feature_overlap` — 功能重叠热力图（哪些功能是标配、哪些是独家）
- `gaps` — **市场空白**：所有竞品都没做好的痛点（≥ 3 条）
- `opportunities` — **颠覆性机会**：基于 gaps + 技术趋势，列出 5-8 个潜在的产品方向（每个含：灵感来源 / 目标用户 / 差异化点 / 验证方式）
- `executive_summary` — 200 字内的 TL;DR

写到 `OUT_DIR/03-analysis.json`（schema 见 references/analysis-framework.md 末尾）。

### Step 4 · 渲染精美 HTML（render.py · Jinja2）

直接执行：

```bash
mkdir -p "$OUT_DIR"
python3 ~/.claude/skills/youzi/render.py \
  --input "$OUT_DIR/03-analysis.json" \
  --output "$OUT_DIR/report.html"
```

`render.py` 用 **Jinja2**（业界标准、经过充分测试）解析 `templates/report.html` 里的 `{{var}}` 和 `{% for %}` 占位符。生成的 HTML：

- **单文件**，无任何外部依赖（CSS/JS/SVG 全部 inline）
- **响应式**（桌面 / 平板 / 手机）
- **主题感知**（跟随系统的浅色 / 深色，可手动切换并记住）
- **可视化**：竞品卡片网格、6 维雷达图、定价对比柱图、功能重叠热力图、机会象限气泡图
- **可读性**：CJK 友好字体栈、合理的字号 / 行距 / 间距、章节锚点、返回顶部

输出路径向用户回显：`✓ Report: ~/youzi-out/<topic>-.../report.html`

### Step 5 · 自检 + 验证（双门禁）

`render.py` 退出后会打印**自检报告**，逐项核对：

- [ ] 雷达图渲染（6 维度齐全）
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
python3 ~/.claude/skills/youzi/verify.py \
  --analysis "$OUT_DIR/03-analysis.json" \
  --manifest "$OUT_DIR/claims-manifest.json" \
  --raw-dir "$OUT_DIR/02-raw" \
  --json "$OUT_DIR/verify-report.json"
```

- exit 2 → 读 verify-report.json 的 `{gate, field, hint}`，修 03-analysis.json 或重爬，再验 —— **修复回路，不是绕过**
- 离线门禁（必跑，<1s）：G1 来源可回溯 / G2 quote 回查 / G3 定价独立性+TTL / G4 缺失诚实 / G5 反伪造 / G6 URL 卫生
- 网络复核（可选，慢）：加 `--network --sample 10`（N1 死链硬失败 / N2 quote 漂移警告）
- 新发现的坏数据形状 → 冻结成离线 fixture 进 tests/（延续 test_pricing_extract.py 模式）

### Step 6 · 交付

1. 用 `open <report.html>` 在浏览器打开（macOS）
2. **给用户 1 段话总结**：
   - 你分析了 X 个竞品
   - 报告路径
   - **一句话点出最大机会**（取 opportunities[0]）
3. 提示用户：**如果想换主题 / 加竞品 / 调整 focus，直接说就行，重跑只要 30 秒**（因为 Step 1-2 的结果可以缓存到 OUT_DIR/02-raw/）

## 重要原则

- **绝对不伪造（最高优先级）**：任何字段抓不到就标「未验证」，绝不用硬编码数据/模板话术/训练记忆冒充爬取结果。历史上因此删掉了：内置静态价格库、关键词模板 differentiators/tech_signals、伪造 G2 引文的 SWOT、硬编码 other_competitors 池 —— 这些都曾让报告"看起来完整"实际全是错误数据。
- **每处内容可追溯**：值 + source_url + 原文 quote（+ 引擎 + 时间戳）。定价额外要求 ≥2 引擎一致才 verified。
- **智能引擎路由**：`scrape_smart` 默认 auto —— 按页面类型选组合（定价页 = JS 组 + 静态对照），引擎历史表现自动学习（`storage/engine-stats.json`）。全开 13 引擎只在 auto 失败后兜底。
- **不重复造轮子**：模板/CSS/可视化都参考 graphify（知识图谱可视化）、ai-radar（仪表盘）、artifact-design（HTML 美学）、dataviz（图表配色）这些已有 skill 的成熟方案。
- **诚实**：strengths / weaknesses / scores 必须基于实际抓到的证据（带 URL + 引文片段），不写"AMAZING" / "BEST" 这种空话。
- **§5.2 行业标准矩阵**：render.py 内置 `_CANONICAL_FEATURES_WHATSAPP`（28 项 × 10 类），作为权威能力清单；每行附"中文释义+行业意义"，每格 ✓/? 都有 tooltip 证据。可在 analysis.json 用 `feature_canonical.evidence_notes` 手动覆盖自动判定。
- **可重入**：`OUT_DIR/02-raw/` 是 cache，重跑时 Step 2 跳过已抓的；`OUT_DIR/03-analysis.json` 存在时 `render.py` 直接渲染。
- **不强制联网**：用户给了本地 JSON 时跳过 Step 1-3。
- **双门禁交付**：render.py exit 0 且 verify.py exit 0 才算交付；verify-report.json 是修复回路的输入，不是可选日志。

## 参考文档（必读）

- `references/crawl-strategy.md` — 爬取策略（**智能路由 + 引擎组合 + 交叉验证**）
- `references/analysis-framework.md` — 13 字段提取 + 证据三元组规范 + opportunities 生成 prompt
- `templates/report.html` — HTML 模板（render.py 解析的）
- `render.py` — Jinja2 渲染器（内置防伪造自检）

## 反模式（不要做）

- ❌ 用 Tailwind / Chart.js CDN（违反 CSP，且离线不能看）
- ❌ 把所有竞品塞成一张大表（信息密度太低）
- ❌ opportunities 里全是"加强 AI 能力"这种正确的废话
- ❌ strengths / weaknesses 不带证据来源（具体到 URL + 引文片段）
- ❌ **任何形式的伪造**：硬编码价格兜底、关键词模板生成的"差异化/技术栈"、从未访问过的 URL 当 source、训练记忆里的"已知信息"冒充爬取结果
- ❌ 全开 13 个引擎跑所有页面（慢 + 低质引擎补充段落污染定价证据；auto 路由已按页面类型选好组合）
