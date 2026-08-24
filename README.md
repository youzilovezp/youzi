<div align="center">

<img src="assets/youzi-logo.svg?v=3" width="110" alt="youzi logo"/>

# 🍊 youzi · 竞品颠覆性分析

**`/youzi <主题>` → 自动挖顶尖竞品 → 深度分析 → 颠覆性机会清单 → 精美 HTML 报告**

![License](https://img.shields.io/badge/License-MIT-orange.svg)
![Dependencies](https://img.shields.io/badge/dependencies-0-brightgreen.svg)
![Python](https://img.shields.io/badge/Python-stdlib_only-3776AB.svg)
![Claude Code](https://img.shields.io/badge/Claude%20Code-Skill-blueviolet.svg)

[🚀 快速入门](快速入门.md) · [📦 安装说明](安装说明.md) · [🌱 小白入门](小白入门.md) · [📘 使用进阶](使用进阶.md) · [⚙️ SKILL](SKILL.md)

</div>

---

## ⚡ 它是什么

`/youzi` 是一个 **Claude Code 竞品分析 skill**。输入一个市场主题,自动产出含**带 URL 出处的 213 条来源**、**117 项竞品功能**、**6 维度雷达对比**、**9 章完整结构**的精美 HTML 报告。

```
/youzi <主题>
       │
       ▼
🕷️ 爬取 → 7 爬虫并行 (firecrawl + Crawl4AI + trafilatura + newspaper3k + readability + markdownify + playwright)
       │
       ▼
🧠 分析 → 13 字段提取 + SWOT + 6 维评分 + 市场空白 + 颠覆机会
       │
       ▼
📊 输出 → 单文件 HTML 报告(主题感知 + 响应式 + 浮动 TOC + 阅读进度条 + 全 [N] 引用)
```

---

## ✨ 核心能力

| | 能力 | 说明 |
|---|---|---|
| 🕷️ | **7 爬虫并行** | 开源 + 商业混合 · 智能合并去重 |
| 🧠 | **13 字段结构化** | strengths/weaknesses/tech_signals 等 |
| 📑 | **9 章节完整** | 背景 / 结论 / 定位 / 商业 / 设计 / 数据 / 反馈 / §8 / Sources |
| 🔗 | **全板块可追溯** | 177 个 `[N]` 引用角标 · 213 条带 URL 来源 |
| 📊 | **117 项功能全集** | 每家竞品 18-22 个功能 · 39 类别 |
| 🎯 | **SWOT 全图** | 6 深度 + 15 §8 其他竞品 = 21 张 SWOT 卡 |
| 📈 | **6 维评分对比** | 评分矩阵 + 奖牌 + 综合分 + 领先/中坚/跟随分级 |
| 🌗 | **主题感知** | 浅色 / 深色 / 跟随系统 |
| 📱 | **响应式** | 桌面 / 平板 / 手机 |
| 🚀 | **零依赖** | 纯 Python 标准库渲染(无 jinja2 / npm) |

---

## 📁 项目结构

```
youzi/
├── 📦 安装说明.md           # 安装指南(系统要求 + 7爬虫 + 验证 + 卸载)
├── 🚀 快速入门.md           # 5 分钟跑通(最少命令)
├── 🌱 小白入门.md           # 完全新手版(详细步骤 + GUI 操作)
├── 📘 使用进阶.md           # 使用手册 + CLI flags + 进阶技巧 + FAQ
├── 👋 README.md             # 你正在看(项目总览)
├── ⭐ SKILL.md              # Claude 触发入口(给 Claude 看)
├── 🔧 render.py             # 零依赖 HTML 渲染器 + normalize() + 全局引用
├── 🎨 templates/report.html # HTML 模板(飞书 9 章结构)
├── 🕸️ adapters/             # 7 个爬虫 adapter + 注册表 + 并行合并
│   ├── firecrawl_scraper.py    ⭐ 商业 96% 覆盖
│   ├── crawl4ai_scraper.py     ⭐ 开源 LLM-ready
│   ├── trafilatura_scraper.py     学术级正文抽取
│   ├── newspaper3k_scraper.py    老牌文章抽取
│   ├── readability_scraper.py     Mozilla Readability
│   ├── markdownify_scraper.py     HTML→MD fallback
│   └── playwright_scraper.py    ⭐ 浏览器自动化
├── 🛠️ scripts/              # 工具脚本
│   ├── run_youzi.py           一站式 CLI
│   ├── crawl_summarize.py     爬虫总结工具
│   ├── build_whatsapp_demo.py  数据生成器
│   └── render_smoke.py        smoke test
├── 📚 references/           # 技术细节参考
│   ├── crawl-strategy.md
│   ├── analysis-framework.md
│   └── scraping-tools.md
└── 📦 examples/             # 示例数据
    ├── online-collab-demo.json
    ├── whatsapp-advertising-demo.json
    └── sample-report.html
```

---

## 📚 文档导航

| 📄 文档 | 👤 适合谁 | ⏱️ 时间 |
|---|---|---|
| [🚀 快速入门.md](快速入门.md) | 🟢 想 5 分钟跑起来 · 老用户 · 只想看命令 | 2 分钟 |
| [📦 安装说明.md](安装说明.md) | 🟢 第一次安装 · 想知道装哪些依赖 | 5 分钟 |
| [🌱 小白入门.md](小白入门.md) | 🟢 完全新手 · 不懂 git/Python/终端 | 10 分钟 |
| [📘 使用进阶.md](使用进阶.md) | 🟡 进阶用户 · CLI flags · 配置 · 排错 | 15 分钟 |
| [⚙️ SKILL.md](SKILL.md) | 🔴 Claude 触发入口(不是给人读的) | 5 分钟 |
| [references/crawl-strategy.md](references/crawl-strategy.md) | 🔴 爬虫策略详解 | 10 分钟 |
| [references/analysis-framework.md](references/analysis-framework.md) | 🔴 13 字段提取框架 | 10 分钟 |
| [examples/sample-report.html](examples/sample-report.html) | 🌈 所有人 · 看效果 | — |

**怎么选**:
- 装好就上手 → [快速入门.md](快速入门.md)
- 装都还没装 → [安装说明.md](安装说明.md)
- 啥都不会 → [小白入门.md](小白入门.md)
- 已经用过想深入 → [使用进阶.md](使用进阶.md)

---

## 🛠️ 技术栈 & 借鉴

| 🔧 来源 | 💡 借鉴了什么 |
|---|---|
| [firecrawl](https://github.com/mcgrapeng/firecrawl) | 96% 网页覆盖爬虫 |
| [Crawl4AI](https://github.com/unclecode/crawl4ai) | 开源 LLM-ready 爬虫 |
| [trafilatura](https://github.com/adbar/trafilatura) | 学术级正文抽取 |
| [newspaper3k](https://github.com/codelucas/newspaper) | 老牌文章抽取 |
| [readability-lxml](https://github.com/buriy/python-readability) | Mozilla Readability 移植 |
| [markdownify](https://github.com/matthewwithanm/python-markdownify) | HTML→MD |
| [playwright](https://github.com/microsoft/playwright) | 浏览器自动化 |
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
