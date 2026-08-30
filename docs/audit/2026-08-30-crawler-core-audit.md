# youzi 爬虫核心审计报告(2026-08-30,第 1-12 轮 loop)

> 定位对照:业界顶尖竞品情报收集 skill。
> 审计范围:adapters 全部 5 引擎 + fetch.py 编排 + sufficiency/deep_link/resolver + 上层管线接口。
> 方法:全部结论均有代码行号 + 可复现实验(离线 monkeypatch / 真实网络探针)双重证据。
> 实测环境:Python 3.13.2 / macOS / 无 FIRECRAWL_API_KEY / 无 JINA_API_KEY。

## 0. 实测基线(先说结论:底子是好的)

| 实验 | 结果 |
|---|---|
| pytest 离线套件 | **120 passed**,`-m "not network"` |
| ruff check | **0 违规** |
| 真实探针 wati.io/pricing(scrape_smart) | 24.3s,3 引擎并行,playwright q=0.69($29/$39/$119/$149/$279/$349)、jina q=0.30 同价 → 交叉验证成立;trafilatura 只见 ₹999(地理变体) |
| 端到端探针 respond.io + ycloud(budget 120s) | **14.4s / 32.2s,10 页全部 sufficient,0 failures**;跨子域 docs 发现正常(docs.ycloud.com) |
| 引擎统计(storage/engine-stats.json, 08-30) | playwright pricing q=0.38(n=16),trafilatura pricing q=0.59,newspaper3k pricing q=0.84(n=3),jina ok=1.00 但 q=0.17-0.30 |

结论:**快乐路径上的多引擎交叉验证架构是业界正确方向,且工程化程度高**(事故驱动注释、诚实预算、证据台账)。以下问题全部是"从优秀到顶尖"的差距,不是基础性错误。

---

## 1. P0 — 必须立即修(正确性/韧性,全部实验复现)

### P0-1 单页异常炸掉整个采集运行 ⚠️ 已复现
- `scripts/fetch.py:763` `asyncio.gather(*tasks)` 无 `return_exceptions=True`;`fetch.py:854` `ex.map` 迭代时异常继续向上传播。
- 复现:monkeypatch `_fetch_page` 使 features 页抛 RuntimeError → `fetch_competitor` 整体抛出;在 main 的 `ex.map` 下会**击穿全部竞品**,已采集的台账也不落盘。
- 修复:`runner` 内 try/except 单页降级为 failure 记录;`main` 改 `submit + as_completed` 隔离竞品级异常。

### P0-2 robots.txt 合规门双重失效 ⚠️ 已复现
- (a) 失败缓存键错误:`fetch.py:237` 失败时 `setdefault(domain_url, [])` 存的是**完整 URL**,而命中查询用 origin(`:215`)→ 失败永不缓存,每 URL 重拉。
- (b) 裸 urllib 无 certifi SSL context(`fetch.py:219-224`),本机(python.org 构建)SSL 校验必失败 → 返回 [] → **所有站点按允许处理**。实测 `https://www.wati.io/robots.txt` 复现。同类问题在 `network_gates.py:26-30` 已修(certifi context),但没共享给 fetch。
- 修复:抽出共享 `http_core`(certifi context + 统一 UA + 超时),robots 失败缓存键改 origin;考虑对明确 403/超时的 robots 记 warn 而非静默放行。

### P0-3 firecrawl 头部截断丢定价表 ⚠️ 已复现
- `adapters/firecrawl_scraper.py:38-41` `_truncate` = 纯头部截断;`adapters/__init__.py:514-525` `truncate_md`(头+尾)是为同类事故专门设计的,firecrawl 没用。
- 复现:`'CSS垃圾'*20000 + '||TAIL_PRICING_TABLE||$59...'` → firecrawl 版丢尾部,truncate_md 版保留。
- 修复:`_truncate` 改调 `truncate_md`。firecrawl 是"有 key 时插首位"的主力引擎,50K+ 页面尾部套餐表全丢。

### P0-4 run_youzi 串行循环丢失竞品级并行
- `scripts/run_youzi.py:53-55` 串行 for 循环调 `fetch_competitor`;竞品级 3 并发只在 `fetch.py` CLI 路径(`fetch.py:53,853`)成立。SKILL.md:134 宣称的并行在主 runner 路径不存在。
- 修复:run_youzi 复用 fetch 的 ThreadPoolExecutor 逻辑(或直接调 fetch.main 的并行函数)。

### P0-5 deep_link 兜底永远无法交叉验证
- `fetch.py:471-483`:deep_link 结果只挂单引擎 `deep_link` → 定价页四级回退的第三级产出**必然 insufficient**(需 ≥2 独立引擎)。兜底兜不出 verified,前功尽弃。
- 修复:deep_link 定位到 URL 后,用该 URL 再跑一次双引擎 `scrape_smart`(例如 playwright+trafilatura),把 deep_link 只当"URL 发现器"而非"内容来源"。

### P0-6 resolver 无 websearch 兜底,"任意赛道"定位名不副实
- `adapters/competitor_resolver.py:193-232`:docstring 声称 source 含 "websearch",实际只有内置表(26 个,偏 WhatsApp/客服)+ 域名猜测。竞品名如 "cursor"、"lovable" → not_found → 整竞品直接失败。
- 修复:接入 websearch 发现(deep_link 已有 DDG 双通道可复用)→ 首个官网结果验证 → 落 domain-guess;内置表降级为缓存。

---

## 2. P1 — 架构层(影响上限)

### P1-1 智能路由是"伪自适应" ⚠️ 已用数据证实
- `recommend_scrapers`(`adapters/__init__.py:237-269`)只能**重排静态白名单内部顺序**,不能把组合外更优引擎纳入;且组合内引擎全量调用、primary 由静态 `_ENGINE_QUALITY`(`:577-584`)决定 → 学习数据对实际结果影响≈0。
- 实测证据:engine-stats 中 newspaper3k pricing q=0.84(全场最高)**不在定价组合**(复现:`'newspaper3k' in recommend_scrapers('https://example.com/pricing')` → False);静态表首位 playwright 实测 q=0.38 < trafilatura 0.59。
- 修复:路由改为"历史分 top-K + ε 探索"真自适应——`recommend_scrapers` 从全引擎池按 (ok×0.6+q×0.4) 分数选 top-3,冷启动(<3 样本)回退静态表;primary 选择同样吃历史分。

### P1-2 学习环与静态优先级双轨制
- `_ENGINE_QUALITY` 静态序与 engine-stats 动态分并存,merge 选 primary 用前者、recommend 用后者 → 两套真相。统一为一个数据源。

### P1-3 并发模型 5 层嵌套,取消不可达
- main ThreadPoolExecutor(3) → 每竞品 `asyncio.run(_gather)` → `to_thread`(页) → 每页 `scrape_smart` 再 `asyncio.run` → `to_thread`(引擎) → playwright 常驻循环线程。每页每次调用建/销毁事件循环。
- jina 节流 `jina_scraper.py:34-41` 在**持锁状态下 sleep** → 预算耗尽时被"取消"的任务线程仍阻塞在锁队列里,线程堆积不可回收;6 页×3 竞品≈18 次 jina 调用×2s 间隔,最坏堆 ~18 个死等线程。
- 修复:节流改 `asyncio` 信号量/条件变量(可等待可取消),或令牌桶;中期把引擎调用统一到一个进程级事件循环,消灭 per-call asyncio.run。

### P1-4 UA/指纹策略 6 处不一致
- playwright 动态版本 UA(真实 chromium 版本)、newspaper3k 硬编码 Chrome/126(`newspaper3k_scraper.py:56`)、trafilatura 默认 UA(无定制)、jina `youzi-jina-reader/1.0`、robots `youzi-intel/2.0`、network_gates Chrome/126。
- playwright 冷启动回退 `_scrape_cold` 用 `_ENGINE.ua()` 但引擎未启动 → 版本回落硬编码 "126"(`playwright_scraper.py:120,161`)。
- 影响:同一站点不同引擎指纹差异大,Cloudflare 类防护可关联识别。修复:全局 UA 生成器(读真实 chromium 版本,进程内缓存),所有 HTTP 出口统一。

### P1-5 变体定价的交叉验证语义
- 实测 wati:trafilatura 见 ₹999(地理变体),playwright/jina 见 $119 → "同一 token 归票"设计对变体无效。当前靠"≥2 引擎同 token"兜住了,但 trafilatura 那票被浪费。
- 建议:投票键归一已做(`price_vote_key`);进一步在 LLM Step 3 按"档位名+相对序"做变体归并,而非 fetch 层硬做。

---

## 3. P2 — 声明与实现 drift(SKILL.md 宣传超前于接线)

| # | 声明 | 实际 |
|---|---|---|
| 1 | SKILL.md:163 `toggle_crawl.py` 伪引擎键 | 文件不存在,全库 0 hit |
| 2 | SKILL.md:41-42 tech_signals/user_feedback 充分性闭环 | `deep_link.locate_tech/locate_feedback`、`sufficiency.assess_tech_signals/assess_feedback` 生产 0 调用(死代码半成品) |
| 3 | SKILL.md:161 lessons "下次运行先读" | `intel-lessons.json` 只写不读(audit.py:628-650 写,无读者) |
| 4 | SKILL.md:40 "台账标 from_cache" | `from_cache` 只在内存 pages_entry(fetch.py:549-552),claims-manifest.json 无此字段 → G2 `pricing_from_cache` 跳过分支实际永远不触发 |
| 5 | 升级梯对 docs/about/blog 等 | `_ENGINE_LADDER_EXTRA` 10 类梯位是死配置,`ladder_engines` 只被以 "pricing" 调用(fetch.py:421) |
| 6 | scraping-tools.md:45 "六门禁" | 实际 7 门(G1-G7) |
| 7 | sufficiency.py:4-6 docstring 引用 `_crawl_page` | 已不存在(现名 `_fetch_page`) |
| 8 | audit 与 fetch 的 kind/kinds 不同步 | fetch 已多值化 kinds,但 audit.py:143,190 仍读单值 kind → home_as_pricing 场景漏判 |
| 9 | pre-commit 头注释引用 crawl4ai | V2 已删除该引擎 |
| 10 | adapters/__init__.py:16 注释 "playwright pricing q=0.50" | 实测 0.38(08-30) — 注释性能数字未标日期来源,已腐烂 |

---

## 4. P2 — 死代码与重复实现

**死代码**(删除或接线,二选一):
- `adapters.scrape_with_fallback` + `strategy="fallback"`(无调用者)
- `deep_link._bing_unwrap / locate_tech / locate_feedback / _reddit_feedback / _g2_direct_feedback`
- `sufficiency.assess_pricing / assess_tech_signals / assess_feedback`(0 调用)
- `pricing_tokens.is_price_like`(0 调用)
- `run_youzi --max-chars/--timeout`(解析后未使用)
- playwright MCP 配置块(`playwright_scraper.py:664-678`)
- `storage/request_queues/ + key_value_stores/`(V1 crawlee 遗留,08-25)

**重复实现**(合并到单一模块):
- `_registrable_domain` ×2(gates/fetch);quote 剥壳正则 ×2(gates/audit);JS 壳判定 ×2 且阈值不一致(sufficiency 200 字 vs deep_link 300 字);engine_index 重建 ×2(verify/audit);TTL=14 常量 ×2(verify/fetch);伪造引文黑名单 ×2(gates/render)。

**结构**:
- render.py 5543 行单文件(模板引擎+渲染+校验混合)。
- adapters/__init__.py 837 行:路由/统计/合并/编排四职责混在一文件 → 建议 routing.py / merge.py / stats.py / __init__.py(纯出口)。
- scripts/fetch.py 872 行:页面任务/台账/缓存/robots 混合 → ledger.py(台账+缓存)/ fetch.py(纯编排)。

---

## 5. 系统性升级方案(按优先级排序)

### 第一批(P0,预计 1-2 天,全部可独立验证)
1. **异常隔离**:fetch `_gather` 每页 try/except + main 改 as_completed;补 3 个回归测试(单页炸/单竞品炸/台账仍落盘)。
2. **firecrawl 截断**:`_truncate` → `truncate_md`;补截断保尾测试。
3. **robots 修复**:共享 http_core(certifi+UA),失败缓存键改 origin,robots 不可达时记 warn 进 failures。
4. **run_youzi 并行**:复用竞品级 ThreadPoolExecutor。
5. **deep_link 后置双引擎验证**:定位 URL 后重爬双引擎,让第四级回退真正能 verified。
6. **resolver websearch**:复用 deep_link DDG 通道做官网发现;内置表降级为缓存。

### 第二批(P1 架构,预计 3-5 天)
7. **真自适应路由**:全引擎池 top-K + 冷启动回退 + primary 吃历史分;engine-stats 快照写入注释时带日期。
8. **统一 HTTP 基座 http_core.py**:certifi context、UA 生成器、重试退避、超时,jina/newspaper/firecrawl-rest/robots/network_gates 全部走它。
9. **并发模型简化**:jina 节流改可取消等待;评估单进程事件循环化(消灭 per-call asyncio.run)。
10. **from_cache 落台账**:manifest.fetched 条目写入 from_cache + pricing_from_cache 语义接通 G2。

### 第三批(P2 清理,预计 2-3 天,可并行)
11. drift 清单逐条修:接线或删除 tech_signals/feedback 闭环、lessons 读取接线(fetch 启动时读 hint 进 prompt 上下文)、死配置梯位接线到 _fetch_page 通用分支、kind/kinds 统一、文档口径校正(七门禁、删 toggle_crawl 引用)。
12. 死代码删除 + 6 处重复实现合并。
13. 结构拆分:adapters 职责分层;render.py 按职责拆包(单独一批,风险大)。
14. mypy 覆盖扩到 scripts/ + gates/audit/fetch;pre-commit 注释更新。

### 验收标准(每批)
- 离线 120+ 测试全过 + 新增回归测试过
- `python3 scripts/fetch.py --competitors "respond.io,ycloud" --budget 120` 无回归(≤35s/竞品、10 页 sufficient)
- network marker 探针:`pytest -m network` 通过
- SKILL.md 每条声称都能指到代码行(声明-实现一致性检查)

## 6. 与"业界顶尖"的差距总结

| 维度 | 现状 | 顶尖标准 | Gap |
|---|---|---|---|
| 架构 | 多引擎交叉验证+事故驱动精细化 ✓ | 同左 + 真自适应 + 故障隔离 | 伪自适应路由、单点异常放大 |
| 逻辑 | 定价四级回退+诚实预算 ✓ | 同左 + 全字段闭环 | 梯位/反馈闭环半成品、deep_link 单引擎死局 |
| 流程 | fetch→render→verify ✓ | 声明=实现 | 9 条 drift、串行化丢失并行 |
| 代码 | 注释即事故史 ✓ | 同左 + 无死代码 | 6 处重复、~8 处死代码 |
| 结构 | 模块边界清晰(fetch/adapters/gates) | 职责单化 | render 5543 行、__init__ 四职责 |
| 规范 | 120 测试+ruff+pre-commit ✓ | +类型+盲区覆盖 | mypy 只盖 render+adapters;韧性零测试 |

**总评**:取证内核(fetch+adapters)设计成熟度高于多数同类开源方案,交叉验证+诚实预算是真正的差异化优势;当前最大风险不是"爬不到",而是(a)异常放大、(b)声明超前于实现、(c)路由不自适应导致引擎组合次优。按上述三批走完,youzi 有真实路径到"业界顶尖"。

---

# 第 2 轮深化(2026-08-30):pricing_tokens / gates 内部 / 门禁旁路 / 质量分语义

> 本轮覆盖第 1 轮未及深水区:pricing_tokens 正则、gates G1-G7 逐条逻辑、verify/audit 消费端、deep_link 实测、4 引擎定价横评扩样。**含 1 项对第 1 轮结论的修正**。

## 2.1 新发现 P0:G2 门禁旁路(反伪造闸门被静默关闭)⚠️ 已复现

- `gates.py:247-248`:`if competitor.get("pricing_from_cache"): continue` —— 这个 `continue` 位于 **competitor 外层循环**内、`for field, url, quote in checks` 消费循环(`gates.py:260`)之前。意图是"跳过定价投票行回查",实际效果是**该竞品此前积累的全部 G2 检查项(strengths/gtm/moat/differentiators/tech_signals/feature_catalog/product_momentum)整批丢弃**。
- 复现(离线):同一句伪造引文 `THIS QUOTE IS FABRICATED...`,`pricing_from_cache=True` → G2 hard 命中 **0**;`=False` → hard 命中 **2**。即:只要定价走了缓存回退,该竞品的伪造引文全部免检。
- 对一个以"证据链不可伪造"为核心价值的工具,这是**门禁完整性漏洞**,修复是一行事:把 vote 循环包进 `if not pricing_from_cache:` 而非 `continue`。

## 2.2 新发现 P1:价格 token 正则两处缺口 ⚠️ 已复现

- **漏检**:`$.012` / `$.99` / `$.5`(无前导零小数价)不命中 —— `pricing_tokens.py:29-31` 前缀路要求符号后紧跟 `\d`。而 `gates.py:496-499` 的 G7 语义正则**显式支持** `\.\d+`(注释还拿 `$.012` 举例)——"单一事实源"自己就有两套口径。实测 wild 样本(manychat `$0.082`、tidio `$24.17`)都有前导零,现实风险中低,但 PAYG 计费页写法 `$.5/conv` 存在。
- **误报**:`RS 485`(工业总线,`re.I` 下 `rs 485` 命中 → 投票键 `₹|485`)、shell 变量 `echo $1 $2`(`$|1` `$|2`)。消费链放大:`audit._price_tokens_per_engine`(audit.py:119-130)**跨该域全部页面**计数价格 token —— docs 页里的 shell 示例会虚增 `engines_with_price`,可能把"定价页 0 token"的 gap 掩盖成 partial。
- 修复:前缀路补 `(?:\d[\d,]*(?:\.\d+)?|\.\d+)`;`Rs` 要求后接数字且不可为独立词 + 数字(或要求 `Rs\.`/`₹` 形态);audit 侧只对 pricing URL 页计数。

## 2.3 新发现 P1(实测):质量分与内容完整性脱钩,学习环奖励空壳 ⚠️ live 证据

4 引擎定价横评(2026-08-30,timeout 45s):

| 站点 | 引擎 | len | q | 价格 token |
|---|---|---|---|---|
| manychat.com/pricing (15s) | playwright | 38,136 | 0.26 | **8 个($0.082 PAYG 等)** |
| | trafilatura | **0** | 0.00 | 0 |
| | newspaper3k | **0** | 0.00 | 0 |
| | jina | 40,774 | 0.25 | **8 个(与 playwright 全同)** |
| tidio.com/pricing/ (16s) | playwright | **111,257** | 0.34 | **5 个($24.17/$49.17/$300/16.67 USD)** |
| | trafilatura | 4,842 | **0.76** | **0** ← 当选 primary |
| | newspaper3k | 562 | **0.76** | 0(空壳) |
| | jina | 50,021 | 0.07 | 5 个(跨换行 `$\n24.17` 仍归一成 `$\|24.17`,与 playwright 成票) |

三个结论:
1. **primary 选择失准(实测)**:tidio 的 primary=trafilatura(q=0.76 过 0.5 门槛 + 引擎排名),而它**零价格**;playwright 111KB 完整价格表因 q=0.34(<0.5)落选。合并视图(02-raw/<cname>.md 主体、LLM 若读合并视图)拿不到价格表 —— 价格只活在各引擎原文里,靠 vote_detail 兜底。
2. **_md_quality 度量的是洁净度而非完整性**:562 字节空壳能拿 0.76(结构干净),111KB 全量内容只有 0.34。length_bonus 权重仅 0.2 且对数,拉不回。
3. **学习环放大空壳**:这些 q 分数经 `record_engine_outcome` 写进 engine-stats,又经 `_engine_stats_score` 影响路由排序 —— "在某站碰巧抽到干净空壳"会被反馈成"该引擎在该类型页优秀"。这是对第 1 轮 P1-1 的重要补充:自适应路由的**前提**是质量分按页面类型加权内容完整性(定价页 = 价格 token 覆盖率;docs = 正文长度/标题结构),否则学习环学的是噪声。

**修正第 1 轮 P1-1 的表述**:"newspaper3k pricing q=0.84 应进定价组合"是幸存者偏差 —— 本轮两个新站点实测它 0 字节/562B 空壳。正确结论:先修质量分语义(按 url_type 加内容完整性维度),再谈自适应组合;静态表把 playwright+jina 作为定价双主力在本轮实测中反而被验证是对的(两站交叉验证均由 playwright×jina 达成)。

## 2.4 deep_link 实测 + P0-5 修复成本下调

- 实测 `locate_pricing_page('chatbase.co')`:17.3s → 正确定位 `https://www.chatbase.co/pricing`,trafilatura 通道,5.4KB 正文。搜索链路(DDG lite 主 + html POST 兜底)工作正常。
- 新发现:`_scrape_and_verify` 内部本来就调 `scrape_smart`(pricing 类型 = 3 引擎组合),**但返回时只保留单引擎标签 + 合并文本,丢弃各引擎原文** → fetch.py 四级回退第三级拿到的天然就是"单引擎",交叉验证注定凑不齐。修复只需让 deep_link 返回 `all_results`(各引擎原文已在内),成本远低于第 1 轮预估的"定位后重爬双引擎"。

## 2.5 verify/audit 消费端复核

- verify.py 结构清晰无异常;`build_engine_index` 对损坏 engines.json 容错良好。
- audit.py `_has_genuine_pricing_url`(audit.py:142)读单值 `kind` —— 与 fetch 的 `kinds` 多值化不同步(home_as_pricing 场景),与第 1 轮 P2-8 相互印证。
- G2 两级归一化(空白折叠 + markdown 链接剥离)与证据侧清洗对称,设计正确;`_norm_md_stripped` lru_cache(64) 在多竞品大原文下会抖动,仅性能小瑕。

## 2.6 本轮增量修复清单(并入第一批/第二批)

| 优先级 | 修复 | 工作量 |
|---|---|---|
| P0 | G2 `continue` 改包裹式条件(gates.py:247) | 1 行 + 1 个回归测试 |
| P0 | deep_link 返回 all_results,fetch 侧四级回退第三级恢复交叉验证 | ~20 行 |
| P1 | PRICE_TOKEN_RX 补 `.\d+` 前缀路;Rs 词边界收紧;audit 价格计数限定 pricing 页 | ~10 行 + 用例 |
| P1 | _md_quality 按 url_type 加内容完整性维度(定价页:价格 token 覆盖;通用:有效正文长度占比),primary_key 第一维改用"类型感知质量" | ~40 行 + 用例 |
| P2 | _norm_md_stripped 缓存上限调大或不缓存 | 1 行 |

**第 2 轮总评**:门禁层(gates/verify)的架构与事故驱动注释质量很高,但 G2 旁路说明**门禁自身缺少"门禁有效性"的元测试**(每个 gate 需要"构造违规样本 → 必须命中"的对抗用例);质量分语义是当前对"顶尖"目标阻碍最大的单点 —— 它同时扭曲 primary 选择、engine-stats 学习、audit 覆盖判定三个消费方。建议第一批修复清单加入:G2 对抗回归测试 + 质量分类型感知化,这两个都是小改动大收益。

---

# 修复实施记录(2026-08-30,第一批落地)

## 已实施(10 项,全部带回归测试 + 实测验证)

| # | 修复 | 文件 | 验证 |
|---|---|---|---|
| P0-1a | 页面级异常隔离(runner try/except 降级为诚实失败) | scripts/fetch.py | 回归测试 ✓ |
| P0-1b | 竞品级异常隔离(submit+as_completed 替代 ex.map;排序键改输入名) | scripts/fetch.py | 回归测试 ✓ |
| P0-2 | robots:失败缓存键改 origin + certifi SSL context | scripts/fetch.py | **实测:wati 17 条/respond 5 条 disallow 首次真实拉到(修复前 SSL 必败静默放行)** ✓ |
| P0-3 | firecrawl `_truncate` → `truncate_md`(保尾) | adapters/firecrawl_scraper.py | 回归测试 ✓ |
| P0-4 | run_youzi step2 竞品级 3 并发 + 单竞品崩溃隔离 | scripts/run_youzi.py | 回归测试 + e2e ✓ |
| P0-5 | deep_link 透传 all_results → 四级回退第三级恢复交叉验证 | scripts/deep_link.py + fetch.py | 回归测试(双引擎同价 → sufficient)✓ |
| P0-7 | G2 continue 改包裹式条件,缓存回退竞品不再免检 | gates.py | **对抗测试:同句伪造引文 flag=True 现在 1 命中(修复前 0)** ✓ |
| P1 | PRICE_TOKEN_RX 补无前导零小数($.012/$.99) | pricing_tokens.py | 回归测试 ✓ |
| P1 | audit 价格计数:有定价页时排除 docs/blog shell 噪声,无定价页回退全页 | audit.py | 回归测试 ×2 ✓ |
| P1 | 定价页 primary 加内容完整性维度(有价才有资格) | adapters/__init__.py | **实测:tidio primary trafilatura(零价格)→playwright** ✓ |

## 实施中新发现并修复:P0-8 中段价格截断丢失 ⚠️ live

修复验证过程中实测发现比预估更深的问题:**tidio 定价页 5 个价格全部位于 111K 页面的 65K-95K 中段** —— 头尾截断(40K+10K)把价格全切掉。三处同时失价:
1. 合并视图(primary 原文截断后无价)
2. 证据库 engines.json(fetch._record 同样截断)
3. **jina 引擎级截断**(价格恰好落在 jina 自己的 50K 截断带外 → 交叉验证随机失败,时好时坏)

修复:`truncate_md` 新增 `keep_rx` 保窗参数 —— 匹配 ±context 字符窗口强制保留,预算按窗口数均分,超量窗口诚实标注丢弃;接线到定价页全链路(引擎级 jina/trafilatura/newspaper3k + 合并级 + fetch 证据库级)。

**实测对照(tidio.com/pricing/)**:

| 指标 | 修复前 | 修复后 |
|---|---|---|
| primary | trafilatura(4.8KB,0 价格,q=0.76) | playwright(113KB,5 价格) |
| 合并视图价格 | **0 个** | **5 个($0/$24.17/$300/$49.17/16.67 USD)** |
| jina 引擎输出价格 | 0 个(被自身截断切掉) | 6 个(含此前丢失的 $32.50) |
| 交叉验证 | 随机失败 | playwright×jina 稳定成票 |

## 测试与静态检查

- 离线套件:**120 → 138 全过**(+18 条回归/对抗用例,含门禁对抗样本)
- ruff check / format:**全绿**
- e2e 实测:tidio 单页对照 ✓、robots 真实拉取 ✓、run_youzi 3 竞品并行 e2e ✓

**e2e 终验(run_youzi 3 竞品,原串行路径改并行后)**:
- 墙钟 ~34s 完成 3 竞品(20.1s/21.9s/33.9s 并行;串行需 ~76s)—— P0-4 生效
- respond.io / ycloud:10 页全充分、0 失败(与修复前基线一致,无回归)
- manychat:定价页 playwright×jina 交叉验证成票($0.082 PAYG 价保窗保留);
  testimonials/blog 两页 newspaper3k 被 403 → 诚实记 failures + insufficient
  (外部反爬,非回归;准 > 快 原则的正确行为)
- 证据库保窗落盘验证:3 竞品定价页每引擎原文全部含价格 token
  (respond 含年价 $1,188/$1,908 + 月价;ycloud 三引擎;manychat 双引擎)

## 遗留(第二批,未实施)

- resolver websearch 兜底(P0-6)
- 真自适应路由(质量分已修语义,组合自适应待做)
- 统一 http_core(UA/SSL/重试仍 6 处分散)
- from_cache 落台账(与 G2 豁免语义联动)
- 死代码清理(scrape_with_fallback/MCP 块/crawlee 遗留等)
- render.py 结构拆分、mypy 扩覆盖


---

# 第 3 轮(2026-08-30):SKILL.md 亲核 / render.py 结构 / 第二批修复落地

## 3.1 SKILL.md 逐条亲核结果(修正子代理 2 处误判)

| 结论 | 亲核结果 |
|---|---|
| "render.py 用 Jinja2"(SKILL.md:220) | **为真**(render.py:49 Jinja2 autoescape);错的是 .pre-commit-config.yaml 头注释"自研 {% %} 模板" —— 已修正注释(防误判:差点按子代理结论改错 SKILL.md) |
| **新 drift #10:可重入承诺未实现** | SKILL.md:283"重跑只要 30 秒"/:293"重跑时 Step 2 跳过已抓的" —— fetch.py 无任何跳过已抓逻辑(grep 证实),页面默认全量重爬。**已改文档为准确语义**(只重渲染秒级;重爬全量;定价 ≤14 天缓存回退) |
| toggle_crawl.py 不存在(:163) | 确认;年付 toggle 能力真身是 playwright_scraper 内建(`<!-- annual-billing variant (toggled) -->` 附加)。**已改文档为实际机制** |
| 离线门禁只列 G1-G6(:261) | **已补 G7** |
| resolver 猜 6 类路径(:133) | 轻微夸大(实际猜 3 类,其余靠导航发现);低优先未改 |
| lessons"下次先读"(:161) | 是对 LLM 的指引而非代码缺口;建议后续在 Step 0 加显式读取指令 |

## 3.2 第二批修复落地(3 项,全部带测试 + 实测)

| 修复 | 验证 |
|---|---|
| **P0-6 resolver websearch 兜底**(三级:builtin 0.95 → domain-guess 0.4 → websearch 0.6) | 回归测试 ×2(命中官网/拒绝张冠李戴域)+ **实测:cursor→cursor.com 4.3s、lovable→lovable.dev 3.4s**。"任意赛道竞品情报"定位的关键缺口闭合 |
| **from_cache 落台账**(manifest.fetched 条目级标记) | 回归测试:缓存回退命中 → 台账 from_cache=True + 双引擎哈希进 manifest;G2 pricing_from_cache 豁免分支从"不可达"变为可用 |
| 文档同步 ×6 | SKILL.md(G7/toggle/可重入×2)、sufficiency.py docstring(_crawl_page→_fetch_page + 未接线说明)、scraping-tools.md(六→七门禁)、pre-commit 注释(自研模板→Jinja2、crawl4ai 移除) |

## 3.3 render.py 结构审计(替代因限流失败的子代理,亲自扫描)

- 5543 行,分层清晰:Template 类(Jinja2 autoescape 封装,render.py:56)+ 工具函数 + **派生层 ~1140 行**(render.py:165-1307,_derive_inspiration/opportunity/commercial_strategies/gtm/moat/product_overview/user_feedback 等)+ 数据块 + 渲染主体
- **最大结构问题:数据即代码** —— `_CANONICAL_FEATURES_WHATSAPP`(render.py:1308-1972,~660 行硬编码行业功能清单)+ 别名库(render.py:1975+,~440 行)。属受控特性(SKILL.md:292:仅 feature_canonical.enabled=true 启用,? 刻=未命中非不支持),但 1100 行领域数据内嵌渲染器,改数据要动渲染器代码
- 自检机制健全(render.py:5350 严格自检:未解析模板标签/数据完整性/文件大小;--no-check 可跳过)
- XSS 覆盖:3 条针对性测试(javascript: URL 不进 href / 属性逃逸 / 定价来源诚实)
- **拆分建议**(低风险顺序):① canonical 数据 → references/canonical-features.json(纯数据搬移);② 派生层 → render_derive.py;③ 渲染主体保留。①收益最大风险最小

## 3.4 状态汇总

- 测试:**141 全过**(120 原始 + 21 回归/对抗);ruff format/check 全绿
- 三轮累计修复 **14 项**(P0×8 / P1×6),全部带可复现验证
- 剩余 backlog(未变):真自适应路由(组合成员级)、统一 http_core(UA/重试 6 处分散)、tech_signals/feedback 自动闭环接线、lessons 读取接线、死代码清理(scrape_with_fallback/MCP 块/crawlee 遗留)、render 数据外置拆分、mypy 扩覆盖


---

# 第 4 轮(2026-08-30):kind/kinds 统一 + 死代码清理 + 收尾审计

## 4.1 修复

| 修复 | 位置 | 验证 |
|---|---|---|
| **audit kind/kinds 多值兼容**(drift #8,最后一条未修 drift) | audit.py 新增 `_entry_kinds()`,`_has_genuine_pricing_url`/`probes` 改读多值 | 回归测试 ×3(home_as_pricing 形态/域名根/旧格式兼容)✓ |
| 死代码清理(6 处,全部先 grep 证实零调用方) | ① `scrape_with_fallback` + `strategy="fallback"` 分支 + `__all__` 条目(adapters/__init__.py)② `_bing_unwrap`(deep_link.py)③ `is_price_like`(pricing_tokens.py)④ playwright MCP 配置块 ⑤ run_youzi `--max-chars/--timeout` 死参数 ⑥ `storage/request_queues/`+`key_value_stores/` crawlee 遗留目录 | `test_scrape_with_fallback_removed` 回归 + 全量套件 ✓ |

## 4.2 收尾审计(最后两块未审内容)

- **install.sh(23K)**:干净 —— 无过时引擎引用,依赖表与 5 引擎白名单精确一致(jinja2/playwright/trafilatura/newspaper3k/lxml_html_clean + chromium),4 平台(Claude Code/opencode/Codex/EasyCode)自动探测,BSD/GNU 兼容,pip 失败 --user 降级。无发现。
- **references/analysis-framework.md(207 行)**:证据铁律与 gates G7 锚点优先级表语义一致(docs 子页 > about/customers > 首页/pricing 默认禁止);13 字段 schema 与 SKILL.md Step 3 同步。无发现。

## 4.3 状态

- 测试:**143 全过**(+1 network marker 默认 deselect);ruff 全绿
- 四轮累计:**16 项修复**(P0×8 / P1×7 / drift 修复×10 文档同步),drift 清单 10 条全部闭环
- 剩余 backlog(需要更大改动或实测支撑,谨慎评估后再动):
  1. 真自适应路由(组合成员级)—— 需先积累修复后质量分语义下的新 engine-stats 基线
  2. 统一 http_core(UA/重试)—— **防误改原则:jina 短 UA 是实测有效的反质询手段,UA 变更必须逐站 A/B 实测,不宜批量统一**
  3. tech_signals/feedback 自动闭环接线(deep_link.locate_tech/locate_feedback 已可用,差 fetch 侧挂点)
  4. lessons 读取接线(fetch 启动时读 hint)
  5. render 数据外置(canonical 660 行 + 别名 440 行 → JSON)
  6. mypy 扩覆盖 scripts/ + gates/audit/fetch


---

# 第 5 轮(2026-08-30):规模实测验证 + 双计费态交叉验证修复

## 5.1 规模实测(5 竞品混合类型,150s 预算,3 并发)

| 竞品 | 解析路径 | 耗时 | 页数 | 不充分 | 失败 |
|---|---|---|---|---|---|
| WATI | builtin | 42.4s | 6 | 无 | 0 |
| Respond.io | builtin | 29.6s | 5 | 无 | 0 |
| linear.app | domain-guess | 30.7s | 6 | 无 | 0 |
| **cursor** | **websearch** | 24.8s | 5 | pricing | 1 |
| **lovable** | **websearch** | 44.5s | 5 | 无 | 0 |

- **websearch 解析的 cursor/lovable 首次走通完整采集管线**(此前直接 not_found)—— P0-6 修复的端到端确认
- 总墙钟 ~75s(5 竞品 3 并发)
- 唯一缺口:cursor 定价不充分 → 顺藤摸瓜发现下述 P1 根因

## 5.2 新发现并修复:双计费态丢失导致交叉验证随机失败 ⚠️ live 前后对照

**机理(cursor.com 实测)**:playwright 的年付 toggle 流程在点击切换**之后**才提取 DOM → markdown 只剩年付态($16/$32);静态引擎(jina)渲染默认月付态($20/$40)→ 两引擎永远凑不到相同 token → **一切带计费切换的定价页上,交叉验证随机失败**(取决于哪个态被 DOM 捕获)。`_monthly_text` 变量原本只用于 diff 比较,捕获后即丢弃。

**修复**(playwright_scraper.py):toggle 后先尝试 JS 回点月付还原 DOM;还原失败时用 toggle 前捕获的 `_monthly_text` 作为渲染文本(既有的 markdown-vs-text 价格兜底会把默认态提升进 markdown)→ 双态齐备。

**实测前后对照(cursor.com/pricing)**:

| 指标 | 修复前 | 修复后 |
|---|---|---|
| playwright 价格 | $16/$32(仅年付态) | **$20/$40(默认月付态)** |
| 交叉验证票 | 0 → insufficient | **$20、$40 双票(playwright×jina)** |

## 5.3 deep_link CLI 入口(SKILL 工作流可执行化)

- SKILL.md:41-42 一直指示 Step 2.5 用 deep_link 定位 tech/feedback,但模块无 CLI(LLM 只能 python -c 拼函数)。补 `tech/feedback/pricing` 三子命令。
- 实测:`deep_link.py pricing meetbot.com` 17s 定位 help center 真实定价页(文档所述"定价藏 help center"场景),all_results 透传可见;SKILL.md 已补精确调用命令。

## 5.4 状态

- 测试 143 全过;ruff 全绿
- 五轮累计:**18 项修复**(P0×8 / P1×9 + 文档同步),全部带实测或回归验证
- engine-stats 已在修复后语义下重新积累(自适应路由的前置条件)


---

# 第 6 轮(2026-08-30):新基线决策 + canonical 外置 + lessons 接线

## 6.1 证据驱动的架构决策:不建自适应路由(防误判)

修复后质量分语义下的新基线(engine-stats,08-30 19:33,定价桶 n=38-40 成熟样本):

| 引擎 | pricing ok/q | docs | feature | about | blog/customer |
|---|---|---|---|---|---|
| playwright | **1.00/0.40** | — | 1.00/0.22 | — | — |
| trafilatura | 0.82/0.54 | 0.92/0.55 | 0.70/0.44 | 0.88/0.58 | 0.82/0.51 |
| jina | 1.00/0.29 | 1.00/0.10 | 1.00/0.22 | 1.00/0.24 | — |
| newspaper3k | 0.86/0.69(n=7) | — | — | — | 0.91-1.00/0.63-0.74 |

**结论:静态路由表与实测已对齐** —— 第 2 轮的"newspaper3k pricing q=0.84 应进组合"是小样本幸存偏差(n=3→7 后收敛 0.69),而定价交叉验证由 playwright×jina 的价格覆盖承载(q 分低但 ok=1.00)。上一轮的分歧主要是旧质量度量的伪影,已在正确的层(primary 内容完整性维度)修复。**数据不要求组合级自适应机制 —— 不建**(复杂度无证据支撑)。backlog 关闭此项。

## 6.2 canonical 数据外置(结构拆分第一步)

- `_CANONICAL_FEATURES_WHATSAPP`(614 行,32 项 × 8 键)→ `references/canonical-features-whatsapp.json`(带 _meta 说明)
- render.py 内替换为 `_load_canonical_features()`(路径相对 `__file__`,符号链接安装同样生效;加载失败返回空列表 → 权威矩阵降级消失而非崩溃)
- **验证:canonical 启用渲染前后字节级一致(cmp 通过)** —— 零回归
- render.py:**5543 → 4950 行**(-593)

## 6.3 lessons 读取接线(SKILL 承诺落地)

- `fetch.load_lessons_hints(domain)`:`intel-lessons.json` 此前只写不读(audit 写、无读者)—— 现在 fetch_competitor 返回值带 `lessons_hints`,CLI 逐竞品打印(💡 前缀),Step 3 LLM 可见
- 实测:ycloud.com/respond.io → 1 条(定价深挖抓手)、meetbot.com → 1 条(询价制结论直接采信)、unknown.io → 0 条 ✓
- 只读、失败容忍;与 audit 写入构成完整闭环(写→读→提示)

## 6.4 状态

- 测试 143 全过;ruff 全绿
- 六轮累计:**20 项修复/改进**,render.py -593 行
- backlog 余项:http_core 统一(需逐站 A/B,防误改)、tech/feedback 自动闭环(Step 2.5 LLM 工具链已可用)、mypy 扩覆盖


---

# 第 7 轮(2026-08-30):门禁对抗元测试补全 + N1/N2 实测

## 7.1 门禁对抗测试补全(tests/test_gates_adversarial.py,16 用例)

第 2 轮确立的原则「每个 gate 需要违规样本对抗用例」此前只覆盖 G2。本轮补齐:

| Gate | 对抗用例 | 结果 |
|---|---|---|
| G1 | 未抓取 URL 当来源 / 失败页当来源 / 合法来源不误报 | ✓ 全部正确触发/放行 |
| G3 | 单哈希 verified / 陈旧 scraped_at(60 天)/ 空 tiers | ✓ |
| G4 | 静默失败(failed 不进 failures)/ 缺失字段断言来源 | ✓ |
| G5 | 黑名单引文 / repr 泄漏 / 派生板块占位符 | ✓ |
| G6 | 非法 URL / 跨域证据警告 | ✓ |
| G7 | 技术信号锚定价页 / 定价陈述豁免语义 / 功能语义锚域名根 | ✓ |

**结论:16/16 全过,未发现新的门禁旁路** —— G2 是唯一一个(已修)。门禁完整性现已被对抗测试锁定,未来任何 gate 改动若破坏其触发能力,CI 即红。

## 7.2 N1/N2 网络门禁首次实测

- 真实证据包回访(report-test 6 URL):全部 2xx,0 hard 0 warn ✓
- **N1 对抗**:构造死链(nonexistent-domain)→ 正确触发 `回访不可达` hard fail ✓
- N1/N2 从"从未验证"变为"实测 + 对抗双覆盖"

## 7.3 状态

- 测试:**159 全过**(143 + 16 对抗);ruff 全绿
- 七轮累计:20 项修复 + 门禁体系对抗测试全覆盖(G1-G7 + N1)


---

# 第 8 轮(2026-08-30):mypy 全覆盖 + 用户文档 drift 复核

## 8.1 用户文档复核(此前盲区)

README.md / 使用手册.md / 安装说明.md 扫描过时标记(toggle_crawl/crawl4ai/
scrape_with_fallback/六门禁/串行等):**零命中** —— 此前各轮文档同步到位。

## 8.2 mypy 扩覆盖(backlog 最后一项落地)

- **根因修复**:`scripts/` 无 `__init__.py`,靠命名空间包工作 → 模块可同时以
  `sufficiency` 与 `scripts.sufficiency` 两种名字解析(mypy 报 "found twice",
  且理论上存在双模块实例状态分裂风险)。补 `scripts/__init__.py` 后模块名唯一
  (运行时行为不变,直接 `python3 scripts/xxx.py` 仍可用)。
- **17 个注解级错误全部真修**(零 `# type: ignore`):
  - 容器注解 ×5(_GATES/spans/supplements/extracted/kinds_got/seen/other_by_cat)
  - `Optional` SSLContext 注解 ×2(fetch/network_gates)
  - `**kw` urlopen 改显式分支 ×2(network_gates,与 fetch 同款)
  - `kwargs: Dict[str, Any]`(adapters 引擎参数下发)
  - `_keep_rx` import-as 改显式赋值 ×2;`results` 重复声明(第 1 轮修复引入)
  - render.py `seen` 变量复用 str/dict 双型 → 拆分改名(4 处历史错误,旧钩子从未真正跑过)
- **pre-commit mypy 钩子扩范围**:`render.py adapters` → 全部 9 模块(19 源文件)

## 8.3 验证

- mypy:**19 源文件 0 错误**;ruff 全绿;159 测试全过
- render 字节级对照:canonical 启用渲染输出与修改前 cmp 一致(去重改名零回归)
- verify G1-G7 重跑 exit 0

## 8.4 八轮终态

| 维度 | 状态 |
|---|---|
| 架构 | 多引擎交叉验证 + 四级回退 + 双计费态捕获;静态路由经新基线验证 |
| 逻辑 | 价格保窗截断/变体归票/kinds 多值/lessons 读写闭环 |
| 流程 | fetch→audit→render→verify 全链路实测;deep_link CLI 可执行 |
| 代码 | 零死代码(清理 6 处)、mypy 19 文件 0 错、ruff 0 违规 |
| 结构 | render.py 5543→4950 行(canonical 外置 JSON);adapters 职责注释化 |
| 规范 | 159 测试(含 17 门禁对抗用例)、10 条 drift 全闭环、pre-commit mypy 全覆盖 |

**剩余 backlog(需外部条件,持续开放)**:http_core 统一(需逐站 A/B 实测,
jina 短 UA 是实测有效的反质询手段,不宜批量统一);大规模长时间运行的
engine-stats 持续观察(自适应路由的决策依据随数据演进重评)。


---

# 第 9 轮(2026-08-30):自审累计 diff + 5 竞品全管线交付 + 新挂死线索

## 9.1 新发现(P1 候选,待下轮定位):非内置赛道抓取挂死 ⚠️

- 现象:`fetch.py --competitors "cursor,lovable,windsurf.com" --budget 150` 两轮均
  **timeout(exit 124,>500s)** —— 单跑 cursor 仅 27.8s 正常。150s 预算 × 3 并发
  理论墙钟 ≤200s,实际超 500s → **某引擎/页面在 deadline 之外挂死**
  (候选:windsurf.com 的某引擎无总超时、或 jina 节流锁排队未被 deadline 感知
  —— 与 P1-3"取消不可达"同族)。日志:/tmp/ai-fetch.log。
- 这正是 loop 持续运行的价值:快乐路径八轮全绿,冷门域名的真实鲁棒性问题
  在泛化测试中暴露。

## 9.2 5 竞品全管线交付(用户指定主题)

ycloud / sleekflow / wati / respond.io / meetbot(builtin 混合 询价制 域):

| 阶段 | 结果 |
|---|---|
| fetch | 15-41s/竞品,23 页,0 failures;Sleekflow testimonials、Meetbot pricing 诚实不充分 |
| **lessons 首次实战生效** | meetbot 打印「询价制结论直接采信」、respond 打印「定价深挖抓手」💡 |
| 分析构建 | 41 claims 全部预验证通过;4/5 定价 verified(3 引擎交叉),Meetbot 诚实未验证(询价制=情报) |
| render | exit 0,自检全 ✓,150.7 KB |
| verify | **G1-G7 全过,41 claims,0 硬失败** |

报告:/tmp/youzi-bsp5/report.html

## 9.3 自审

- 累计 diff:-433 行(8 文件);playwright toggle 重排逻辑复读确认连贯
- 顺手清理一处自留的思考痕迹注释


---

# 第 10 轮(2026-08-30):P0-9 并发 import 死锁(faulthandler 定位 + 修复 + 实测验证)

## 根因(faulthandler.dump_traceback_later 线程栈转储)

第 9 轮发现的"AI 赛道组合挂死"(>400s)根因:

- **Python 3.13 per-module import 锁竞争死锁**:cursor+lovable+windsurf 三竞品
  并发时,websearch/domain 解析路径在 worker 线程里**首次并发执行**
  `import requests`(deep_link._search_via_jina / jina / newspaper3k 的
  函数级延迟 import)→ 三线程全部卡死在 `_get_module_lock`,>400s 零进展
- 栈转储铁证:三个竞品线程分别卡在 requests / email._policybase /
  urllib3.six 的 import 锁 acquire;主线程在 as_completed 空等
- 完美解释复现条件:单跑正常(无并发 import)、builtin 5 竞品正常
  (不走 websearch → 不触发 deep_link 的函数级 import)、只有
  "非内置名并发冷启动"才死锁

## 修复(两层)

1. **主线程预热 import**(fetch.py 顶部 `import requests`):worker 内
   import 全部命中 sys.modules 缓存,不再触锁 —— 一行修死锁
2. **deep_link 预算感知**(顺带修的次级问题):locate_pricing_page 新增
   budget_s 参数(步内检查,最坏 ~360s 路径收进竞品预算);fetch 侧
   剩余 <10s 时诚实跳过并记 failure

## 验证

- **同一死锁组合:400s+ 超时 → 50.4s 完成**(cursor 50.1s 全充分 /
  lovable 31.1s / windsurf 26.5s)—— 前后对照实测
- 159 测试全过(修复了 stub 签名随 budget_s 演进);ruff 全绿

## 教训(入方法库)

- 多线程 worker 内的函数级延迟 import 是死锁温床;重依赖应在并发开始前预热
- "预算只在步骤间检查"的结构性缺口:任何无 budget 感知的回退路径
  (搜索/重试循环)都可能把预算撑爆 —— deep_link 已修,后续新增回退
  路径必须带 budget 参数
- faulthandler.dump_traceback_later 是无 py-spy 环境定位挂死的首选


---

# 第 11 轮(2026-08-30):死锁防御纵深 + AI 赛道全管线闭环

## 11.1 死锁防御纵深(P0-9 后续)

- P0-9 只预热了 requests 链(已爆点);trafilatura/newspaper3k/playwright/
  markdownify/bs4 同为 worker 线程函数级延迟 import —— 同类风险
- 新增 `_preheat_engine_imports()`:main() 并发开始前主线程逐个 try-import,
  可选依赖缺席静默跳过(不影响 is_available 语义)

## 11.2 run_youzi lessons 提示补齐

- fetch CLI 已打印 💡 lesson,run_youzi step2 路径漏打(第 6 轮接线时的
  遗漏)—— 补齐,两条路径行为一致

## 11.3 AI 赛道全管线闭环(泛化证明)

用第 10 轮死锁修复后的数据(cursor/lovable/windsurf,fetch 50.4s):

| 阶段 | 结果 |
|---|---|
| 分析构建 | 20 claims 全部预验证通过 |
| render | exit 0,122.2 KB,自检全 ✓ |
| verify | **G1-G7 全过,0 硬失败** |

cursor 定价 verified(2 档);lovable/windsurf 诚实未验证(定价页未交叉
验证,与 fetch 的"不充分: pricing"一致)—— 非 WhatsApp 赛道从 websearch
解析到门禁交付全链路走通。

## 11.4 状态

- 159 测试全过;ruff 全绿
- 十一轮累计:23 项修复/改进;两大赛道(WhatsApp BSP + AI 编程助手)
  全管线实测交付


---

# 第 12 轮(2026-08-30):network 验收套件复活(V1 契约腐烂发现 + 重写 + 实跑)

## 发现:发布验收套件自 V2 重构起就是死的

- `tests/test_e2e_real.py`(network 标记,默认 deselect)期望 fetch.py 直接
  产出 03-analysis.json —— **V1 契约**。2026-08-27 V2 重构删除脚本侧语义
  提取(Step 3 归 LLM)后该测试必然 FileNotFoundError,但因 network 标记
  从未被执行,静默腐烂了 3 天。
- 教训:**默认 deselect 的测试 = 不存在的测试**。发布验收路径必须有
  一次真实执行才算数。

## 重写(V2 现实)

- 内置 `build_analysis_from_evidence()`:Step 3 的程序化替身,铁律与 LLM
  相同 —— quote 写入前 gates._quote_grep 预验证、定价 tier 只取跨引擎
  交叉验证票、G7 锚点避域名根
- 管线:fetch(--budget 150)→ 证据构建 → render → verify 离线门禁(必须全绿)
  → N1 网络门禁(10 URL 真实回访)

## 实跑结果

- `pytest tests/test_e2e_real.py -m network`:**1 passed** —— 全管线绿
- 产物:/tmp/youzi-e2e-acceptance/(report.html + verify-report.json)
- 离线套件:160 全过;网络验收从"从未执行"变为"实测通过"

## 状态

- 十二轮累计:23 项修复 + 1 套验收套件复活;测试 160(离线 159 + 网络 1)
