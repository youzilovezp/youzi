# Crawl Strategy · 竞品爬取策略（智能路由版 · 按模块选引擎 + 交叉验证 + 证据可追溯）

## 核心原则：**智能路由选组合，定价交叉验证，逐引擎留证据，绝不伪造**

---

## 🎯 智能引擎路由（`scrape_smart` 默认 auto 策略）

```
scrape_smart(url)  # 默认 auto —— 不需要手动选引擎
├─ classify_url(url) 识别页面类型
│   pricing / docs / feature / about / blog / customer / changelog / dashboard / homepage
├─ 按类型选引擎组合（下表 = adapters._URL_TYPE_SCRAPERS）
├─ 引擎历史成功率/质量加权排序（storage/engine-stats.json，越用越准）
└─ 定价页特殊处理：各引擎原文独立保留（禁止拼接）→ 上层交叉验证
```

### V2 引擎白名单（4 本地 + 1 商业，2026-08-27 重构，engine-stats n=700+ 校准）

| 引擎 | 定位 | 启用条件 |
|------|------|---------|
| **playwright** | JS 页王者（定价 q=0.50 / 首页 q=0.42；唯一能登录交互） | `pip install playwright && playwright install chromium` |
| **trafilatura** | 静态正文王者（docs q=0.57） | `pip install trafilatura` |
| **newspaper3k** | 文章型（blog/customer q=0.67） | `pip install newspaper3k lxml_html_clean` |
| **jina** | 第三方渲染，交叉验证第二票 | 免 key |
| **firecrawl** | 商业最强；`recommend_scrapers` 检测到 `FIRECRAWL_API_KEY` 时动态插首位 | 无 key 不尝试 |

### URL 类型路由表（`adapters._URL_TYPE_SCRAPERS`）

| url_type | 引擎组合（有序，前 = 主力） |
|----------|------------------------------|
| **pricing** | playwright + **trafilatura** + jina（JS 组 + 静态对照双通道：**≥2 独立引擎看到相同价格才 verified**） |
| feature / homepage | playwright + trafilatura + jina |
| docs / about / integration / changelog | trafilatura + jina |
| blog / customer | newspaper3k + trafilatura |
| testimonials | trafilatura + newspaper3k |
| dashboard / 登录墙 | playwright（必须真浏览器） |

（firecrawl 有 key 时对所有类型插首位；`need_login=True` 强制 playwright）

**为什么是白名单而不是全引擎**：V1 曾注册远超必要的引擎，慢是小事；大事是低质引擎的"补充段落"会污染证据 —— 定价页混入其他引擎抓到的对比表价格/附加项价格/缓存价格，就是报告价格全错的根因之一。2026-08-27 重构把实测打不赢的引擎全部删除，只留下白名单 5 个，路由按类型精准出机，定价页只信交叉验证。

```python
from adapters import scrape_smart

# 默认 auto(推荐):智能路由 + 定价页交叉验证隔离
result = scrape_smart("https://example.com/pricing")
print(result["url_type"])    # "pricing"
print(result["scraper"])     # 实际用的引擎

# 手动指定引擎(调试用,只能给白名单内的名字)
result = scrape_smart(url, enabled_scrapers=["jina", "trafilatura"])
```

**需要登录 / 交互** → `need_login=True`（强制 playwright）
**需要截图** → `need_screenshot=True`

---

## 🚀 一次性安装（推荐）

```bash
# 1. 本地引擎（白名单 4 个开源）
pip install trafilatura newspaper3k lxml_html_clean playwright
playwright install chromium

# 2. firecrawl（可选，商业最强）：只需环境变量，REST 直接可用
export FIRECRAWL_API_KEY='fc-xxx'   # https://firecrawl.dev 注册（免费 500 页/月）

# 3. Agent-Reach（中文竞品调研神器）
# 在 Claude Code 里直接说：
"请安装并启用 Agent-Reach（github.com/Panniantong/Agent-Reach）"
# 或手动：
git clone https://github.com/Panniantong/Agent-Reach ~/.agent-reach
# 然后按 README 配置 MCP server
```

安装后 **firecrawl（有 key 时）和 Agent-Reach 提供的 channel 都自动可用**，无需再单独配置。

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

## Step 2 · 深度爬取（fetch.py 一次完成 · 只取证）

```bash
# === 统一入口:fetch.py(V2 取证层,一次跑完整批竞品) ===
python3 scripts/fetch.py --competitors "wati,respond.io" --out-dir OUT_DIR [--budget 300]

# 产出(全部自动落盘):
#   OUT_DIR/02-raw/<name>.md           合并视图(markdown)
#   OUT_DIR/02-raw/<name>.engines.json 每引擎独立原文 —— 交叉验证的数据源
#   OUT_DIR/claims-manifest.json       台账(url × engine × hash × 时间,verify.py 消费)

# fetch.py 内建充分性闭环:定价 ≥2 独立引擎一致才 sufficient;
#   不达标沿升级梯换未用引擎重爬;全灭时 deep_link 搜索发现官方定价页;
#   预算(默认 300s/竞品)耗尽诚实标 insufficient。

# === 单页调试(仅调试用,正式流程走 fetch.py) ===
from adapters import scrape_smart
r = scrape_smart("https://example.com/pricing")
for engine_result in r["all_results"]:   # 各引擎独立原文
    print(engine_result["scraper"], engine_result["markdown"][:200])

# === 特殊场景 ===
# 登录墙 → scrape_smart(url, need_login=True)  # 强制 playwright
# firecrawl MCP 可用时也可直接用 mcp firecrawl 工具,
#   但定价页仍需第二个独立引擎对照 —— 单引擎价格一律标"未验证"

# === Fallback:fetch.py 某竞品全灭(0 页) → WebSearch 找该竞品
#     Wikipedia/Crunchbase/G2 页作替代证据源(如实标注) ===
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

**所有字段遵守上面的「证据规范」**：值 + source_url + 逐字 quote；证据里没有 → 「未验证」。V2 的 fetch.py 只取证不做语义提取（旧版的脚本侧启发式结果已随 165KB 单体删除），LLM 必须回对 02-raw 原文逐字段提取并核对。

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
- **Step 2（爬取）**：fetch.py 内建两级并行 —— 页面级（homepage 先行供导航发现，其余 6 类页面错峰并发，实测单竞品 40-60s → 15-25s）+ 竞品级（3 并发）；playwright 浏览器进程级复用（常驻事件循环 + 按域 context 缓存，省 1.5-2s/页冷启动）
- **Step 2.5（视觉/视频/社交）**：全部并行
- **Step 3（分析）**：每个竞品一个 subagent 并行提取
- **礼貌性**：页面错峰启动、jina 全局限速（免 key ~20 RPM）+ 429 退避、robots.txt disallow 跳过

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
