#!/usr/bin/env python3
from __future__ import annotations

"""小红书舆情数据分析脚本

用法：
  python3 analyze.py xhs_data.json --output report.json
  python3 analyze.py xhs_data.json --format markdown --output report.md
  python3 analyze.py xhs_data.json --format lark-markdown --output report.md
  python3 analyze.py xhs_data.json --competitors "品牌A,品牌B"
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


# ============================================================
# 数值解析
# ============================================================

def safe_int(val: str | int | None) -> int:
    """将各种格式的数字文本转为 int（支持 '1.2万' 等中文缩写）"""
    if val is None:
        return 0
    if isinstance(val, (int, float)):
        return int(val)
    val = str(val).strip().replace(",", "")
    if "万" in val:
        return int(float(val.replace("万", "")) * 10000)
    if "亿" in val:
        return int(float(val.replace("亿", "")) * 100000000)
    try:
        return int(float(val))
    except ValueError:
        return 0


# ============================================================
# 分词与停用词
# ============================================================

STOP_WORDS = set(
    "的 了 是 在 我 有 和 就 不 人 都 一 一个 上 也 很 到 说 要 去 你 会 着 没有 看 好 "
    "自己 这 他 她 它 们 吗 嗯 啊 哦 呢 吧 哈 嘻 么 那 这个 那个 什么 怎么 还 "
    "可以 真的 感觉 觉得 而且 但是 因为 所以 如果 虽然 比较 非常 特别 已经 一直 "
    "知道 时候 现在 然后 或者 还是 这样 那样 应该 需要 没什么 不是 一下 一些".split()
)


def tokenize(text: str) -> list[str]:
    """中文分词：滑动窗口 + 最长子串去重

    策略：先提取 2-4 字窗口，再去除被更长词完全包含且频次相近的短词。
    单条文本内先做窗口提取，跨文本的子串去重在 analyze_keywords 中统一处理。
    """
    if not text:
        return []
    text = re.sub(r"[^\u4e00-\u9fff\w]", " ", text)
    tokens: list[str] = []
    for seg in text.split():
        if re.match(r"^[\u4e00-\u9fff]+$", seg):
            for n in (6, 5, 4, 3, 2):
                for i in range(len(seg) - n + 1):
                    w = seg[i : i + n]
                    if w not in STOP_WORDS:
                        tokens.append(w)
        elif len(seg) > 1:
            tokens.append(seg.lower())
    return tokens


def dedup_substrings(counter: Counter, threshold: float = 0.7) -> Counter:
    """去除被更长词包含的短词噪音

    当短词出现次数的 threshold 比例以上都伴随某个长词出现时，视为噪音移除。
    例："中国香港"(8) 会导致 "中国香"(8)、"国香港"(8)、"国香"(8) 被清理。
    """
    words = sorted(counter.keys(), key=len, reverse=True)
    to_remove: set[str] = set()
    for short in words:
        if short in to_remove:
            continue
        for long in words:
            if len(long) <= len(short) or long in to_remove:
                continue
            if short in long and counter[short] <= counter[long] / threshold:
                to_remove.add(short)
                break
    for w in to_remove:
        del counter[w]
    return counter


# ============================================================
# 情感分析（关键词规则）
# ============================================================

POS_WORDS = set(
    # 通用正面
    "推荐 好用 惊艳 回购 种草 喜欢 好看 舒服 值得 优秀 强烈 满意 完美 超赞 "
    "绝绝子 神器 宝藏 心动 无限回购 真香 闭眼入 必入 五星 靠谱 专业 权威 "
    # 教育行业正面
    "提分 逆袭 上岸 录取 offer 拿到 通过 进步 提升 优秀 名校 冲刺 "
    "负责 耐心 认真 用心 细致 氛围好 师资强 教学好 "
    "感谢 感恩 庆幸 选对 值了 没白花 稳了 圆梦".split()
)

NEG_WORDS = set(
    # 通用负面
    "踩雷 难用 失望 差评 退款 翻车 鸡肋 不好 后悔 垃圾 坑 难看 假 劣质 "
    "拉黑 别买 慎入 不推荐 一言难尽 智商税 太差 售后差 "
    # 教育行业负面
    "退费 跑路 虚假宣传 不负责 划水 坑钱 忽悠 套路 割韭菜 "
    "没效果 白花 浪费 骗 投诉 维权 过敏 泄题 作弊".split()
)


def sentiment(text: str) -> str:
    if not text:
        return "中性"
    pos = sum(1 for w in POS_WORDS if w in text)
    neg = sum(1 for w in NEG_WORDS if w in text)
    if pos > neg:
        return "正面"
    if neg > pos:
        return "负面"
    return "中性"


# ============================================================
# 六大分析模块
# ============================================================

def analyze_sentiment(notes: list[dict]) -> dict:
    """情感分析：笔记 + 评论

    笔记情感：优先使用 note["sentiment"]（由主模型预标注），缺失时回退关键词。
    评论情感：始终使用关键词规则（量大，无需 AI）。
    """
    results: dict[str, list] = {"正面": [], "负面": [], "中性": []}
    comment_sentiments: Counter = Counter()

    for note in notes:
        # 优先用 AI 预标注，回退关键词
        s = note.get("sentiment") or sentiment(
            note.get("content", "") + " " + note.get("title", "")
        )
        # 容错：统一为标准标签
        s = {"正面": "正面", "负面": "负面", "中性": "中性"}.get(s, "中性")
        results[s].append({
            "title": note.get("title", ""),
            "url": note.get("url", ""),
            "snippet": (note.get("content", "") or "")[:80],
        })
        for c in note.get("comments", []):
            cs = sentiment(c.get("content", ""))
            comment_sentiments[cs] += 1

    total = len(notes) or 1
    return {
        "note_distribution": {k: len(v) / total for k, v in results.items()},
        "note_counts": {k: len(v) for k, v in results.items()},
        "comment_distribution": dict(comment_sentiments),
        "top_positive": results["正面"][:3],
        "top_negative": results["负面"][:3],
    }


def analyze_keywords(notes: list[dict], top_n: int = 20) -> list[dict]:
    """热词提取（含子串去重）"""
    counter: Counter = Counter()
    for note in notes:
        text = note.get("title", "") + " " + note.get("content", "")
        counter.update(tokenize(text))
        for c in note.get("comments", []):
            counter.update(tokenize(c.get("content", "")))
    counter = dedup_substrings(counter)
    return [{"word": w, "count": c} for w, c in counter.most_common(top_n)]


def analyze_kol(notes: list[dict], top_n: int = 10) -> list[dict]:
    """KOL 识别"""
    authors: dict[str, dict] = defaultdict(
        lambda: {"note_count": 0, "total_engagement": 0, "best_note": None, "best_eng": 0}
    )
    for note in notes:
        author = note.get("author", "未知")
        eng = (safe_int(note.get("likes"))
               + safe_int(note.get("collects"))
               + safe_int(note.get("comment_count")))
        info = authors[author]
        info["note_count"] += 1
        info["total_engagement"] += eng
        if eng > info["best_eng"]:
            info["best_eng"] = eng
            info["best_note"] = note.get("title", "")

    ranked = sorted(authors.items(), key=lambda x: x[1]["total_engagement"], reverse=True)
    return [
        {"rank": i + 1, "author": a, **d}
        for i, (a, d) in enumerate(ranked[:top_n])
    ]


# 话题聚类关键词 — 通用 + 教育行业
TOPIC_KEYWORDS = {
    "产品体验": ["好用", "效果", "体验", "使用", "感受", "质感", "手感"],
    "课程与教学": ["课程", "上课", "教学", "老师", "师资", "讲得", "教得", "课堂", "班型", "授课"],
    "升学与录取": ["录取", "offer", "上岸", "申请", "大学", "名校", "院校", "升学", "留学", "本科", "硕士"],
    "价格讨论": ["价格", "性价比", "便宜", "贵", "优惠", "折扣", "划算", "活动", "学费", "费用", "预算"],
    "对比评测": ["对比", "测评", "vs", "PK", "区别", "哪个好", "比较", "选择"],
    "教程攻略": ["教程", "攻略", "方法", "技巧", "搭配", "怎么用", "步骤", "备考", "规划"],
    "售后服务": ["客服", "售后", "退货", "退款", "换货", "物流", "发货", "退费"],
}


def analyze_topics(notes: list[dict]) -> list[dict]:
    """话题聚类"""
    clusters: dict[str, list] = {t: [] for t in TOPIC_KEYWORDS}
    clusters["其他"] = []

    for note in notes:
        text = note.get("title", "") + " " + note.get("content", "")
        matched = False
        for topic, keywords in TOPIC_KEYWORDS.items():
            if any(k in text for k in keywords):
                clusters[topic].append(note.get("title", ""))
                matched = True
                break
        if not matched:
            clusters["其他"].append(note.get("title", ""))

    return [
        {"topic": t, "count": len(ns), "examples": ns[:3]}
        for t, ns in clusters.items() if ns
    ]


def analyze_competitors(notes: list[dict], competitors: list[str]) -> list[dict]:
    """竞品对比"""
    if not competitors:
        return []
    stats: dict[str, dict] = {
        c: {"mentions": 0, "positive": 0, "negative": 0, "related_words": Counter()}
        for c in competitors
    }
    for note in notes:
        text = note.get("title", "") + " " + note.get("content", "")
        for c in note.get("comments", []):
            text += " " + c.get("content", "")
        for comp in competitors:
            if comp in text:
                s = stats[comp]
                s["mentions"] += 1
                sen = sentiment(text)
                if sen == "正面":
                    s["positive"] += 1
                elif sen == "负面":
                    s["negative"] += 1
                words = tokenize(text)
                s["related_words"].update(w for w in words if w != comp)

    return [
        {
            "brand": c,
            "mentions": s["mentions"],
            "positive_ratio": s["positive"] / max(s["mentions"], 1),
            "negative_ratio": s["negative"] / max(s["mentions"], 1),
            "top_related": [w for w, _ in s["related_words"].most_common(5)],
        }
        for c, s in stats.items()
    ]


def analyze_trends(notes: list[dict]) -> list[dict]:
    """时间趋势"""
    daily: dict[str, dict] = defaultdict(
        lambda: {"count": 0, "positive": 0, "negative": 0, "neutral": 0}
    )
    for note in notes:
        date = note.get("scraped_at", note.get("date", "未知"))
        day = daily[date]
        day["count"] += 1
        s = sentiment(note.get("content", ""))
        if s == "正面":
            day["positive"] += 1
        elif s == "负面":
            day["negative"] += 1
        else:
            day["neutral"] += 1

    return [{"date": d, **v} for d, v in sorted(daily.items())]


# ============================================================
# 报告生成
# ============================================================

def estimate_tokens(notes: list[dict]) -> dict:
    """估算 token 消耗（中文约 1.5 字符/token，英文约 4 字符/token）"""
    total_chars = 0
    for note in notes:
        total_chars += len(note.get("title", ""))
        total_chars += len(note.get("content", ""))
        for c in note.get("comments", []):
            total_chars += len(c.get("content", ""))
    # 中文为主，按 1.5 字符/token 估算
    estimated_input = int(total_chars / 1.5)
    # 报告输出约为输入的 30%
    estimated_output = int(estimated_input * 0.3)
    return {
        "total_chars": total_chars,
        "estimated_input_tokens": estimated_input,
        "estimated_output_tokens": estimated_output,
        "estimated_total_tokens": estimated_input + estimated_output,
    }


def generate_report(notes: list[dict], competitors: list[str]) -> dict:
    return {
        "summary": {
            "total_notes": len(notes),
            "total_comments": sum(len(n.get("comments", [])) for n in notes),
            "generated_at": datetime.now().isoformat(),
        },
        "sentiment": analyze_sentiment(notes),
        "keywords": analyze_keywords(notes),
        "kol": analyze_kol(notes),
        "topics": analyze_topics(notes),
        "competitors": analyze_competitors(notes, competitors),
        "trends": analyze_trends(notes),
        "token_usage": estimate_tokens(notes),
    }


# ---- 章节编号器 ----

class SectionCounter:
    def __init__(self, start: int = 1):
        self._n = start
    def next(self, title: str) -> str:
        s = f"{self._n}. {title}"
        self._n += 1
        return s


def report_to_markdown(report: dict, brand: str = "品牌") -> str:
    sec = SectionCounter()
    lines = [
        f"# {brand} 小红书舆情分析报告",
        f"> 生成时间：{report['summary']['generated_at'][:10]} "
        f"| 样本量：{report['summary']['total_notes']} 条笔记 "
        f"| 评论数：{report['summary']['total_comments']}",
        "",
        f"## {sec.next('概览摘要')}",
        f"- 笔记总数：{report['summary']['total_notes']}",
        f"- 评论总数：{report['summary']['total_comments']}",
        "",
        f"## {sec.next('情感分析')}",
        "",
    ]

    sd = report["sentiment"]["note_counts"]
    for label in ("正面", "负面", "中性"):
        pct = report["sentiment"]["note_distribution"].get(label, 0)
        lines.append(f"- **{label}**：{sd.get(label, 0)} 条（{pct:.0%}）")

    if report["sentiment"]["top_positive"]:
        lines += ["", "**正面典型**："]
        for n in report["sentiment"]["top_positive"]:
            lines.append(f"- [{n['title']}]({n['url']}) — {n['snippet']}")
    if report["sentiment"]["top_negative"]:
        lines += ["", "**负面典型**："]
        for n in report["sentiment"]["top_negative"]:
            lines.append(f"- [{n['title']}]({n['url']}) — {n['snippet']}")

    lines += ["", f"## {sec.next('热词 Top 20')}", "",
              "| 排名 | 关键词 | 出现次数 |", "|------|--------|---------|"]
    for i, kw in enumerate(report["keywords"], 1):
        lines.append(f"| {i} | {kw['word']} | {kw['count']} |")

    lines += ["", f"## {sec.next('KOL 影响力排名')}", "",
              "| 排名 | 作者 | 笔记数 | 总互动量 | 代表作 |",
              "|------|------|--------|---------|--------|"]
    for k in report["kol"]:
        lines.append(
            f"| {k['rank']} | {k['author']} | {k['note_count']} "
            f"| {k['total_engagement']} | {k.get('best_note', '')} |"
        )

    lines += ["", f"## {sec.next('话题聚类')}", ""]
    for t in report["topics"]:
        lines.append(f"### {t['topic']}（{t['count']} 条）")
        for ex in t["examples"]:
            lines.append(f"- {ex}")
        lines.append("")

    if report["competitors"]:
        lines += [
            f"## {sec.next('竞品声量对比')}", "",
            "| 品牌 | 提及次数 | 正面占比 | 负面占比 | 常见关联词 |",
            "|------|---------|---------|---------|-----------|",
        ]
        for c in report["competitors"]:
            related = "、".join(c["top_related"]) if c["top_related"] else "-"
            lines.append(
                f"| {c['brand']} | {c['mentions']} "
                f"| {c['positive_ratio']:.0%} | {c['negative_ratio']:.0%} "
                f"| {related} |"
            )
        lines.append("")

    lines += [f"## {sec.next('时间趋势')}", "",
              "| 日期 | 发布量 | 正面 | 负面 | 中性 |",
              "|------|--------|------|------|------|"]
    for t in report["trends"]:
        lines.append(
            f"| {t['date']} | {t['count']} | {t['positive']} "
            f"| {t['negative']} | {t['neutral']} |"
        )

    lines += ["", f"## {sec.next('风险预警与建议')}", "",
              "*由 Claude 基于以上数据撰写。*"]

    # ---- Token 消耗 ----
    tk = report.get("token_usage", {})
    if tk:
        lines += [
            "",
            "---",
            f"> 本次分析处理 **{tk['total_chars']:,}** 字符，"
            f"预估消耗 **{tk['estimated_total_tokens']:,}** tokens"
            f"（输入 ~{tk['estimated_input_tokens']:,} / 输出 ~{tk['estimated_output_tokens']:,}）",
        ]

    return "\n".join(lines)


def report_to_lark_markdown(report: dict, brand: str = "品牌") -> str:
    """生成飞书文档优化格式（Lark-flavored Markdown）"""
    sec = SectionCounter()
    lines: list[str] = []

    # ---- 概览 ----
    total = report["summary"]["total_notes"]
    comments = report["summary"]["total_comments"]
    date = report["summary"]["generated_at"][:10]

    lines += [
        f'<callout emoji="📊" background-color="light-blue">',
        f"**报告生成时间**：{date} | **样本量**：{total} 条笔记 | **评论数**：{comments}",
        "</callout>",
        "",
        f"## {sec.next('概览摘要')}",
        "",
    ]

    # 概览分栏
    sd = report["sentiment"]["note_counts"]
    pos_pct = report["sentiment"]["note_distribution"].get("正面", 0)
    neg_pct = report["sentiment"]["note_distribution"].get("负面", 0)
    neu_pct = report["sentiment"]["note_distribution"].get("中性", 0)

    lines += [
        '<grid cols="3">',
        "<column>",
        "",
        f'<callout emoji="👍" background-color="light-green">',
        f'**正面** {sd.get("正面", 0)} 条（{pos_pct:.0%}）',
        "</callout>",
        "",
        "</column>",
        "<column>",
        "",
        f'<callout emoji="👎" background-color="light-red">',
        f'**负面** {sd.get("负面", 0)} 条（{neg_pct:.0%}）',
        "</callout>",
        "",
        "</column>",
        "<column>",
        "",
        f'<callout emoji="😐" background-color="pale-gray">',
        f'**中性** {sd.get("中性", 0)} 条（{neu_pct:.0%}）',
        "</callout>",
        "",
        "</column>",
        "</grid>",
        "",
    ]

    # ---- 情感分析 ----
    lines.append(f"## {sec.next('情感分析')}")
    lines.append("")

    if report["sentiment"]["top_positive"]:
        lines += [
            f'<callout emoji="✅" background-color="light-green">',
            "**正面典型**",
            "",
        ]
        for n in report["sentiment"]["top_positive"]:
            lines.append(f"- [{n['title']}]({n['url']}) — {n['snippet']}")
        lines += ["</callout>", ""]

    if report["sentiment"]["top_negative"]:
        lines += [
            f'<callout emoji="⚠️" background-color="light-yellow">',
            "**负面典型（需关注）**",
            "",
        ]
        for n in report["sentiment"]["top_negative"]:
            lines.append(f"- [{n['title']}]({n['url']}) — {n['snippet']}")
        lines += ["</callout>", ""]

    # 评论情感
    cd = report["sentiment"].get("comment_distribution", {})
    if cd:
        lines += [
            "**评论情感分布**：",
            f'- 正面 {cd.get("正面", 0)} | 负面 {cd.get("负面", 0)} | 中性 {cd.get("中性", 0)}',
            "",
        ]

    # ---- 热词 ----
    lines += [f"## {sec.next('热词 Top 20')}", "",
              "| 排名 | 关键词 | 出现次数 |", "|------|--------|---------|"]
    for i, kw in enumerate(report["keywords"], 1):
        lines.append(f"| {i} | {kw['word']} | {kw['count']} |")
    lines.append("")

    # ---- KOL ----
    lines += [f"## {sec.next('KOL 影响力排名')}", "",
              "| 排名 | 作者 | 笔记数 | 总互动量 | 代表作 |",
              "|------|------|--------|---------|--------|"]
    for k in report["kol"]:
        lines.append(
            f"| {k['rank']} | {k['author']} | {k['note_count']} "
            f"| {k['total_engagement']} | {k.get('best_note', '')} |"
        )
    lines.append("")

    # ---- 话题聚类 ----
    lines += [f"## {sec.next('话题聚类')}", ""]
    for t in report["topics"]:
        lines.append(f"### {t['topic']}（{t['count']} 条）")
        for ex in t["examples"]:
            lines.append(f"- {ex}")
        lines.append("")

    # ---- 竞品（条件渲染）----
    if report["competitors"]:
        lines += [
            f"## {sec.next('竞品声量对比')}", "",
            "| 品牌 | 提及次数 | 正面占比 | 负面占比 | 常见关联词 |",
            "|------|---------|---------|---------|-----------|",
        ]
        for c in report["competitors"]:
            related = "、".join(c["top_related"]) if c["top_related"] else "-"
            lines.append(
                f"| {c['brand']} | {c['mentions']} "
                f"| {c['positive_ratio']:.0%} | {c['negative_ratio']:.0%} "
                f"| {related} |"
            )
        lines.append("")

    # ---- 时间趋势 ----
    lines += [f"## {sec.next('时间趋势')}", "",
              "| 日期 | 发布量 | 正面 | 负面 | 中性 |",
              "|------|--------|------|------|------|"]
    for t in report["trends"]:
        lines.append(
            f"| {t['date']} | {t['count']} | {t['positive']} "
            f"| {t['negative']} | {t['neutral']} |"
        )
    lines.append("")

    # ---- 风险预警 ----
    lines += [
        f"## {sec.next('风险预警与建议')}",
        "",
        f'<callout emoji="🔔" background-color="light-yellow">',
        "**以下分析由 Claude 基于以上数据自动生成，请结合实际业务判断。**",
        "</callout>",
    ]

    # ---- Token 消耗 ----
    tk = report.get("token_usage", {})
    if tk:
        lines += [
            "",
            "---",
            "",
            f'<callout emoji="🔢" background-color="pale-gray">',
            f"**本次分析资源消耗**：处理 {tk['total_chars']:,} 字符，"
            f"预估消耗 {tk['estimated_total_tokens']:,} tokens"
            f"（输入 ~{tk['estimated_input_tokens']:,} / 输出 ~{tk['estimated_output_tokens']:,}）",
            "</callout>",
        ]

    return "\n".join(lines)


# ============================================================
# CLI 入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="小红书舆情分析")
    parser.add_argument("input", help="xhs_data.json 路径")
    parser.add_argument("--output", "-o", default="xhs_report.json", help="输出文件路径")
    parser.add_argument("--format", "-f", choices=["json", "markdown", "lark-markdown"],
                        default="json")
    parser.add_argument("--brand", "-b", default="品牌", help="品牌名称（用于报告标题）")
    parser.add_argument("--competitors", "-c", default="", help="竞品品牌，逗号分隔")
    args = parser.parse_args()

    data = json.loads(Path(args.input).read_text(encoding="utf-8"))
    competitors = [c.strip() for c in args.competitors.split(",") if c.strip()]
    report = generate_report(data, competitors)

    output_path = Path(args.output)
    if args.format == "lark-markdown":
        output_path.write_text(
            report_to_lark_markdown(report, args.brand), encoding="utf-8"
        )
    elif args.format == "markdown":
        output_path.write_text(
            report_to_markdown(report, args.brand), encoding="utf-8"
        )
    else:
        output_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    print(f"报告已生成：{output_path}")


if __name__ == "__main__":
    main()
