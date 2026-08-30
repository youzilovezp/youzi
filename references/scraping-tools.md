# 爬虫工具参考（V2 白名单版）

> 写给要扩展 /youzi 工具链的人。
> V2 现实：本地引擎只有白名单 5 个，统一入口是 `scripts/fetch.py`。

---

## 📊 V2 引擎白名单（2026-08-27 重构，engine-stats n=700+ 校准）

| 引擎 | 定位 | 启用条件 |
|------|------|---------|
| **playwright** | JS 页王者（定价/首页/功能页主力；唯一能登录交互） | `pip install playwright && playwright install chromium` |
| **trafilatura** | 静态正文王者（docs/about/changelog 主力） | `pip install trafilatura` |
| **newspaper3k** | 文章型（blog/customer/testimonials 主力） | `pip install newspaper3k lxml_html_clean` |
| **jina** | 第三方渲染，交叉验证第二票 | 免 key |
| **firecrawl** | 商业最强；检测到 `FIRECRAWL_API_KEY` 自动插首位 | 无 key 不尝试 |

路由表见 `adapters._URL_TYPE_SCRAPERS`（按 URL 类型选组合，定价页 JS 组 + 静态对照双通道）。策略详解看 [crawl-strategy.md](crawl-strategy.md)。

## 🧟 历史教训：被删掉的引擎（不要加回来）

V1 曾注册过一大排引擎（含 Crawl4AI、Crawlee、Camoufox、Scrapy、requests-html、readability、markdownify、html2text 等）。2026-08-27 重构全部删除，原因：

1. **实测打不赢** —— engine-stats n=700+ 显示多数引擎在对应页面类型上质量分长期垫底，维护成本 > 收益
2. **低质引擎污染证据** —— 跨引擎"补充段落"把不同引擎的碎片拼在一起，定价页混入对比表价格/缓存价格，是报告价格全错的根因之一
3. **依赖地狱** —— 每个引擎一条 pip 依赖链，安装失败率高，且多数用户根本用不到

**想加新引擎**：先跑 `storage/engine-stats.json` 对照（新引擎必须在目标页面类型上质量分 ≥ 现有主力），再在 `adapters/` 加 adapter + 注册表加一行 + 路由表调位次。不要恢复死引擎。

## 🔧 MCP / 第三方通道（与本地 adapter 无关）

| 通道 | 用途 |
|------|------|
| **firecrawl MCP**（`firecrawl`） | 会话内直接调商业爬虫；本地 adapter 走 `FIRECRAWL_API_KEY` REST，二者独立 |
| **playwright MCP**（`mcp__playwright`） | 会话内浏览器交互 |
| **Agent-Reach** | Exa 语义搜索 / GitHub / YouTube / X / 小红书 / 微博 / B站 / V2EX / 雪球 |
| **zread MCP** | 开源竞品仓库结构与文档搜索 |
| **zai MCP** | 截图/架构图/UI/视频视觉理解 |
| **web-reader / web-search-prime MCP** | 轻量 URL→MD、带 domain/recency 过滤的搜索 |

## 🧭 三层职责（V2 铁律）

1. **脚本只取证** —— `scripts/fetch.py`：URL 发现 → 智能路由爬取 → 落盘 → 台账，无语义提取
2. **LLM 唯一语义提取** —— 套餐/功能/定位/评分全部由 LLM 从 02-raw 证据提取（历史教训：165KB 提取单体已删）
3. **门禁审判** —— `verify.py` 离线八门禁（来源可回溯/quote 回查/定价独立性/缺失诚实/反伪造/URL 卫生/溯源权威性/结构契约）
