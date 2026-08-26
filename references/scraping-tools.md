# 2026 年最火爬虫技术全景 + 集成方案

> 写给要扩展 /youzi 工具链的人
> 不重复造轮子，按场景选最合适的工具。

---

## 📊 速查矩阵（按类别）

| 类别 | 推荐工具 | /youzi 集成价值 |
|------|---------|---------|
| **LLM-ready 整站抓取** | 🔥 **Firecrawl**（已集成）| ⭐⭐⭐⭐⭐ |
| **多平台统一接入** | 🔥 **Agent-Reach**（已集成）| ⭐⭐⭐⭐⭐ |
| **开源 Python 爬虫** | 🔥 **Crawl4AI**（已集成）| ⭐⭐⭐⭐⭐ |
| **现代爬虫框架（反爬）** | 🔥 **Crawlee**（已集成 v1.2）| ⭐⭐⭐⭐ |
| **隐身浏览器（反 Cloudflare）** | 🔥 **Camoufox**（已集成 v1.2）| ⭐⭐⭐⭐ |
| **轻量 URL→Markdown** | 🔥 **Jina Reader**（已集成）| ⭐⭐⭐⭐ |
| **AI 自动生成 schema** | 🔥 **ScrapeGraphAI** | ⭐⭐⭐⭐ |
| **AI 搜索/语义** | 🔥 **Tavily** / **Exa** | ⭐⭐⭐⭐ |
| **云爬虫平台** | 🔥 **Apify** | ⭐⭐⭐ |
| **企业级反爬** | **Bright Data** / **Oxylabs** | ⭐⭐⭐ |
| **隐身浏览器（云）** | **Browserbase** / **Browserless** | ⭐⭐ |
| **浏览器自动化** | **Playwright MCP**（已集成）| ⭐⭐⭐ |
| **图结构 AI** | **ScrapeGraph** | ⭐⭐⭐ |
| **Python 框架** | **Scrapy**（已集成）| ⭐⭐ |
| **反爬绕过** | **ZenRows** | ⭐⭐⭐ |

---

## 🔥 一、开源 AI 爬虫（强烈推荐）

### 1. **Crawl4AI** ⭐⭐⭐⭐⭐

**最强开源 LLM 爬虫**，GitHub 8K+ stars，Python 库。

**能力**：
- 🚀 专为 LLM 优化输出（Markdown / JSON / 结构化）
- 🧠 自适应 LLM 提取（不用写 XPath）
- 🌐 并发抓取（数倍性能提升）
- 🔌 多 LLM 支持（OpenAI / Claude / Ollama / DeepSeek）
- 🎯 浏览器池（避免被封）

**为什么 /youzi 应该集成**：
- 免费开源（相对于 firecrawl 按量付费）
- 数据本地化（隐私场景）
- 可深度定制（13 字段提取可程序化）

**安装**：
```bash
pip install crawl4ai
crawl4ai-setup  # 安装浏览器依赖
```

**集成到 /youzi（在 render.py 加一个 adapter）**：
```python
# youzi/adapters/crawl4ai_scraper.py
import asyncio
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig

async def scrape_with_crawl4ai(url: str, prompt: str = None) -> str:
    """Crawl4AI 抓取 + LLM 提取"""
    async with AsyncWebCrawler() as crawler:
        result = await crawler.arun(
            url=url,
            config=CrawlerRunConfig(
                extraction_strategy="llm" if prompt else "markdown",
                extraction_prompt=prompt,  # 例如："提取 13 字段（功能、价格、用户...）"
            ),
        )
        return result.markdown if not prompt else result.extracted_content

# 在 SKILL.md crawl-strategy.md 中加 Crawl4AI 作为优先级
```

**优先级建议**：作为 firecrawl 的**免费替代**或**fallback**。

---

### 2. **ScrapeGraphAI** ⭐⭐⭐⭐

**类比**：用 LLM 自动生成抓取流程（不用写 XPath / CSS selector）。

**核心创新**：
- 你说"提取所有竞品的价格和功能"，LLM 自动生成抓取策略
- 支持图结构（Graph），多步抓取并合并
- 支持 GPT-4 / Claude / 本地 LLM

**为什么 /youzi 应该集成**：
- **13 字段提取**可以直接用 `${competitor.pricing}` 这种自然语言 schema
- 比 firecrawl 的 JSON schema 更灵活
- 处理**非结构化页面**（论坛、博客评论）特别好

**安装**：
```bash
pip install scrapegraphai
# 需要 OpenAI 或 Anthropic API key
```

**集成示例**：
```python
from scrapegraphai.graphgraphs import SmartScraperGraph

graph = SmartScraperGraph(
    prompt="""提取以下字段：
    - 公司名称
    - 产品功能列表（数组）
    - 定价（字符串）
    - 目标用户（数组）
    - 这家公司的 3 个最强项 + 3 个最弱项（基于证据）""",
    source="https://example.com/features",
    config={"llm": {"model": "claude-3-5-sonnet-20241022"}},
)
result = graph.run()
# = {"company": ..., "features": [...], "pricing": ..., ...}
```

**优先级建议**：替换 /youzi 现在手写的 13 字段提取。

---

### 3. **Tavily** ⭐⭐⭐⭐

**AI 搜索 API**，专为 LLM 优化。

**能力**：
- 🔍 语义搜索（不是关键词）
- 🎯 答案提取（直接返回答案，不是链接列表）
- 📰 新闻/学术/通用三种模式
- 💰 比 Google Custom Search 便宜 10 倍

**为什么 /youzi 应该集成**：
- 替代 firecrawl search 的 WebSearch Prime
- 找到新兴竞品（"AI 编程助手 2026 新出现的"）
- 获取行业报告（"AI 编程市场 2026 规模"）

**集成**：
```bash
export TAVILY_API_KEY="tvly-xxxxx"
```

```python
from tavily import TavilyClient
client = TavilyClient()
results = client.search(
    query="最佳 AI 编程助手 2026",
    search_depth="advanced",
    max_results=10,
    include_answer=True,  # 直接给答案
)
```

**优先级建议**：作为 firecrawl search 的备选。

---

### 4. **Exa** ⭐⭐⭐⭐

**神经搜索引擎**（`exa.ai`），比 Tavily 更精准。

**能力**：
- 🧠 真正的语义搜索（向量检索）
- 🔗 相似页面发现（find similar）
- 📚 学术 / 公司 / 推文多种引擎

**集成同理 Tavily**。

---

## 🌐 二、轻量级 URL→Markdown

### 5. **Jina Reader** ⭐⭐⭐⭐

**免费 + 零配置**：`curl https://r.jina.ai/https://example.com` 直接返回 Markdown。

**能力**：
- 单 URL 抓取
- 20 req/min（免费）/ 500 req/min（key）
- 自动处理 JS 重度

**集成**：
```python
import requests
def jina_read(url):
    return requests.get(f"https://r.jina.ai/{url}", headers={"X-Return-Format": "markdown"}).text
```

**优点**：最简单、零安装。**缺点**：并发差、不能爬整站。

---

## 🏢 三、云爬虫平台

### 6. **Apify** ⭐⭐⭐⭐

**最大爬虫市场**（1500+ 现成 scrapers）。

**能力**：
- 🎭 Stealth browser（自动反反爬）
- 🏪 Actor 市场（别人写好的 scraper 直接用）
- 📊 监控 + 调度
- 🔌 MCP 集成

**集成**：
```bash
# Apify 不是开源，但有 SDK
pip install apify-client
```

**为什么 /youzi 应该集成**：
- 想分析 LinkedIn / Instagram / Twitter 时，Apify 有现成 actor
- 长任务调度（每周自动跑一次）

**优先级**：可选（按需）

---

### 7. **Bright Data** + **Oxylabs**（企业级反爬）

**核心**：商业代理网络 + 验证码破解 + 住宅 IP。

**适合**：跨境电商 / 价格监控 / 公开数据采集。

**为什么 /youzi 不太需要**：
- 我们的目标是**公开技术信息**（功能、价格、用户评价）
- 不需要绕过 Cloudflare 高级防护
- 成本高（$500+/月）

**优先级**：除非你做**价格监控/SERP 抓取**，否则跳过。

---

## 🛠️ 四、浏览器自动化

### 8. **Playwright MCP** ⭐⭐⭐⭐

**微软官方浏览器自动化**，MCP 集成版。

**能力**：
- 全功能浏览器（点击、滚动、登录、下载）
- 多浏览器（Chromium / Firefox / Safari）
- 跨平台

**集成**：
```bash
npm install -g @playwright/mcp
```

**为什么 /youzi 应该集成**：
- 一些竞品网站**必须登录才能看功能**（如 Linear / Notion）
- firecrawl 抓不到的复杂 SPA
- 截图功能（用 `page.screenshot()`）

**优先级建议**：作为 firecrawl 失败时的 **fallback**。

---

### 9. **Browserbase / Browserless**（隐身浏览器）

**核心**：云端 Chrome（防反爬）。

**优先级**：除非你做**高反爬场景**（如 LinkedIn 抓取），否则跳过。

---

## 📊 五、各工具对比决策表

| 场景 | 推荐工具 | 理由 |
|------|---------|------|
| **标准公司官网抓取** | 🔥 Firecrawl（已集成）| 96% 覆盖 + 截图 |
| **需要登录或交互** | Playwright MCP | 唯一能填表单、点按钮 |
| **本地部署 / 隐私** | Crawl4AI | 开源免费 + LLM 灵活 |
| **非结构化页面（论坛/评论）** | ScrapeGraphAI | 自然语言 schema |
| **AI 搜索（找新竞品）** | Tavily / Exa | 语义搜索 |
| **单 URL 快速抓（偶尔用）** | Jina Reader | 零配置 |
| **批量定时抓取** | Apify | 自带调度 |
| **反爬严格（电商/社媒）** | Bright Data | 商业级 |
| **看视频 / 社交** | Agent-Reach（已集成）| 中文社交神器 |
| **结构化提取（13 字段）** | ScrapeGraphAI | 说人话 vs 写 XPath |

---

## 🛠️ 六、集成到 /youzi 的 Recommended Roadmap

### 阶段 1（已有 ✅ 13 个爬虫已注册）
- ✅ **WebSearch**（内置）
- ✅ **WebFetch**（内置）
- ✅ **firecrawl**（96% 网页覆盖 + 截图）
- ✅ **crawl4ai**（开源 LLM-ready markdown）
- ✅ **jina**（轻量 URL→MD）
- ✅ **crawlee**（现代反爬框架）
- ✅ **camoufox**（Firefox 隐身反 Cloudflare）
- ✅ **trafilatura / newspaper3k / readability / markdownify / html2text**
- ✅ **playwright / scrapy / requests-html**
- ✅ **Agent-Reach**（多平台）

### 阶段 2（推荐 ⭐⭐⭐⭐⭐）
- 🔥 **ScrapeGraphAI**（自然语言 schema）
  - 替换 13 字段的手写提取逻辑
- 🔥 **Tavily**（AI 搜索）
  - 替代 firecrawl search

### 阶段 3（按需）
- ⚪ **Playwright MCP**（登录场景）
- ⚪ **Jina Reader**（轻量备用）
- ⚪ **Apify**（批量调度）

### 阶段 4（企业级）
- ⚪ **Bright Data**（价格监控）
- ⚪ **Browserbase**（高反爬）

---

## 🔧 七、推荐实施优先级（最小投入最大收益）

### 立即做（成本<1天，ROI>10x）

**1. Crawl4AI**（开源免费，直接 pip 装）
```bash
# tools/crawl4ai_adapter.py
from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
import asyncio

async def scrape(url, prompt=None):
    async with AsyncWebCrawler() as c:
        result = await c.arun(url, config=CrawlerRunConfig(
            extraction_strategy="llm" if prompt else "markdown",
            extraction_prompt=prompt,
        ))
        return result.extracted_content if prompt else result.markdown
```

**2. ScrapeGraphAI**（替换 13 字段提取）
```python
# tools/scrapegraph_adapter.py
from scrapegraphai.graphgraphs import SmartScraperGraph

def extract_13_fields(html, schema_prompt):
    graph = SmartScraperGraph(
        prompt=schema_prompt,  # "提取功能/价格/用户/强项/弱项..."
        source=html,
        config={"llm": {"model": "claude-3-5-sonnet"}},
    )
    return graph.run()
```

**3. 添加到 `crawl-strategy.md` 决策树**：
```
尝试顺序：
1. firecrawl（96% 覆盖）
2. ↓ 失败 ↓
3. Crawl4AI（开源免费 + LLM 提取）
4. ↓ 失败 ↓
5. Playwright MCP（需要登录）
```

---

## 📚 八、参考资源

| 资源 | 链接 |
|------|------|
| Crawl4AI GitHub | github.com/unclecode/crawl4ai |
| ScrapeGraphAI | github.com/ScrapeGraphAI/ScrapeGraphAI |
| Tavily | tavily.com |
| Exa | exa.ai |
| Jina Reader | jina.ai/reader |
| Apify | apify.com |
| 2026 工具横评 | firecrawl.dev/blog/best-web-scraping-tools |
| awesome-ai-web-scraping | github.com/h4ckf0r0day/awesome-ai-web-scraping |

---

## ✅ TL;DR

**立即集成**（3 个工具，1 天就能搞定）：

1. **Crawl4AI** — 开源免费 / 替代 firecrawl
2. **ScrapeGraphAI** — 自然语言 schema / 替代 13 字段提取
3. **Tavily** — AI 搜索 / 替代 WebSearch

**按需集成**：

4. **Playwright MCP** — 登录场景
5. **Jina Reader** — 轻量备用

**不需要**（除非做企业爬虫）：

- Bright Data / Oxylabs（贵）
- Browserbase / Browserless（重场景）

---

**记住**：`/youzi` 的核心价值是**让产品经理能用自然语言生成竞品分析报告**。**不要为了用最酷的工具而堆砌**。firecrawl + Agent-Reach 已经覆盖 95% 场景，Crawl4AI 是最佳补充。
