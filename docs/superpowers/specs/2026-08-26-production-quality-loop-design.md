# youzi 竞品情报平台 · 生产级证据验证闭环 — 设计文档

- 日期：2026-08-26
- 状态：已与用户确认（分层验证 / 证据硬门禁 / 真实数据验收 / 方案 A）
- 范围：youzi 工程（`/Users/zhangpeng/workspace/liaohe/youzi/youzi`）

## 1. 背景与问题诊断

youzi 是一个竞品情报收集平台（Claude skill）：发现竞品 → 多引擎爬取 → LLM 结构化分析 → 渲染 HTML 报告。现有 76 个离线单元测试全部通过，但作为生产级工具存在四类数据质量问题，全部位于现有测试盲区：

| 用户投诉 | 根因（代码级定位） |
|---|---|
| 信息不准确 | ① 定价「双引擎验证」可被同一反爬/区域变体页交叉误证（关联捕获）；② founded/HQ/team_size 为页级归属——年份可能在定价页命中却标注官网来源；③ 14 天缓存 TTL 常量定义了但从未被引用（`_PRICING_CACHE_TTL_DAYS`，永不过期） |
| 信息缺失 | weaknesses/differentiators/opportunities 由脚本返回 `[]`（留给 LLM 但无门禁兜底）；`run_youzi.py` 爬取失败 print+continue 静默跳过，无失败清单 |
| 出处链接不准确 | 猜测的 404 URL 会被写成 `pricing_source`；feature 定位不到出处时默认挂 `default_src`；**全链路无任何 URL 可达性检查** |
| 来源不可靠 | quote-grep 铁律只写在 `references/analysis-framework.md`，无代码强制；反伪造检测仅 3 条历史黑名单字符串 |

结构性根因：**证据链是隐式的**——爬取后即丢弃，analysis.json 只剩结论；render.py 的 self_check 只做结构性检查（URL 数量 >0、无 repr 泄漏），任何「结构良好但事实错误」的数据都能通过全部现有门禁。

## 2. 已确认的决策

1. **分层验证**：离线硬门禁每次必跑（快，进 pytest/CI）；真实网络验证（URL 可达、quote 实时复核）为独立 opt-in 层。
2. **验收标准 = 证据硬门禁**（非全量 schema 套件）：
   - 每条 claim 的 source_url 必须可回溯（本轮成功抓取或缓存验证）
   - quote 必须在对应引擎原文中 grep 命中
   - `pricing_verified=true` ⇒ ≥2 内容独立引擎一致 + 缓存新鲜
   - 无字段静默缺失（缺失必须显式「未验证」）
   - 无猜测 URL 充当 source
3. **真实数据验收**：修复完成后用真实竞品（WhatsApp 赛道内置表：WATI / respond.io / YCloud 等 3-5 家）跑完整爬取→分析→渲染→验证，全部硬门禁绿灯才算交付。
4. **方案 A**：证据包显式落盘 + 独立验证器 + 爬取侧根因修复（否决：塞进 render.py 的 B 方案、质量框架化的 C 方案）。

## 3. 架构

```
爬取管线 ──→ 证据包(02-raw + claims-manifest.json) ──→ verify.py 离线门禁(必跑, <1s)
     │                                                    │
     └──→ 03-analysis.json ──→ render.py ──→ HTML         └→ verify.py --network (opt-in, 慢)

交付条件：render.py exit 0 且 verify.py exit 0（+可选网络层）
```

### 3.1 证据包契约（Evidence Bundle）

爬取管线（`crawl_competitors.py`）在输出 analysis.json 的同时落盘：

```
OUT_DIR/
├── 03-analysis.json            # 结论（已有）
├── claims-manifest.json        # 新增
└── 02-raw/
    ├── <name>.md               # 已有：合并后 markdown
    └── <name>.engines.json     # 新增：每个 URL 各引擎独立原文（quote 离线回查依据）
```

`claims-manifest.json` schema：

```json
{
  "run": {"topic": "...", "started_at": "...", "finished_at": "...", "pipeline_version": "..."},
  "fetched": {
    "<url>": {
      "status": "ok | failed",
      "http_status": 200,
      "engines": {"playwright": {"ok": true, "chars": 12000, "content_hash": "sha256:…"}},
      "fetched_at": "ISO8601",
      "final_url": "重定向后 URL（可选）"
    }
  },
  "claims": [
    {
      "field": "competitors[0].pricing_tiers[1].price",
      "value": "$948",
      "source_url": "https://…/pricing",
      "quote": "…（≤120 字符原文）",
      "engine": "playwright",
      "verified_by": ["playwright", "crawl4ai"],
      "from_cache": false,
      "scraped_at": "ISO8601"
    }
  ],
  "failures": [
    {"competitor": "X", "url": "https://…", "kind": "pricing", "error": "404", "rescued_by": null}
  ]
}
```

设计要点：
- `content_hash` = 引擎 markdown 空白归一化后的 SHA-256，用于「引擎独立性」判定。
- `02-raw/<name>.engines.json` 按竞品组织：`{url: {engine: markdown}}`，只存成功引擎，控制体积（每引擎截断上限沿用 max_chars 逻辑）。
- LLM（Step 3）产出的字段同样写 claims——SKILL.md 工作流增加「写 claim」要求，验证器对 LLM 字段与脚本字段一视同仁。

### 3.2 验证器 `verify.py`（新增，项目根目录，与 render.py 同级）

CLI：

```bash
python3 verify.py --analysis OUT/03-analysis.json \
                  --manifest OUT/claims-manifest.json \
                  --raw-dir OUT/02-raw \
                  [--network] [--sample 10] \
                  [--json OUT/verify-report.json]
```

退出码：`0` 全部硬门禁通过；`2` 任一硬门禁失败（与 render.py 约定一致）；`1` 输入缺失/损坏。

#### Layer 1 · 离线硬门禁（必跑，<1s，进 pytest）

| ID | 门禁 | 级别 | 对准投诉 |
|---|---|---|---|
| G1 | 来源可回溯：每条 claim 的 `source_url` ∈ manifest.fetched 且 `status=ok` | 硬 | 出处链接不准确 |
| G2 | quote 回查：quote（空白归一化）在 source_url 对应引擎原文（02-raw/*.engines.json）中逐字命中 | 硬 | 来源不可靠/伪造 |
| G3 | 定价完整性：`pricing_verified=true` ⇒ `verified_by` ≥2 个引擎且这些引擎的 `content_hash` 互不相同；若 `from_cache=true` 则 `scraped_at` 距今 ≤ TTL(14d)；tiers 非空 | 硬 | 信息不准确 |
| G4 | 缺失诚实：13 字段每个要么有值要么显式「未验证」标记；analysis 中值为空 ⇒ manifest.failures 或 claim 中有对应记录；禁止静默缺失 | 硬 | 信息缺失 |
| G5 | 反伪造：历史黑名单引文、Python repr 泄漏（`['` / `{'` / `"{&#39;`）、派生板块占位符（待补充） | 硬 | 伪造 |
| G6 | URL 卫生：source_url 为合法 http(s) 绝对 URL；指向其他竞品主域 = 警告 | 警告 | 出处错误 |

#### Layer 2 · 网络门禁（`--network`，opt-in）

| ID | 门禁 | 级别 | 说明 |
|---|---|---|---|
| N1 | 可达性：GET 全部**被交付 claim 实际引用**的去重 source_url（「未验证」字段不引用 URL，天然不进本门禁）；浏览器 UA、10s 超时、重试 1 次、限速 ~1 req/s、总预算上限；非 2xx（含重定向终态非 2xx）= 硬失败；跨域重定向 = 警告 | 硬 | |
| N2 | quote 实时复核：轻量引擎（trafilatura，非 13 引擎全开）重抓 source_url，quote 容错匹配（空白/大小写归一化）；不命中 = 警告「证据可能过期」 | 警告 | 线上页面会漂移，权威比对是 G2 对本轮证据的离线回查，故 N2 降级为警告 |
| — | `--sample N` 随机抽样 N 条 source_url 控制耗时（默认全量） | — | |

实现约束：纯 stdlib（`urllib.request` / `hashlib` / `json` / `re`）+ 项目已有依赖，不新增第三方包。网络层并发用 `asyncio` + 限速，与 adapters 风格一致。

#### 输出：verify-report.json

机器可读，供自动修复回路与人工排查：

```json
{
  "passed": false,
  "exit_code": 2,
  "summary": {"hard_failed": 2, "warnings": 5, "claims_checked": 87, "urls_checked": 23},
  "violations": [
    {"gate": "G2", "severity": "hard", "field": "competitors[2].strengths[0].evidence",
     "source_url": "https://…", "detail": "quote 未在 02-raw/wati.engines.json 任何引擎原文中命中",
     "hint": "重写该字段为引擎原文逐字引文，或重新爬取该 URL"}
  ]
}
```

### 3.3 爬取侧根因修复清单

| # | 修复 | 位置 |
|---|---|---|
| F1 | 缓存 TTL 真正生效：`_PRICING_CACHE_TTL_DAYS` 用于过期判定，过期视为 miss；cache 读写加原子写（tmp+rename） | `crawl_competitors.py` |
| F2 | 禁止未抓取 URL 充当 source：定价页全失败 ⇒ `pricing_source=""` + 定价「未能获取」 | `crawl_competitors.py` |
| F3 | founded/HQ/team_size 行级归属：记录命中页 URL + quote，claim 写入 manifest | `crawl_competitors.py` |
| F4 | feature 出处诚实化：定位不到出处 ⇒ `source=None` + `note="未定位出处"`，不再默认挂 default_src | `crawl_competitors.py` |
| F5 | 定价独立性守卫：投票计引擎数前按 `content_hash` 去重——≥2 引擎但内容相同 ⇒ 不算交叉验证 | `crawl_competitors.py` |
| F6 | run_youzi.py 失败写 manifest（print+continue → 记录） | `run_youzi.py` |
| F7 | resolver 文档漂移修正（声称的 WebSearch 回退不存在）；domain-guess 置信度 0.4 在输出中显式标注 | `competitor_resolver.py` |
| F8 | 管线落盘 claims-manifest.json + engines.json（§3.1 契约） | `crawl_competitors.py` / `run_youzi.py` |

### 3.4 闭环运行方式（测试→修复→验证）

```
爬取 → 分析(LLM 写 claims) → 渲染 → verify.py 离线门禁
                                    ├─ exit 2 → verify-report.json {gate, offender, hint}
                                    │           → 运行者按 hint 修 03-analysis.json / 重爬 → 再验证   ← 修复回路
                                    └─ exit 0 →（可选 --network 实网复核）→ 交付
```

- `SKILL.md` Step 5 从「render 自检」升级为「render 自检 + verify 硬门禁」双门禁；Step 3 铁律同步为「写 claim」。
- 新事故形状（真实 e2e 发现的新 bad shape）→ 冻结为离线 fixture 进回归网（延续 `test_pricing_extract.py` 既有模式）。

## 4. 测试计划

| 层 | 文件 | 内容 |
|---|---|---|
| 离线单元 | `tests/test_verify.py`（新） | 每个门禁 G1-G6 各配 pass/fail 合成 manifest fixture；报告格式；退出码 |
| 离线单元 | `tests/test_pipeline.py`（扩） | F1-F7 修复的回归测试（TTL 过期、404 不当 source、行级归属、内容哈希去重） |
| 真实 e2e | `tests/test_e2e_real.py`（新） | `@pytest.mark.network` 标记，默认跳过（`-m network` 触发）；跑真实竞品全管线 + verify --network |
| 验收 | 手动/脚本 | §5 验收标准 |

## 5. 验收标准（Definition of Done）

1. 全部离线测试（含新增 test_verify.py）通过。
2. 真实数据 e2e：WhatsApp 赛道 ≥3 家内置竞品（如 WATI / respond.io / YCloud），完整管线产出的 claims-manifest + analysis + report 通过 `verify.py --network` 全部硬门禁（exit 0）。
3. 该次真实运行的成功引擎原文沉淀为离线 fixture（供 G2 回查测试与后续回归）。
4. 已知四类投诉对应的根因各有回归测试覆盖（TTL / 404-source / 页级归属 / 关联误证 / 静默失败）。

## 6. 非目标（YAGNI）

- 不引入质量框架（great-expectations 风格）、数据库、常驻服务。
- 不做全字段 schema 校验套件、跨实体一致性矩阵（market_segments.players 校验等）——留待证据门禁稳定后按需加。
- 分数 1-10 范围等纯结构问题 → 仅警告级，不在本期硬门禁。
- 不改造 13 引擎 adapter 本身（引擎质量由路由层既有统计机制管理）。
- N2 quote 实时复核不追求 DOM 级精确（页面漂移是常态，权威在离线 G2）。

## 7. 风险与对策

| 风险 | 对策 |
|---|---|
| 真实站点反爬导致 e2e 网络层抖动 | N1 只检查被 claim 引用的 URL——爬取失败但已诚实标注「未验证」并记录 failures 的字段不引用 URL，不触发网络硬门禁（诚实降级可交付）；N1 自带重试与限速 |
| engines.json 体积膨胀 | 仅存成功引擎、单引擎沿用 max_chars 截断、gzip 可选 |
| LLM 不写 claims 导致 G1/G2 大面积红 | SKILL.md Step 3 同步改造 + 脚本侧对无 claim 字段按 G4「缺失诚实」处理而非直接红 |
| 改动 crawl_competitors.py（2429 行）回归风险 | 全部修复配对回归测试；现有 76 测试保持绿 |
