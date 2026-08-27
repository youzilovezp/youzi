# youzi V2 精简重构设计（准 > 全 > 美）

日期：2026-08-27
状态：已获用户批准（含 4 项决策：V2 精简重构 / firecrawl 有 key 启用 / 报告全面视觉重设计 / 双 topic 实爬验收）

## 1. 问题与根因

用户报告的三大症状：信息失准、信息缺失、来源定位不准；外加定价排版丑。

基于 engine-stats.json（n=700+ 真实爬取）与 git 历史（同一 demo 修 4 轮仍在修）的根因分析：

1. **三套机器抢着做语义提取**：crawl_competitors.py（165KB，套餐/功能/翻译对齐/黑名单正则通道）+ crawl_summarize.py（关键词提取器）+ LLM（SKILL.md Step 3 的本职）。互相打架，是信息失准的结构性根源。
2. **13 引擎里 8 个是死重**：firecrawl 402 欠费（ok=0.12）、crawl4ai 输出 CSS 垃圾（q=0.09-0.28，曾污染定价证据）、scrapy/camoufox/crawlee/html2text/markdownify/requests_html 几乎无成功记录。补偿层（智能路由→充分性→升级梯→深链→门禁）叠 5 层给死引擎打补丁。
3. **报告半空列**：定价卡月/年分栏但年付数据缺失时渲染全「—」列，Custom 档语义错位塞进月/年格子。

## 2. 目标（优先级序）

1. **准（权威）**：每个字段带证据三元组 {值, source_url, quote}；定价 ≥2 独立引擎一致才 verified；来源必须定位到实际地址（docs 具体子页、社区具体帖子页，非栏目首页/域名根）。
2. **全**：sufficiency 契约驱动引擎升级梯自动重爬，直到达标或预算尽；不达标诚实标「未验证」，绝不伪造。
3. **美**：报告全面视觉重设计，定价区优雅降级（无空列）、Free/Custom 语义档独立呈现。

## 3. 方案选型

- **A. 抽换内脏（选定）**：保留久经考验资产（adapters 统一接口、verify G1-G6、sufficiency 契约、deep_link 深链、render.py Jinja2 核心），删除病灶（8 死引擎、165K 提取单体、97K demo、summarize）。
- B. 全新代码库：已验证资产重做一遍，风险大周期长，无增量收益。否决。

## 4. 架构：三层职责，不再打架

```
脚本层 = 取证（爬取、落盘、台账、URL 精确定位）—— 不做任何语义判断
LLM 层 = 语义（Step 3 逐字段提取 + source_url + quote）—— 唯一的提取者
门禁层 = 审判（verify G1-G6 + sufficiency 契约）—— 不过不交付
```

## 5. 引擎层：13 → 4+1，按信息类型路由

| 信息类型 | 主力 | 交叉验证票 | 依据（engine-stats） |
|---|---|---|---|
| 定价/首页/功能（JS 页） | playwright | trafilatura + jina | playwright q=0.42-0.50 全场最高 |
| 文档/技术 | trafilatura | jina | docs q=0.57 |
| 博客/客户案例 | newspaper3k | trafilatura | q=0.67 |
| 反馈社区 | Reddit JSON + 搜索通道（deep_link） | — | G2/Trustpilot 多数被封 |
| 有 FIRECRAWL_API_KEY 时 | firecrawl 插入优先位 | — | 商业最强，402 纯因欠费 |

- **自动切换**：爬后立即 sufficiency 评估 → 不达标沿「该 url_type 历史质量分排序」的升级梯换**未用**引擎重爬 → 达标或 5 分钟/竞品预算尽 → 诚实标「未验证」。
- **firecrawl 探测**：环境变量有 key 即启用（插入各类型引擎组优先位），无 key 不报错不尝试。
- **删除**：crawl4ai、crawlee、camoufox、scrapy、readability、markdownify、html2text、requests_html 共 8 个适配器及其 SKILL.md 宣传文案。
- engine-stats 学习机制保留，仅覆盖存活的 5 引擎。

## 6. 采集层：scripts/fetch.py（新，~数百行）

替代 crawl_competitors.py（165KB）。职责仅四件事：

1. **URL 发现**：resolver 导航猜路径 + 404 由首页导航发现兜底（沿用现有 competitor_resolver.py）。
2. **爬取**：scrape_smart() 智能路由（保留现有 adapters/__init__.py 核心，删死引擎后瘦身）。
3. **落盘**：02-raw 每页每引擎原文独立（all_results），header 记录 Source/Scrapers/Time。
4. **台账**：OUT_DIR/crawl-ledger.json —— {url, engine, timestamp, content_hash, http_ok}。定价 sufficiency 不达标时自动触发升级梯 + deep_link 搜索发现官方定价页。

**删除**：套餐提取通道、功能提取通道、跨家翻译对齐、垃圾黑名单、G2 回查清洗等全部正则启发式——语义提取 100% 归 LLM（Step 3）。

## 7. 验证层：原样保留 + 强化

保留：G1 来源可回溯 / G2 quote 回查 / G3 定价独立性+TTL / G4 缺失诚实 / G5 反伪造 / G6 URL 卫生 + N1/N2 网络复核（opt-in）。

强化：
- source_url 必须能在 crawl-ledger.json 里找到对应抓取记录（verify.py 查台账而非 02-raw 文件名猜测）。
- tech_signals 锚定 docs 具体子页（sufficiency.assess_tech_signals 现有语义）。
- user_feedback 锚定具体评论/帖子页（assess_feedback 现有语义）。

## 8. 报告层：全面视觉重设计（templates/report.html 重写）

- **定价区**：每竞品一卡；有年付数据 → 月/年双列 + 省付%徽章 + 折算月价；无年付 → 只渲染月付列（优雅降级，无半空列）；Free 档 $0 绿徽章；Custom 档独立语义行（「联系销售报价」），不占月/年格子。
- **证据链**：报告内每个数字/结论可点开查看 {source_url, quote, 引擎, 时间}。
- **保留**：雷达图、功能重叠热力图、机会象限、主题切换、单文件无外部依赖。
- **风格**：现代 SaaS 情报仪表盘，CJK 友好，月/暗双主题，沿用现有 CSS token 体系但整体重排版。

## 9. 删除清单

| 文件 | 处置 |
|---|---|
| adapters/{crawl4ai,crawlee,camoufox,scrapy,readability,markdownify,html2text,requests_html}_scraper.py | 删除 |
| scripts/crawl_competitors.py（165KB） | 删除（被 fetch.py 替代） |
| scripts/crawl_summarize.py | 删除 |
| scripts/build_whatsapp_demo.py（97KB） | 删除 |
| tests/test_pipeline.py 中提取通道用例 | 删除对应用例 |
| storage/engine-stats.json 死引擎桶 | 清理（保留存活引擎历史） |

保留：adapters/{__init__,firecrawl,trafilatura,newspaper3k,jina,playwright}_scraper.py、scripts/{sufficiency,deep_link,run_youzi}.py、verify.py、gates.py、network_gates.py、render.py（瘦身）、tests/{test_verify,test_pricing_extract,test_accuracy_loop,test_e2e_*}.py、competitor_resolver.py。

## 10. 验收标准（双 topic 实爬）

1. **回归 topic**：WhatsApp-BSP 五家（YCloud/Sleekflow/WATI/respond.io/Unifonic），与第四轮历史数据比对：定价字段准确率不降、来源定位精度提升（docs 子页率）。
2. **新 topic**：任选一个新赛道跑全流程。
3. **硬指标**：verify.py G1-G6 全绿（exit 0）；定价 ≥2 引擎交叉验证（pricing_verified=true 或诚实标未验证）；tech_signals 100% 锚定 docs 具体子页；报告定价区无空列、无 Custom 档错位；单文件 <1.5MB。
4. **pytest**：保留测试全绿。

## 11. 风险与对策

- **5 引擎覆盖不足某站点**：升级梯 + deep_link 搜索发现替代官方源 + 诚实标未验证。历史上 4+1 组合已覆盖 engine-stats 中全部高质量样本。
- **LLM 提取质量波动**：G2 quote 回查硬门禁兜底（grep 不到原文 = 拒收）。
- **报告重写引入回归**：render_smoke.py + test_e2e_offline.py 冻结 fixture 钉住。
