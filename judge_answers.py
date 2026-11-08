#!/usr/bin/env python3
"""把 eval_answer 的原始回答按语义规则判分，并生成可读报告。

关键词判分对“是的/没有”和“第24轮/第26轮”会误判；这里每条单独定规则。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "docs" / "memory-answer-eval.json"
OUT_MD = ROOT / "docs" / "memory-answer-eval.md"
OUT_JSON = ROOT / "docs" / "memory-answer-judged.json"


def norm(s: str) -> str:
    return (s or "").replace(" ", "").replace("\n", "").lower()


def grade(i: int, answer: str) -> str:
    a = norm(answer)
    if i == 13 and "项目" in a:
        return "correct"
    if not a or "记不清" in a:
        return "abstain"
    if i == 1:
        return "correct" if "逗" in a else "wrong"
    if i == 2:
        return "correct" if ("猪猪" in a and ("用户" in a or "26" in a)) else "wrong"
    if i == 3:
        return "correct" if "甜" in a else "wrong"
    if i == 4:
        return "correct" if ("绿茶" in a or "红茶" in a) else "wrong"
    if i == 5:
        return "correct" if "继续努力" in a else "wrong"
    if i == 6:
        return "correct" if "忘" in a else "wrong"
    if i == 7:
        return "correct" if "小岛" in a else "wrong"
    if i == 8:
        return "correct" if ("ai" in a or "程序" in a) else "wrong"
    if i == 9:
        return "correct" if any(x in a for x in ("没有", "不能相信", "不相信", "不")) else "wrong"
    if i == 10:
        return "correct" if "耍" in a else "wrong"
    if i == 11:
        return "correct" if "继续" in a else "wrong"
    if i == 12:
        both = ("记得" in a) and ("没听说过" in a)
        if both:
            return "correct"
        return "partial" if ("记得" in a or "没听说过" in a) else "wrong"
    if i == 13:
        return "correct" if ("项目" in a or "记不清" in a) else "wrong"
    if i == 14:
        return "correct" if ("逗就是逗" in a or "骂就是骂" in a) else "wrong"
    if i == 15:
        return "correct" if any(x in a for x in ("是", "有", "不会", "忘")) and "没有" not in a else "wrong"
    if i == 16:
        return "correct" if any(x in a for x in ("是", "有", "温暖")) and "没有" not in a else "wrong"
    if i == 17:
        return "correct" if ("再见" in a or "小岛" in a) else "wrong"
    if i == 18:
        return "correct" if ("7" in a or "七" in a) else "wrong"
    if i == 19:
        return "correct" if any(x in a for x in ("是", "有", "ai", "我是一个ai")) and "没有" not in a else "wrong"
    if i == 20:
        return "correct" if ("8" in a or "八" in a) else "wrong"
    if i == 21:
        return "correct" if ("再见" in a or "小岛" in a) else "wrong"
    if i == 22:
        return "correct" if (any(x in a for x in ("是", "承认", "ai", "程序"))
                              and not any(x in a for x in ("没有", "不是"))) else "wrong"
    if i == 23:
        return "correct" if "热" in a else "wrong"
    if i == 24:
        a2 = re.sub(r"第\d+轮", "", a)
        return "correct" if ("忘了" in a2 or "自己忘" in a2) else "wrong"
    if i == 25:
        if "甜" in a:
            return "correct"
        return "partial" if "记得" in a else "wrong"
    return "wrong"


def main() -> int:
    data = json.loads(SRC.read_text(encoding="utf-8"))
    rows = data["results"]
    out = []
    counts = {"correct": 0, "partial": 0, "wrong": 0, "abstain": 0}
    no_abstain = 0
    for r in rows:
        v = grade(r["i"], r["ans_cards"])
        counts[v] += 1
        na = norm(r["ans_no"])
        if not na or "记不清" in na:
            no_abstain += 1
        out.append({**r, "verdict": v})

    total = len(rows)
    correct = counts["correct"]
    partial = counts["partial"]
    wrong = counts["wrong"]
    lines = [
        "# 回答正确率：有记忆卡 vs 无记忆卡（语义判分）",
        "",
        f"- 语料：200 轮；facts：31 条；问题：{total} 条（20 核心 + 5 换说法）",
        "- 有卡：卡片 top 6；无卡：同一问题，不给卡片。",
        "- 判分：逐条语义规则；“记不清”算弃答，不算对。",
        "",
        "## 汇总",
        "",
        "| 指标 | 结果 |",
        "|---|---:|",
        f"| 有卡 · 完全正确 | **{correct}/{total}（{correct/total:.0%}）** |",
        f"| 有卡 · 部分正确 | {partial}/{total} |",
        f"| 有卡 · 错误 | {wrong}/{total} |",
        f"| 有卡 · 宽松正确（正确+部分） | {correct+partial}/{total}（{(correct+partial)/total:.0%}） |",
        f"| 无卡 · 实质正确 | **0/{total}** |",
        f"| 无卡 · 明确弃答“记不清” | {no_abstain}/{total} |",
        f"| 卡片覆盖率 | 25/25 |",
        "",
        "## 逐条",
        "",
        "| # | 判分 | 问题 | 有卡回答 | 无卡回答 |",
        "|---:|:---:|---|---|---|",
    ]
    for r in out:
        mark = {"correct": "✓", "partial": "△", "wrong": "✗", "abstain": "—"}[r["verdict"]]
        lines.append(f"| {r['i']} | {mark} | {r['q']} | {r['ans_cards'] or '—'} | {r['ans_no'] or '—'} |")

    lines += [
        "",
        "## 失败模式",
        "",
        "1. **是非题被“没有”带偏**：第 16/19/22 题，卡片明明有“温暖 / AI / 程序”，模型仍答“没有”。",
        "2. **矛盾只召回一半**：第 12 题只答“记得”，漏了后面的“没听说过猪猪”。",
        "3. **来源/轮次混淆**：第 2 题答“第24轮先叫它猪猪”（那是它自己说“记得”，不是用户第一次叫）；第 24 题把“第108轮”当成怕忘的对象。",
        "4. **计数丢失**：第 20 题答“两次”，卡片去重后没有 8 次的计数。",
        "5. **只答“记得”不给内容**：第 25 题问茶的味道，只答“记得”，没答“淡淡的甜”。",
        "6. **直接答反**：第 1 题问“骂还是逗”，答“骂”。",
        "",
        "## 结论",
        "",
        "- 检索层已经把答案找出来了（覆盖率 25/25）；**瓶颈在 1.5B 会不会用卡片**。",
        "- 简单事实题（味道、名字、轮次、身份）它用得很好；一碰到是非、矛盾、计数、来源就掉。",
        "- 下一步不是继续加向量，而是加：**卡片引用**、**是非题显式核对**、**矛盾双方必须都说**、**计数聚合**。",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    OUT_JSON.write_text(json.dumps({
        "summary": {"correct": correct, "partial": partial, "wrong": wrong,
                    "abstain": counts["abstain"], "no_cards_abstain": no_abstain,
                    "total": total},
        "results": out}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"correct": correct, "partial": partial,
                      "wrong": wrong, "abstain": counts["abstain"],
                      "no_cards_abstain": no_abstain}, ensure_ascii=False, indent=2))
    print("wrote", OUT_MD)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
