# Analysis Framework · 13 字段提取

每个竞品按下面 13 个字段提取，**所有 strengths / weaknesses / scores 必须有 raw 文件里的具体证据**（带 URL + 引文片段）。没有证据的字段写「无足够证据」。

## 单竞品 schema

```json
{
  "name": "Notion",
  "url": "https://notion.so",
  "tagline": "工作空间 · 一个工具搞定一切",
  "founded": 2016,
  "stage": "成熟期",
  "target_users": ["知识工作者", "中小团队", "学生"],
  "core_features": ["块编辑器", "数据库", "模板市场", "AI 写作", "跨平台同步", "API"],
  "pricing": "免费 + Plus $10/月 + Business $15/月",
  "strengths": [
    {
      "point": "块编辑器极灵活，几乎可以搭建任何工作流",
      "evidence": "官网主页：'Turn any thought into a beautiful page. Notion docs adapt to how you think.' (https://notion.so)",
      "score": 9
    }
  ],
  "weaknesses": [
    {
      "point": "离线能力弱，网速慢时体验差",
      "evidence": "G2 评价 Top 5 负面关键词：'slow offline' (https://g2.com/products/notion/reviews)",
      "score": 3
    }
  ],
  "differentiators": [
    "块编辑器 + 数据库二合一（vs. Confluence 只文档 / Airtable 只表格）",
    "AI 直接写满整篇文档（vs. 多数产品只做补全）"
  ],
  "tech_signals": [
    "React + 自研同步引擎（来自 engineering blog）",
    "Postgres + 自研增量同步（来自 changelog 2024）"
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
- [ ] strengths / weaknesses 至少各有 1 条带 URL 证据
- [ ] 6 维评分每个竞品都有，且 1-10 范围内
- [ ] opportunities ≥ 5 条
- [ ] opportunities 每条都有 validation 量化信号
- [ ] executive_summary ≤ 200 字
