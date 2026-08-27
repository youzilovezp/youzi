<div align="center">

<img src="assets/youzi-logo.svg?v=3" width="110" alt="youzi logo"/>

# 🍊 youzi · 竞品情报收集工具

**`/youzi <主题>` → 自动挖出赛道顶尖竞品 → 逐一深度拆解 → 精美 HTML 报告 + 颠覆性机会清单**（支持 Claude Code / opencode / Codex / EasyCode 四平台）

![License](https://img.shields.io/badge/License-MIT-orange.svg)
![Python](https://img.shields.io/badge/Python-3.8%2B-3776AB.svg)
![Claude Code](https://img.shields.io/badge/Claude%20Code-Skill-blueviolet.svg)
![opencode](https://img.shields.io/badge/opencode-Skill-green.svg) ![codex](https://img.shields.io/badge/Codex-Skill-blue.svg) ![easycode](https://img.shields.io/badge/EasyCode-Skill-orange.svg)

[📦 安装说明](安装说明.md) · [📖 使用手册](使用手册.md) · [⚙️ SKILL](SKILL.md)

</div>

---

## 🚀 30 秒上手

> 还没装？先看 [📦 安装说明](安装说明.md)（一条命令，约 3 分钟），装完再回来。

```bash
# 1. 一条命令装齐：skill 自动探测四平台 + 自动装引擎依赖（pip 包 + chromium）
./install.sh install

# 2. 打开你的 AI 工具（Claude Code / opencode / Codex / EasyCode），输入：
/youzi 在线协作工具

# 3. 等 5-15 分钟 → ~/youzi-out/<主题>-<日期>/report.html 自动生成
```

报告是**单文件 HTML**（零外部依赖，可离线打开、邮件转发、打印），章节结构见下方「报告 9 大章节」。

> 💡 爬取用 **5 引擎白名单**（playwright / trafilatura / newspaper3k / jina / firecrawl），按页面类型智能路由；不装引擎也能跑（降级到 WebSearch），推荐至少装 playwright + trafilatura，详见 [📦 安装说明](安装说明.md)。

---

## 🤔 这是什么？

**一个 AI 编码工具的竞品调研 skill**（Claude Code / opencode / Codex / EasyCode 四平台）：你说一个市场主题，AI 自动把该赛道的竞品挖出来、逐个爬官网取证、结构化拆解，最后产出一份**每条结论都带 URL 证据**的单文件 HTML 报告，并给出超越竞品的颠覆性产品机会清单。

```
你输入：/youzi 在线协作工具
        │
        ▼
AI 做：
  🔍  发现竞品 — 6+ 角度并行搜索（头部/份额/替代品/G2 评分/开源/编辑），取 Top N
  🕷️  统一取证 — fetch.py 按 5 引擎白名单智能路由爬官网/定价/docs
                 （定价 ≥2 独立引擎交叉验证，不达标自动换引擎重爬）
  🧠  结构化分析 — 13 字段逐条提取，每条带 {值, source_url, quote} 证据三元组
  📊  渲染报告 — Jinja2 单文件 HTML（主题感知 / 响应式 / [N] 引用跳转）
  ✅  双门禁 — render.py 自检 + verify.py G1-G7 证据核验，不过 = 不交付
        │
        ▼
你拿到：
  ~/youzi-out/<主题>-<日期>/
  ├── 01-competitors-list.json  # 竞品候选清单
  ├── 02-raw/                   # 每竞品原文 + 每引擎独立证据（缓存可复用）
  ├── claims-manifest.json      # 取证台账（URL × 引擎 × 哈希 × 时间）
  ├── 03-analysis.json          # 13 字段结构化数据（改完可重渲染）
  └── report.html               # ⭐ 最终报告（单文件，零外部依赖，自检上限 1.5MB）
```

---

## ✨ 核心能力

| | 能力 | 说明 |
| --- | --- | --- |
| 🔍 | **多角度竞品发现** | 6+ 搜索视角合并去重；`--include` / `--exclude` 精确控制名单 |
| 🕷️ | **5 引擎智能路由** | playwright（JS 页）/ trafilatura（静态正文）/ newspaper3k（文章）/ jina（免 key）/ firecrawl（商业，检测到 key 自动启用）；按页面类型自动组合，引擎表现自学习 |
| 💰 | **定价交叉验证** | ≥2 独立引擎看到相同价格才标「已验证」；单引擎自动显示 ⚠ 未验证徽章 |
| 🧬 | **证据三元组** | 每个字段 = 值 + source_url + 原文逐字 quote；verify.py 门禁逐条回查，抓不到就诚实标「未验证」，**绝不伪造** |
| 🧠 | **13 字段深度拆解** | 定位 / 阶段 / 目标用户 / 12+ 核心功能 / 定价 / 优劣势 / 差异化 / 技术栈信号 / 6 维评分（功能·体验·定价·集成·AI·势头） |
| 🎯 | **颠覆性机会清单** | 市场空白 ≥3 条 + 机会方向 5-8 个（每个含灵感来源 / 目标用户 / 差异化点 / 验证方式） |
| 📄 | **单文件精美报告** | 浅色 / 深色 / 跟随系统，响应式，浮动 TOC，Ctrl+P 打印友好，可离线 / 邮件分发 |
| 🔌 | **四平台集成** | 同一套 SKILL.md；`./install.sh` 自动探测装到所有已装的 AI 工具 |
| 🔁 | **可重入** | `02-raw/` 是缓存，重跑跳过已抓；改 `03-analysis.json` 后 30 秒重渲染，不必重爬 |

---

## 📑 报告 9 大章节

| § | 章节 | 实际内容 |
| --- | --- | --- |
| 01 | 背景与目标 | 分析背景 + 目标 + 关键数字信息条（竞品数/功能数/机会数/来源数）+ 分析方法说明 |
| 02 | 结论与建议 | 专业术语速查 + 启发点 / 机会点（按竞品分组）+ Top 3 颠覆性机会（按 disrupt_score 排序） |
| 03 | 产品定位分析 | 宣传口号卡片 + 用户及市场定位表 + 目标用户重叠矩阵 |
| 04 | 定价分析 | 每家定价卡（月付/年付分栏、免费档、定制档、年付省%）+ 证据链（✓ 已验证 N 引擎 / ⚠ 未验证·单引擎） |
| 05 | 产品设计分析 | 4 端支持表 + 功能对比矩阵（独家功能标注）+ 技术栈信号聚类 + 各家技术栈明细（带原文引用） |
| 06 | 产品数据分析 | 市场细分全景 + 产品迭代信号 + 发布密度对比 + 近期发布时间线 |
| 07 | 用户反馈分析 | 每家正 / 负反馈卡，每条带来源链接，没抓到就明说「不编造」 |
| 08 | 其他竞品资料库 | 深度分析之外的市场全景玩家，按类别分组（无数据时整节隐藏） |
| ★ | 来源与参考资料 | 全部带 URL 的证据清单，正文 `[N]` 角标可跳转 |

---

## 📚 文档导航

| 你想了解什么 | 看这里 |
| --- | --- |
| 🆕 第一次安装（四平台） | [📦 安装说明.md](安装说明.md) |
| 🚀 装好后怎么用 / 参数 / FAQ | [📖 使用手册.md](使用手册.md) |
| 🤖 AI 执行的完整工作流 | [⚙️ SKILL.md](SKILL.md) |
| 🕷️ 爬取策略详解 | [references/crawl-strategy.md](references/crawl-strategy.md) |
| 🧠 13 字段提取框架 | [references/analysis-framework.md](references/analysis-framework.md) |
| ❌ 出错了 | [📖 使用手册 § 6](使用手册.md) |

---

## 🛠️ 技术栈一览

| | 技术 | 用途 |
| --- | --- | --- |
| 🐍 | Python 3.8+ | 取证 / 验证 / 渲染脚本 |
| 🎨 | Jinja2 | HTML 报告渲染（唯一必装第三方包） |
| 🕷️ | playwright · trafilatura · newspaper3k · jina · firecrawl | 5 引擎白名单（推荐装前两个） |
| 🤖 | Claude Code / opencode / Codex / EasyCode | 任一 AI 编码工具承载执行 |
| 📦 | MIT License | 自由使用 |

---

## 📄 License

MIT
