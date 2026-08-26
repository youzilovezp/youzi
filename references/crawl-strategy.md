# Crawl Strategy · 竞品爬取策略（智能路由版 · 按模块选引擎 + 交叉验证 + 证据可追溯）

## 核心原则：**智能路由选组合，定价交叉验证，逐引擎留证据，绝不伪造**

---

## 🎯 智能引擎路由（`scrape_smart` 默认 auto 策略）

```
scrape_smart(url)  # 默认 auto —— 不需要手动选引擎
├─ classify_url(url) 识别页面类型
│   pricing / docs / feature / about / blog / customer / changelog / dashboard / homepage
├─ 按类型选引擎组合（下表）
├─ 引擎历史成功率/质量加权排序（storage/engine-stats.json，越用越准）
└─ 定价页特殊处理：各引擎原文独立保留（禁止拼接）→ 上层交叉验证
```

| 页面类型 | 引擎组合 | 设计理由 |
|----------|---------|---------|
| **pricing** | firecrawl + playwright + crawl4ai + **trafilatura** | 价格几乎都是前端渲染 → JS 组；加 1 个静态引擎做交叉对照：**≥2 独立引擎看到相同价格才 verified** |
| docs | firecrawl + crawl4ai + trafilatura | 文档站需要深读 + 结构 |
| feature | firecrawl + crawl4ai + trafilatura | 功能页 JS 中等 |
| homepage | firecrawl + crawl4ai + jina | 覆盖优先 |
| about | trafilatura + readability + markdownify | 静态文本，轻量引擎省资源 |
| blog / customer | trafilatura + newspaper3k + readability | 文章型，正文抽取器最强 |
| changelog | markdownify + trafilatura + readability | 简单文本 |
| dashboard / 登录墙 | playwright + camoufox | 必须真浏览器 |

**为什么不再全开 13 引擎**：慢是小事；大事是低质引擎的"补充段落"会污染证据 ——
定价页混入其他引擎抓到的对比表价格/附加项价格/缓存价格，就是报告价格全错的
根因之一。auto 路由按类型精准出机，定价页只信交叉验证。

```python
from adapters import scrape_smart

# 默认 auto(推荐):智能路由 + 定价页交叉验证隔离
result = scrape_smart("https://example.com/pricing")
print(result["url_type"])    # "pricing"
print(result["scraper"])     # 实际用的引擎

# auto 失败兜底:全引擎并行(覆盖最大)
result = scrape_smart(url, strategy="parallel")

# 手动指定引擎(调试用)
result = scrape_smart(url, enabled_scrapers=["camoufox", "jina"])
```

**需要登录 / 交互** → `need_login=True`（强制 playwright/camoufox）
**需要截图** → `need_screenshot=True`

13 个爬虫的定位速查：

┌──────────────┬────────────────────────────┬────────────────────────┐
│ 工具         │ 何时用                     │ 何时不用               │
├──────────────┼────────────────────────────┼────────────────────────┤
│ firecrawl    │ 通用首选（96% 覆盖）       │ API 受限/本地化时      │
│ Crawl4AI     │ firecrawl 失败时 fallback  │ —                      │
│ Crawlee      │ 整站爬取 / 高反爬          │ 单 URL（开销大）       │
│ Camoufox     │ Cloudflare 拦截的隐身场景  │ 普通站点（开销大）     │
│ Playwright   │ 登录、点击按钮、截图 SPA   │ 简单页面（开销大）     │
│ Jina Reader  │ 零配置快速 URL→MD         │ 大量并发（限流）       │
│ Trafilatura  │ 学术级正文抽取             │ JS 重度页面            │
│ Newspaper3k  │ 文章/博客                  │ SPA                    │
│ Readability  │ Mozilla 算法，文章类       │ 商品页                 │
│ Markdownify  │ HTML→MD fallback          │ JS 重度                │
│ html2text    │ HTML→纯文本 fallback      │ JS 重度                │
│ Scrapy       │ 整站批量                   │ 单 URL（开销大）       │
│ requests-html│ 轻量 JS 渲染               │ 复杂 SPA               │
└──────────────┴────────────────────────────┴────────────────────────┘

---

## 🚀 一次性安装（推荐）

```bash
# 1. firecrawl MCP server（最强爬虫）
npx -y firecrawl-cli@latest init

# 2. Agent-Reach（中文竞品调研神器）
# 在 Claude Code 里直接说：
"请安装并启用 Agent-Reach（github.com/Panniantong/Agent-Reach）"
# 或手动：
git clone https://github.com/Panniantong/Agent-Reach ~/.agent-reach
# 然后按 README 配置 MCP server
```

安装后**所有 firecrawl-mcp 和 Agent-Reach 提供的 channel 都自动可用**，无需再单独配置。

---

## Step 1 · 发现竞品（并行多角度搜索 · 用 Agent-Reach Exa + WebSearch Prime）

```python
# 1. 全球头部（G2 / Capterra）— WebSearch Prime 限定 domain
mcp__web-search-prime__web_search_prime(
  search_query="top <TOPIC> tools 2026",
  search_domain_filter="site:g2.com OR site:capterra.com"
)

# 2. Agent-Reach Exa 语义搜索（无 API key 免费）— 找新兴竞品
# Agent-Reach channel: exa_semantic_search(query="<TOPIC> emerging startups 2026")

# 3. GitHub 视角 — zread MCP
mcp__zread__search_doc(
  repo_name="awesome-<TOPIC>",
  query="<TOPIC>",
  language="en"
)

# 4. 中文（36氪 / 虎嗅）— WebSearch Prime 限定中文媒体
mcp__web-search-prime__web_search_prime(
  search_query="<TOPIC> 国内排行",
  search_domain_filter="site:36kr.com OR site:huxiu.com OR site:sspai.com"
)

# 5. 替代品 + 对比视角
WebSearch(query="<TOPIC> alternatives to")
WebSearch(query="<TOPIC> vs comparison 2026")

# 6. Agent-Reach V2EX / 雪球 — 看开发者/投资人讨论
# channel: v2ex_search(query="<TOPIC>")
```

合并去重，按"出现次数 × 知名度"排序，**取 Top COUNT 个**。

---

## Step 2 · 深度爬取（智能路由 + 逐页取证）

```python
# === 统一入口:scrape_smart(auto 智能路由,按页面类型自动选引擎组合) ===
from adapters import scrape_smart

r_home    = scrape_smart("<竞品官网>/")            # firecrawl + crawl4ai + jina
r_pricing = scrape_smart("<竞品官网>/pricing")     # firecrawl + playwright + crawl4ai + trafilatura
r_feat    = scrape_smart("<竞品官网>/features")    # firecrawl + crawl4ai + trafilatura
r_about   = scrape_smart("<竞品官网>/about")       # trafilatura + readability + markdownify

# 定价页取证:r["all_results"] 里是各引擎独立原文 —— 交叉验证的数据源
for engine_result in r_pricing["all_results"]:
    print(engine_result["scraper"], engine_result["markdown"][:200])

# === 落盘规范(可追溯的最低要求) ===
# 每页写 OUT_DIR/02-raw/<name>-<page>.md,header 必须含:
#   # Source: <url>
#   # Scrapers: <引擎列表>
#   # Time: <UTC 时间>
# run_youzi.py --crawl-only 已自动按此规范落盘

# === 特殊场景 ===
# 登录墙 → scrape_smart(url, need_login=True)  # playwright/camoufox
# 反爬(Cloudflare) → enabled_scrapers=["camoufox", "jina"]
# firecrawl MCP 可用时也可直接用 mcp firecrawl 工具(96% 覆盖),
#   但定价页仍需第二个独立引擎对照 —— 单引擎价格一律标"未验证"

# === Fallback:auto 组合全失败 → strategy="parallel" 全引擎兜底,
#     仍失败 → WebSearch 找该竞品 Wikipedia/Crunchbase/G2 页作替代证据源(如实标注) ===
```

## 📋 证据规范（Step 3 LLM 提取的输入契约）

**每个进入报告的字段必须满足：**

```json
{
  "value": "Growth $39/mo",
  "source_url": "https://example.com/pricing",
  "quote": "Growth — $39 per user/mo, billed annually",
  "engines": ["firecrawl", "playwright"],
  "scraped_at": "2026-08-25 03:12 UTC",
  "verified": true
}
```

- `quote` 必须**逐字**出现在 `source_url` 对应的 02-raw markdown 里（LLM 写完自查）
- 定价：`verified` = ≥2 个独立引擎一致；单引擎 = false（报告自动显示 ⚠）
- 无证据 → 值写「未验证」，绝不编造

---

## Step 2.5 · 视觉/代码/视频增强（头部 3-5 个竞品必做）

### 视觉分析（zai MCP）

```python
# 1. 从 firecrawl 输出找 Hero image URL，下载到 OUT_DIR/02-raw/<name>-hero.jpg
# 2. 用 zai MCP 分析视觉风格
mcp__zai-mcp-server__analyze_image(
  image_source="OUT_DIR/02-raw/cursor-hero.jpg",
  prompt="分析这张 SaaS 产品首页 Hero 图的视觉设计：主色调、字体风格、品牌调性、目标用户暗示、可借鉴的设计语言。中文输出。"
)

# 3. 架构图 → 技术栈信号
mcp__zai-mcp-server__understand_technical_diagram(
  image_source="<图 URL>",
  prompt="提取这个架构图中的所有组件、技术栈、调用关系。重点关注：用了哪些服务、AI 模型、数据库、API。"
)

# 4. UI 截图 → 设计规格
mcp__zai-mcp-server__ui_to_artifact(
  image_source="<UI 截图 URL>",
  output_type="spec",
  prompt="提取设计 token：主色/辅色/字号/间距/圆角/阴影/组件命名约定。"
)
```

### 视频分析（Agent-Reach YouTube 优先 · zai MCP 兜底）

```python
# Agent-Reach YouTube channel（提取字幕 + 摘要，比 analyze_video 更准）
# channel: youtube_summary(url="https://youtube.com/watch?v=...")

# 或 zai MCP analyze_video（视觉理解）
mcp__zai-mcp-server__analyze_video(
  video_source="https://youtube.com/watch?v=...",
  prompt="拆解这个产品演示视频：1) 主要使用场景 2) 核心功能 3) UI 特点 4) 目标用户 5) 商业模式信号。"
)
```

### 开源代码仓库（zread MCP + Agent-Reach GitHub）

```python
# zread MCP（结构化）
mcp__zread__get_repo_structure(repo_name="owner/repo")
mcp__zread__search_doc(repo_name="owner/repo", query="architecture")

# Agent-Reach GitHub channel（issue / PR / 搜索）
# channel: github_search(query="<TOPIC> language:python stars:>1000")
```

### 社交信号（Agent-Reach 独家）

```python
# X/Twitter — 看竞品 CEO / 团队在说什么
# channel: twitter_search(query="<竞品名> OR <CEO 名字>", limit=50)

# 小红书 / 微博 / B站 — 中国用户口碑
# channel: xhs_search(query="<竞品名> 使用体验")
# channel: weibo_search(query="<竞品名>")
# channel: bilibili_search(query="<竞品名> 评测")

# V2EX — 开发者评价
# channel: v2ex_search(query="<竞品名>")
```

结果写入 `OUT_DIR/02-raw/<name>-{visual,video,code,social}.md`。

---

## Step 3 · 结构化分析（LLM 基于证据提取 13 字段 + 视觉/代码/视频/社交信号）

读取 `OUT_DIR/02-raw/<name>.md`、`*-visual.md`、`*-video.md`、`*-code.md`、`*-social.md`，按 `analysis-framework.md` 提取 13 个字段。

**所有字段遵守上面的「证据规范」**：值 + source_url + 逐字 quote；证据里没有 → 「未验证」。`scripts/crawl_competitors.py` 输出的启发式结果（tagline/价格行/功能列表）**只是候选**，LLM 必须回对 02-raw 原文核对后采用或丢弃。

新增字段（来自 MCP）：
- `visual_design` — 视觉风格描述（主色 / 字体 / 调性）来自 `analyze_image`
- `tech_stack` — 技术栈信号（来自 `understand_technical_diagram` / `search_doc`）
- `video_signals` — 产品演示视频中的关键能力 / 用户场景
- `social_signals` — 社交平台的用户痛点 / CEO 言论 / 媒体声量

---

## 📊 缓存策略

- `OUT_DIR/02-raw/<name>.md` 存在 → 跳过该竞品爬取（除非 `--refresh`）
- 截图 / 视频 / 社交结果单独缓存：`OUT_DIR/02-raw/.visual-cache.json` / `.video-cache.json` / `.social-cache.json`
- 增量更新时只爬上次失败或过期的项

---

## ⚠️ 反爬应对

| 情况 | 方案 |
|------|------|
| JS 重度渲染（SPA） | **firecrawl**（内置 JS 引擎 + 96% 覆盖） |
| 登录墙 | firecrawl.agent（AI 自动交互）+ 备用 archive.org 缓存 |
| 反爬 Cloudflare | firecrawl 自带 stealth 模式；不行就 web-reader |
| 国内站慢/超时 | firecrawl 重试 + 走 36氪 / 虎嗅替代源 |
| 视频站 | Agent-Reach YouTube channel（字幕提取）> zai analyze_video（视觉） |
| 社交平台 | Agent-Reach（自动选最优 backend） |

---

## 🚀 性能：并行优先

- **Step 1（搜索）**：6+ 个查询全部并行（同 message）
- **Step 2（爬取）**：firecrawl.crawl 一次抓全站，或每个竞品的 4-5 个页面用 Agent subagent 并行
- **Step 2.5（视觉/视频/社交）**：全部并行
- **Step 3（分析）**：每个竞品一个 subagent 并行提取

---

## 💡 何时 firecrawl vs Agent-Reach

| 需求 | 用 firecrawl | 用 Agent-Reach |
|------|-------------|---------------|
| 竞品官网内容 | ✅ 必选 | — |
| 产品演示视频 | ❌ | ✅ YouTube channel |
| GitHub 仓库 | �️ zread 更专业 | ✅ GitHub channel |
| 用户口碑 / 社交 | � | ✅ X/小红书/B站 |
| 语义搜索（找新竞品） | � | ✅ Exa channel |
| 登录墙后的内容 | ✅ firecrawl.agent | — |
| 多语言站点 | ✅ 自带多语言 | — |
