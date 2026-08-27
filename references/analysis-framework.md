# Analysis Framework · 13 字段提取（证据优先协议）

每个竞品按下面 13 个字段提取。**证据铁律（优先级最高）：**

1. **每个字段必须带证据三元组**：`值 + source_url + quote（原文逐字引文 ≤100 字）`
2. **quote 必须能在该 source_url 的 02-raw markdown 里逐字找到** —— 写完用 grep 自查
3. **证据里没有的写「未验证」**，绝不编造、绝不脑补、绝不用训练记忆填充
4. **定价交叉验证**：≥2 独立引擎一致 → `pricing_verified: true`；单引擎 → `false`（报告显示 ⚠ 未验证徽章）；零证据 → 「未能获取，请核对官网」
5. 脚本只取证不做语义提取（fetch.py；旧版脚本侧启发式提取已删）—— 所有字段由 LLM 回对 02-raw 原文提取并核对
6. **溯源优先级（G7 门禁强制）**：功能/技术/差异化类字段（`core_features` / `differentiators` / `tech_signals` / `feature_catalog`）的 source 按下表优先级锚定：

   | 优先级 | 页面类型 | 说明 |
   |--------|---------|------|
   | 1 | docs/features 具体子页 | `docs.xxx.com/<具体主题>`、`xxx.com/features/<子功能>`——论断原文就在那里 |
   | 2 | about/customers | 公司/客户事实类论断 |
   | 3 | 首页（域名根） | **默认禁止**——首页是营销聚合页，不承载具体论断 |
   | 4 | pricing | **默认禁止**——仅当 quote 本身是定价陈述（货币符号+价格数字）时允许 |

   域名根或 `/pricing` 路径锚点 → verify.py G7 hard fail。信息只出现在低优先级页面时，先去更高优先级页面找对应表述重新锚定；抓不到权威锚点的条目**宁可删除也不留低质锚点**。

## 单竞品 schema

```json
{
  "name": "Notion",
  "url": "https://notion.so",
  "tagline": "工作空间 · 一个工具搞定一切",
  "tagline_source": "https://notion.so",
  "founded": 2016,
  "stage": "成熟期",
  "target_users": ["知识工作者", "中小团队", "学生"],
  "core_features": ["AI 逐行审查", "PR 摘要", "一键修复", "安全扫描", "PR 队列分级", "多平台集成", "CLI 审查", "冲突检测", "准入控制", "报告生成", "团队协作", "API 接入"],
  "feature_catalog": {
    "Notion": [
      {"name": "块编辑器", "category": "内容创作", "desc": "Turn any thought into a beautiful page", "source": "https://notion.so/help/what-is-a-block"},
      {"name": "团队协作", "category": "协作", "source": "https://notion.so/help/..."}
    ]
  },
  "pricing": "免费 + Plus $10/月 + Business $15/月",
  "pricing_verified": true,
  "pricing_engines": ["firecrawl", "playwright"],
  "pricing_source": "https://notion.so/pricing",
  "pricing_scraped_at": "2026-08-25 03:12 UTC",
  "strengths": [
    {
      "point": "块编辑器极灵活，几乎可以搭建任何工作流",
      "evidence": "官网主页原文：'Turn any thought into a beautiful page. Notion docs adapt to how you think.'",
      "score": 9,
      "source": "https://notion.so"
    }
  ],
  "weaknesses": [
    {
      "point": "离线能力弱，网速慢时体验差",
      "evidence": "G2 评价原文（需真的读过该页）：'slow offline'",
      "score": 3,
      "source": "https://g2.com/products/notion/reviews"
    }
  ],
  "differentiators": [
    {
      "point": "块编辑器 + 数据库二合一（vs. Confluence 只文档 / Airtable 只表格）",
      "quote": "Turn any thought into a beautiful page",
      "source_url": "https://notion.so/help/what-is-a-block"
    }
  ],
  "tech_signals": [
    {"name": "React 前端", "source": "https://notion.so/blog/..."},
    {"name": "自研增量同步引擎", "source": "https://notion.so/changelog..."}
  ],
  "scores": {
    "feature_richness": 9,
    "ux": 8,
    "pricing_value": 7,
    "integration": 9,
    "ai_capability": 7,
    "momentum": 8
  }
}
```

**注意 evidence 字段的写法**：引文必须是你**真的在 02-raw 里读到的句子**。
写 "G2 评价：xxx" 之前，必须真的爬过那个 G2 页面 —— 否则整条删掉，写「未收集到」。

**core_features 提取要求**：必须扫该竞品的 **docs 证据页**（claims-manifest 台账里 kind=docs 的 URL 对应 02-raw 段落），每家 ≥ 12 条、每条 ≤ 12 字 —— 不得只抄 pricing 页的套餐功能清单（那是商业包装，不是功能证据）。

**feature_catalog 提取要求（§5.2.1 厂商功能矩阵的唯一数据源）**：
- 结构 `{"<竞品名>": [{name, category, desc?, source}]}` —— **漏写整个矩阵空白**（历史事故：Step 3 只写了 core_features 字符串数组，报告最核心的对比矩阵渲染成空壳）
- `category` 跨厂商用**统一分类**（渠道接入 / 收件箱与协作 / 营销获客 / 自动化 / AI 能力 / 电商变现 / 数据与集成 / 安全合规 / 服务），矩阵按 category 分组
- 同一能力跨厂商**用相同 name**（如各家都叫"营销群发"、"团队共享收件箱"），render 才能自动合并对齐；厂商独有功能用其独有名
- `source` 逐条锚定 docs/features 具体子页（G7 会查）；确实锚不到的条目 source 留空字符串（gates 允许，矩阵该格显示 ✓ 但无溯源链接）
- `desc` 可放厂商原文措辞（用户可对比确认同义）

## 评分维度（6 维 · 1-10 分）

| 维度 | 含义 | 评分依据 |
|------|------|---------|
| `feature_richness` | 功能丰富度 | 核心功能数量 + 高级能力（API / 自动化 / 权限粒度） |
| `ux` | 体验 | 上手成本 + 流畅度 + 视觉 + 移动端（看 G2/Capterra 评分） |
| `pricing_value` | 定价性价比 | 免费额度 + 付费门槛 + 与功能匹配度 |
| `integration` | 生态 | 集成数量（Slack/Google/Office…）+ API 开放度 |
| `ai_capability` | AI 能力 | 是否原生 AI + AI 深度（写文档 / 建流程 / Agent） |
| `momentum` | 势头 | 近 12 月发版速度 + 客户增长 + 媒体声量 |

## 跨竞品分析 schema

```json
{
  "market_segments": {
    "all_in_one": ["Notion", "Coda"],
    "vertical_specialist": ["飞书", "钉钉"],
    "open_source": ["AFFiNE", "AppFlowy"],
    "enterprise": ["Confluence", "Quip"]
  },
  "comparison_matrix": {
    "Notion":   {"feature_richness": 9, "ux": 8, "pricing_value": 7, "integration": 9, "ai_capability": 7, "momentum": 8},
    "Coda":     {"feature_richness": 9, "ux": 7, "pricing_value": 6, "integration": 8, "ai_capability": 7, "momentum": 6},
    ...
  },
  "feature_overlap": {
    "block_editor": ["Notion", "Coda", "AFFiNE"],
    "database": ["Notion", "Airtable", "Coda"],
    "ai_writing": ["Notion AI", "Coda AI", "飞书智能伙伴"],
    "offline_first": ["AppFlowy", "Obsidian"]
  },
  "gaps": [
    {
      "gap": "没有产品做到「离线优先 + AI + 协作」三合一",
      "evidence": "Notion/Airtable 强在线；AppFlowy 离线强但 AI 弱；飞书 AI 强但绑定云",
      "severity": "high"
    }
  ],
  "opportunities": [
    {
      "title": "离线优先的 AI 协作工作空间",
      "inspiration": "Notion 的灵活性 + Obsidian 的离线 + Cursor 的 AI 体验",
      "target_users": ["隐私敏感的企业", "网络不稳地区（东南亚 / 非洲）", "学生 / 个人知识管理"],
      "differentiators": ["本地存储 + CRDT 协作", "AI 跑在本地小模型（Ollama 集成）", "Markdown 原生"],
      "validation": ["Reddit r/Notion 抱怨 'offline broken' 帖子数 / 月", "G2 上 'offline-first' 关键词搜索量", "GitHub AppFlowy Star 增速"],
      "moat": "技术：CRDT + 本地 LLM 集成；网络：先发占位"
    }
  ],
  "executive_summary": "在线协作赛道已极度拥挤，前 10 名集中在'All-in-one 工作空间'和'垂直专家'两类。最大的市场空白是**离线优先 + AI + 协作**三角——所有头部都至少缺一角。"
}
```

## Opportunities 生成 prompt（Step 3 调用）

```
你是一个产品策略师。基于下面的竞品矩阵和市场空白，生成 5-8 个**颠覆性产品机会**：

【输入】
- 竞品：[list]
- scores：[matrix]
- gaps：[list]
- 技术趋势：2026 年公认重要的技术拐点（端侧 LLM / 多模态 / Agent 框架 / WebAssembly / CRDT / ...）

【每个 opportunity 必须含】
1. title — 一句话产品名（≤ 10 字）
2. inspiration — 灵感来源（哪些竞品的哪些能力组合 / 跨界迁移）
3. target_users — 3 类具体用户（不要写"中小企业"这种空话）
4. differentiators — 2-3 个**对手做不到**的差异点
5. validation — 3 个**可量化**的验证信号（搜索词、Star 数、Reddit 抱怨数…）
6. moat — 护城河（技术 / 网络效应 / 品牌 / 数据 — 不要写"先发优势"这种废话）

【反模式】
- ❌ "加强 AI 能力" / "优化用户体验" / "打造一站式平台" — 正确的废话
- ❌ 跟所有竞品都长得一样的产品
- ❌ 没法落地的宏大愿景（"颠覆整个 SaaS 行业"）

【输出】
JSON 数组，按「颠覆性指数」降序（颠覆性 = 用户痛点强度 × 技术拐点匹配度 × 竞品空白度）。
```

## 自检清单（Step 3 完成时核对）

- [ ] 每个竞品 13 个字段齐全
- [ ] 每个非空字段的 quote 能在 02-raw 里 grep 到（逐字）
- [ ] strengths / weaknesses 至少各有 1 条带 URL 证据（没有证据就明说，不硬凑）
- [ ] core_features ≥ 12 条且来自 docs 证据页（非 pricing 套餐清单）
- [ ] **feature_catalog 每家都写**（§5.2.1 矩阵唯一数据源；category 统一分类、同能力同名、source 逐条锚定）
- [ ] differentiators 为 dict 结构 `{point, quote, source_url}`，source_url 锚定 docs/features 具体子页
- [ ] tech_signals / feature_catalog 的 source 无域名根、无 /pricing 锚点（G7）
- [ ] 定价带 `pricing_verified` / `pricing_source` / `pricing_scraped_at`
- [ ] 6 维评分每个竞品都有，且 1-10 范围内，且能说出打分依据（功能数/集成数/定价结构）
- [ ] opportunities ≥ 5 条（由 LLM 基于证据生成，不是脚本模板）
- [ ] opportunities 每条都有 validation 量化信号
- [ ] executive_summary ≤ 200 字
- [ ] **全程零伪造**：没有任何字段来自硬编码/模板/训练记忆
