# xhs-monitor

**小红书品牌舆情监控 Claude Code Skill**

通过 playwright-cli 接入真实浏览器，抓取小红书笔记和评论，自动完成六维舆情分析，一键发布飞书文档。

---

## 能做什么

**单品牌监控**：输入关键词，输出含情感分布、热词排名、KOL 影响力、话题聚类、风险预警的完整报告

**竞品对比**：同时抓取两个品牌，量化分析内容策略差异

> 实测：22 条笔记 + 175 条评论，从抓取到飞书文档发布约 5 分钟

示例报告截图（唯寻 vs 渊学通对比）：

| 维度 | 渊学通 | 唯寻 |
|------|--------|------|
| 样本量 | 22 条笔记 / 62 条评论 | 22 条笔记 / 175 条评论 |
| 篇均评论 | 2.8 条 | 8.0 条 |
| Top KOL 互动 | 81 | 2,405 |
| 内容风格 | 课程导向 | 员工 IP / 娱乐化 |

---

## 前置条件

| 依赖 | 说明 |
|------|------|
| [Claude Code](https://claude.ai/code) | 运行 Skill 的宿主环境 |
| [playwright-cli](https://github.com/vansvoler/playwright-cli) | 浏览器自动化，支持 attach 真实 Chrome |
| [lark-cli](https://github.com/vansvoler/lark-cli) | 飞书文档发布（可选，跳过则只生成 Markdown） |
| Chrome 浏览器 | 已登录小红书账号 |
| Python 3.9+ | 运行分析脚本 |

---

## 安装

```bash
# 1. 克隆到 Claude Code Skills 目录
git clone https://github.com/vansvoler/xhs-monitor ~/.claude/skills/xhs-monitor

# 2. 安装 Python 依赖（无第三方库，仅标准库）
# analyze.py 只依赖 Python 标准库，无需额外安装
```

---

## 使用方法

在 Claude Code 中直接描述任务即可触发 Skill：

```
帮我监控一下「唯寻」的小红书舆情，最新的 20 条笔记
```

```
抓取「渊学通」和「唯寻」各 20 条，做个竞品对比
```

Claude 会自动确认以下参数后开始执行：

| 参数 | 说明 | 示例 |
|------|------|------|
| 关键词 | 搜索词 | 唯寻 |
| 笔记数量 | 抓取条数 | 20 / 50 / 100 |
| 排序方式 | 综合 / 最新 / 最热 | 最新 |
| 竞品（可选） | 对比品牌 | 渊学通,菠萝国际 |

### 手动执行分析脚本

```bash
# 生成标准 Markdown 报告
python3 ~/.claude/skills/xhs-monitor/scripts/analyze.py xhs_data.json \
  --format markdown --output report.md --brand "唯寻"

# 生成飞书优化格式
python3 ~/.claude/skills/xhs-monitor/scripts/analyze.py xhs_data.json \
  --format lark-markdown --output report_lark.md --brand "唯寻" \
  --competitors "渊学通,菠萝国际"
```

---

## 工作流

```
Phase 0  确认参数（关键词 / 数量 / 排序 / 竞品）
    ↓
Phase 1  attach 到已登录 Chrome（规避风控）
    ↓
Phase 2  搜索 → 虚拟滚动抓列表 → 逐条抓详情 + 评论
    ↓
Phase 3  analyze.py 六维分析
    ↓
Phase 4  lark-cli 发布飞书文档
```

详细操作指南见 [SKILL.md](SKILL.md)。

---

## 六维分析说明

| 维度 | 内容 |
|------|------|
| 情感分析 | 笔记 + 评论正面/负面/中性分布，含典型案例摘要 |
| 热词提取 | Top 20 高频词，滑动窗口分词 + 子串去重 |
| KOL 影响力 | 按互动量（点赞+收藏+评论）排名，含代表作 |
| 话题聚类 | 产品体验 / 课程教学 / 升学录取 / 价格讨论 / 对比评测 |
| 竞品声量 | 竞品被提及次数、情感倾向、常见关联词 |
| 时间趋势 | 按日期聚合发布量和情感变化 |

---

## 反爬说明

小红书对机房 IP 和自动化浏览器有严格风控（错误码 300012）。本 Skill 通过 `playwright-cli attach --cdp=chrome` 接入用户真实浏览器，复用已有登录态和网络环境，无需处理额外的代理或指纹问题。

---

## 文件结构

```
xhs-monitor/
├── SKILL.md                    # Claude Code Skill 主指南
├── scripts/
│   └── analyze.py              # 六维分析脚本
└── references/
    └── analysis-dims.md        # 分析维度详细说明
```

---

## License

MIT
