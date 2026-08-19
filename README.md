<div align="center">

<img src="assets/youzi-logo.svg?v=3" width="110" alt="youzi logo"/>

# 🍊 youzi · 竞品颠覆性分析

**`/youzi <主题>` → 自动挖顶尖竞品 → 深度分析 → 颠覆性机会清单 → 精美 HTML 报告**

![License](https://img.shields.io/badge/License-MIT-orange.svg)
![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen.svg)
![Python](https://img.shields.io/badge/Python-stdlib_only-3776AB.svg)
![Claude Code](https://img.shields.io/badge/Claude%20Code-Skill-blueviolet.svg)

[🚀 快速入门](快速入门.md) · [🌱 小白入门](小白入门.md) · [📘 使用进阶](使用进阶.md) · [⚙️ SKILL](SKILL.md)

</div>

---

## ⚡ 30 秒跑通

```bash
# 1. 装（一次性）
mkdir -p ~/.claude/skills
ln -s /path/to/youzi ~/.claude/skills/youzi

# 2. 跑 —— 在 Claude Code 里输入：
/youzi 在线协作工具
```

报告自动生成在 `~/youzi-out/<主题>-<日期>/report.html` 🎉

---

## 🔄 它是怎么工作的

```
        /youzi <主题>
             │
             ▼
  🕷️  爬取 · 3 爬虫并行 + 智能合并去重
      (firecrawl / Crawl4AI / Playwright)
             │
             ▼
  🧠  分析 · 8 大模块
      市场细分 · 竞品画像 · 雷达 · 功能全集 · 矩阵 · 空白 · 机会 · 定价
             │
             ▼
  💡  输出 · 颠覆性机会清单
             │
             ▼
  📊  精美 HTML 报告（~/youzi-out/）
```

---

## ✨ 核心能力

| | 能力 | 说明 |
|---|---|---|
| 🕷️ | **3 爬虫并行** | firecrawl + Crawl4AI + Playwright，智能合并去重 |
| 🧠 | **8 大模块分析** | 市场细分 / 竞品画像 / 雷达 / 功能全集 / 矩阵 / 空白 / 机会 / 定价 |
| 📊 | **精美 HTML 报告** | 单文件 472KB，CSS/JS/SVG 全 inline，离线可看 |
| 🌗 | **主题感知** | 浅色 / 深色 / 跟随系统 |
| 📱 | **响应式** | 桌面 / 平板 / 手机 |
| 🛠️ | **零依赖** | 纯 Python 标准库渲染（render.py 49K） |
| 🔍 | **真实数据** | firecrawl 抓 251KB 原始数据 + 6 张截图 |

---

## 📁 项目结构

```
youzi/
├── 🚀 快速入门.md               # 5 分钟跑通
├── 🌱 小白入门.md               # 完全新手版
├── 📘 使用进阶.md               # 进阶手册
├── 👋 README.md                 # 你正在看
├── ⭐ SKILL.md                  # Claude 触发入口
├── 🔧 render.py                 # 零依赖 HTML 渲染器
├── 🎨 templates/report.html     # HTML 模板
├── 🕸️ adapters/                 # 3 个爬虫工具（并行 + 合并）
│   ├── firecrawl_scraper.py
│   ├── crawl4ai_scraper.py
│   ├── playwright_scraper.py
│   └── __init__.py              #   scrape_smart() 统一入口
├── 📚 references/               # 详细文档
│   ├── crawl-strategy.md
│   ├── analysis-framework.md
│   └── scraping-tools.md
└── 📦 examples/                 # 示例
    ├── online-collab-demo.json
    └── sample-report.html
```

---

## 📚 文档导航

| 📄 文档 | 👤 适合谁 | ⏱️ 时间 |
|---|---|---|
| [🚀 快速入门.md](快速入门.md) | 🟢 想 5 分钟跑起来 | 2 分钟 |
| [🌱 小白入门.md](小白入门.md) | 🟢 完全新手 | 5 分钟 |
| [📘 使用进阶.md](使用进阶.md) | 🟡 进阶用户 | 15 分钟 |
| [⚙️ SKILL.md](SKILL.md) | 🔴 开发者（Claude 触发） | 5 分钟 |
| [references/crawl-strategy.md](references/crawl-strategy.md) | 🔴 爬虫策略 | 10 分钟 |
| [references/analysis-framework.md](references/analysis-framework.md) | 🔴 分析框架 | 10 分钟 |
| [references/scraping-tools.md](references/scraping-tools.md) | 🔴 工具横评 | 5 分钟 |
| [examples/sample-report.html](examples/sample-report.html) | 🌈 所有人 · 看效果 | — |

---

## 🛠️ 站在巨人肩膀上

| 🔧 来源 | 💡 借鉴了什么 |
|---|---|
| [firecrawl](https://github.com/mcgrapeng/firecrawl) | 96% 网页覆盖爬虫 |
| [Agent-Reach](https://github.com/mcgrapeng/Agent-Reach) | 中文社交 / YouTube / Exa 语义 |
| `graphify` skill | 结构化抽取思路 |
| `ai-radar` skill | Skill 架构范式 |
| `dataviz` skill | 配色公式 |
| `artifact-design` | HTML 美学 |

---

## 📄 License

MIT

---

<div align="center">

**Made with 🔥 by Claude Code** —— 愿你找到下一个颠覆性机会 🍊

</div>
