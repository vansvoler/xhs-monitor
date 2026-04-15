---
name: xhs-monitor
description: >-
  小红书品牌舆情监控：通过 playwright-cli 抓取小红书笔记和评论，进行多维度舆情分析
  （情感分析、热词提取、KOL 识别、话题聚类、竞品对比、时间趋势），生成结构化报告并发布到飞书文档。
  触发场景：(1) 监控某品牌/关键词的小红书舆情 (2) 抓取小红书笔记和评论
  (3) 分析小红书上的品牌口碑/用户反馈 (4) 生成小红书舆情分析报告
---

# 小红书品牌舆情监控

## 工作流

```
确认抓取范围 → attach 浏览器 → 搜索关键词 → 滚动提取笔记列表 → 逐条抓取详情+评论 → 数据分析 → 飞书文档输出
```

## Phase 0: 确认抓取范围

**开始抓取前，必须先向用户确认以下参数**：

| 参数 | 说明 | 示例 |
|------|------|------|
| 关键词 | 搜索词 | "唯寻" |
| 笔记数量 | 滚动抓取的笔记条数，同时也是详情抓取的条数 | 20 / 50 / 100 |
| 排序方式 | 搜索结果排序 | 综合（默认）/ 最新 / 最热 |
| 竞品（可选） | 竞品品牌名，用于对比分析 | "菠萝国际,翰林" |

排序方式对应的 URL 参数：

| 排序 | URL 参数 | 适用场景 |
|------|----------|----------|
| 综合（默认） | `sort=general` 或不传 | 全面了解品牌舆情 |
| 最新 | `sort=time_descending` | 追踪最近动态、事件响应 |
| 最热 | `sort=popularity_descending` | 发现高影响力内容 |

搜索 URL 拼接示例：
`https://www.xiaohongshu.com/search_result?keyword={关键词}&type=1&sort=time_descending`

> 用户可能只说"帮我看看 XX 的舆情"，此时主动询问：
> "需要抓多大范围？比如**20 条快速摘要**（约 1 分钟），还是**50 条深度分析**（约 3 分钟）？排序用**综合**、**最新**还是**最热**？"

确认后记为 `TARGET`（默认 50）。**列表抓取和详情抓取使用同一个数量——抓到多少条列表，就全部抓详情+评论，不做截断。**

## Phase 1: 浏览器准备

**唯一推荐方式：attach 到用户已登录的 Chrome**。
小红书对海外 IP / 机房 IP / 自动化浏览器有严格风控（300012 错误），attach 真实浏览器可规避指纹检测和 IP 限制。

```bash
# 用户需先开启 Chrome 远程调试：chrome://flags/#enable-debugging
playwright-cli -s=xhs attach --cdp=chrome
```

> **风控须知**：
> - 若用户使用 VPN/代理（如 Shadowrocket TUN 模式），小红书域名必须走国内出口
> - 使用 `attach` 模式无需额外处理代理，因为复用用户已有的浏览器网络环境

## Phase 2: 搜索与抓取

### 搜索笔记

```bash
playwright-cli -s=xhs tab-new "https://www.xiaohongshu.com/search_result?keyword={URL编码关键词}&type=1"
playwright-cli -s=xhs snapshot
```

### 滚动加载 + 提取笔记列表

> **关键**：小红书搜索结果使用**虚拟滚动**，DOM 中始终只保留约 24 个 `section.note-item`，
> 滚动时旧卡片被替换而非追加。必须在滚动过程中逐批提取，按 `data-index` 去重。

滚动加载（只有 Playwright 原生 mouse.wheel 能触发 IntersectionObserver）：

```bash
playwright-cli -s=xhs run-code "async page => { await page.mouse.wheel(0, 5000); await page.waitForTimeout(2000); }"
```

每次滚动后立即提取当前可见卡片：

```bash
playwright-cli -s=xhs --raw eval "JSON.stringify(
  [...document.querySelectorAll('section.note-item')].map(n => ({
    idx: parseInt(n.dataset.index) || 0,
    title: (n.querySelector('.footer .title span') || {}).textContent?.trim() || '',
    author: (n.querySelector('.card-bottom-wrapper .name') || {}).textContent?.trim() || '',
    date: (n.querySelector('.card-bottom-wrapper .time') || {}).textContent?.trim() || '',
    likes: (n.querySelector('.card-bottom-wrapper .count') || {}).textContent?.trim() || '0',
    url: 'https://www.xiaohongshu.com/explore/' +
      ((n.querySelector('a[href*=explore]') || {}).getAttribute?.('href') || '').split('/').pop(),
    cover_url: (n.querySelector('a.cover') || {}).href || ''
  })).filter(n => n.url.length > 50)
)"
```

循环「滚动 → 提取 → 按 idx 去重合并」直到达到 `TARGET` 或连续 3 次无新内容。

同时从 `cover_url` 获取带 `xsec_token` 的完整链接（详情页必需），将 `/search_result/` 替换为 `/explore/`。

### 逐条抓取详情 + 评论

使用带 `xsec_token` 的 URL 跳转（裸 `/explore/` 链接会 404）：

```bash
playwright-cli -s=xhs goto "{带token的完整URL}"

# 提取正文和互动数据
playwright-cli -s=xhs --raw eval "JSON.stringify({
  title: document.querySelector('#detail-title, .title')?.textContent?.trim(),
  content: document.querySelector('#detail-desc, .desc')?.textContent?.trim(),
  likes: document.querySelector('.like-wrapper .count')?.textContent?.trim(),
  collects: document.querySelector('.collect-wrapper .count')?.textContent?.trim(),
  comment_count: document.querySelector('.chat-wrapper .count')?.textContent?.trim(),
  author: document.querySelector('.username')?.textContent?.trim(),
  date: document.querySelector('.date, .bottom-container .date')?.textContent?.trim()
})"

# 提取评论（按 user+content 去重，避免主评和回复重复）
playwright-cli -s=xhs --raw eval "JSON.stringify(
  [...document.querySelectorAll('.comment-item, .parent-comment')].map(c => ({
    user: (c.querySelector('.name, .author-name') || {}).textContent?.trim() || '',
    content: (c.querySelector('.content, .note-text') || {}).textContent?.trim() || '',
    likes: (c.querySelector('.like .count, .like-wrapper .count') || {}).textContent?.trim() || '0'
  }))
)"
```

**列表中抓到的笔记全部抓取详情，不做截断。** 每条间隔 3 秒，避免触发风控。超过 50 条时分批执行。

## Phase 3: 数据分析

```bash
# 标准 Markdown 报告
python3 ~/.claude/skills/xhs-monitor/scripts/analyze.py xhs_data.json \
  --format markdown --output xhs_report.md --brand "品牌名" \
  --competitors "竞品A,竞品B"

# 飞书优化格式（推荐，直接用于 Phase 4）
python3 ~/.claude/skills/xhs-monitor/scripts/analyze.py xhs_data.json \
  --format lark-markdown --output xhs_report_lark.md --brand "品牌名"
```

六大分析维度详见 [references/analysis-dims.md](references/analysis-dims.md)。

## Phase 4: 飞书文档输出

使用 `lark-doc` skill 将报告发布为飞书文档：

```bash
# 读取 lark-markdown 格式的报告内容，创建飞书文档
REPORT=$(cat xhs_report_lark.md)
lark-cli docs +create --as user --title "品牌名 小红书舆情分析报告" --markdown "$REPORT"
```

创建成功后返回 `doc_url`，可直接在飞书中查看。

## 反爬要点

- **必须用 `attach --cdp=chrome`** 接入用户真实浏览器，避免指纹检测
- 笔记详情页必须携带 `xsec_token`，从搜索结果页的 `a.cover` 链接中获取
- 每次请求间隔 3 秒
- 单次抓取 ≤50 条笔记，超量分批执行
- 遇到验证码时暂停，提示用户手动处理
