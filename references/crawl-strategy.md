# Crawl Strategy · 竞品爬取策略（升级版 · firecrawl + Agent-Reach + MCP 套件）

## 核心原则：**firecrawl > web-reader > WebFetch，Agent-Reach 补全社交/视频/语义搜索**

---

## 🎯 工具选择决策树（2026 多工具版）

```
需要爬网页内容？
├─ 需要登录 / 交互？
│   ├─ 是 → Playwright MCP / playwright_scraper（唯一能填表单/点按钮）
│   └─ 否 ↓
├─ 想要截图？
│   ├─ 是 → firecrawl（首选 + 截图）→ playwright_scraper（备用）
│   └─ 否 ↓
├─ firecrawl 不可用 / 失败？
│   ├─ 是 → crawl4ai_scraper（开源免费，本地化）
│   └─ 否 ↓
├─ 是 SPA / 重度 JS？
│   ├─ 是 → crawl4ai_scraper 或 Playwright
│   └─ 否 ↓
└─ 默认 → firecrawl（96% 覆盖 + 高质量 markdown）

# 三个爬虫工具的定位
┌──────────┬─────────────────────────┬──────────────────────┐
│ 工具     │ 何时用                  │ 何时不用             │
├──────────┼─────────────────────────┼──────────────────────┤
│ firecrawl │ 通用首选                │ API 受限/本地化时    │
│ Crawl4AI  │ firecrawl 失败时 fallback │ —                    │
│ Playwright│ 登录、点击按钮、截图 SPA│ 简单页面（开销大）  │
└──────────┴─────────────────────────┴──────────────────────┘
```

**统一调用入口**（推荐用这个）：

```python
from adapters import scrape_with_fallback

result = scrape_with_fallback(
    url="https://example.com/features",
    need_screenshot=True,    # firecrawl 截图
    need_login=False,        # 不需要登录
)
print(result["scraper"])  # "firecrawl" | "crawl4ai" | "playwright"
print(result["markdown"][:500])
```

自动决策 + 失败时回退到下个工具。

└─ 否（需要视觉/代码/社交分析）？
   ├─ 截图/图片 → zai MCP analyze_image
   ├─ 架构图 → zai MCP understand_technical_diagram
   ├─ UI 设计 → zai MCP ui_to_artifact / ui_diff_check
   ├─ 开源代码仓库 → zread MCP / Agent-Reach GitHub channel
   ├─ 产品演示视频 → Agent-Reach YouTube / zai MCP analyze_video
   ├─ 社交信号（X/小红书/微博/B站） → Agent-Reach
   └─ 语义搜索 → Agent-Reach Exa channel
```

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

## Step 2 · 深度爬取（每个竞品并行 · 优先 firecrawl）

```python
# === 主爬取：firecrawl（96% 覆盖 + JS 重度 + 结构化 JSON）===
firecrawl.scrape(
  url="<竞品官网>/",
  formats=["markdown"],
  only_main_content=True
)
firecrawl.scrape(
  url="<竞品官网>/pricing"
)
firecrawl.scrape(
  url="<竞品官网>/features"
)
firecrawl.scrape(
  url="<竞品官网>/customers"
)

# === 进阶：firecrawl.agent（自动导航交互）===
# 适合需要点击/滚动才能看到的内容（如需登录后的功能演示）
firecrawl.agent(
  prompt="访问这个产品的定价页，列出所有套餐名称、价格、限制、功能对比",
  urls=["<竞品官网>/pricing"],
  model="spark-1-pro"
)

# === 进阶：firecrawl.crawl（一次抓全站）===
firecrawl.crawl(
  url="<竞品官网>",
  limit=50,  # 最多抓 50 页
  include_paths=["/blog/*", "/docs/*"]
)

# === Fallback：firecrawl 失败时用 web-reader / WebFetch ===
mcp__web-reader__webReader(url="<url>")
WebFetch(url="<url>")
```

每个竞品的内容写到 `OUT_DIR/02-raw/<name>.md`。

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

## Step 3 · 结构化分析（13 字段 + 视觉/代码/视频/社交信号）

读取 `OUT_DIR/02-raw/<name>.md`、`*-visual.md`、`*-video.md`、`*-code.md`、`*-social.md`，按 `analysis-framework.md` 提取 13 个字段。

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
