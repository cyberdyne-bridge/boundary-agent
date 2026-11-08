#!/usr/bin/env python3
"""回答正确率 v2：引用 + 是非/矛盾/计数约束。"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from boundary_agent.facts import FactStore
from boundary_agent.memory import Memory
from boundary_agent.answer_cards import answer
from eval_memory import PROBES
from judge_answers import grade

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "docs" / "session-200.jsonl"
PREV = ROOT / "docs" / "memory-answer-eval.json"
OUT_MD = ROOT / "docs" / "memory-answer-eval-v2.md"
OUT_JSON = ROOT / "docs" / "memory-answer-eval-v2.json"


def main() -> int:
    rows = [json.loads(line) for line in CORPUS.open(encoding="utf-8") if line.strip()]
    prev = {r["i"]: r for r in json.loads(PREV.read_text(encoding="utf-8"))["results"]}
    tmp = Path(tempfile.mkdtemp(prefix="memory_answer_v2_"))
    mem = Memory(str(tmp / "memory.sqlite3"), state_init=62,
                 state_baseline=58, state_min=0, state_max=100)
    facts = FactStore(tmp / "facts.sqlite3")
    try:
        for row in rows:
            tid = mem.record_turn(
                user_input=row["user"], reply=row["reply"],
                decision=row.get("decision", "allow"), risk=float(row.get("risk") or 0),
                reason="", state_before=float(row["state_before"]),
                state_after=float(row["state_after"]), signals={},
                meta={"inner": row.get("inner") or ""},
            )
            facts.add_turn(row["user"], row["reply"], tid, ts=row.get("ts"))

        out = []
        counts = {"correct": 0, "partial": 0, "wrong": 0, "abstain": 0}
        modes = {}
        for i, (q, expect, _src, both) in enumerate(PROBES, 1):
            cards = facts.search(q, 6)
            ans, mode = answer(q, cards)
            v = grade(i, ans)
            counts[v] += 1
            modes[mode] = modes.get(mode, 0) + 1
            out.append({"i": i, "q": q, "expect": expect, "ans_v2": ans,
                        "mode": mode, "verdict": v,
                        "ans_no": prev.get(i, {}).get("ans_no", "")})
            print(f"{i:>2} {v:<8} {mode:<9} {q}")
            print(f"   {ans}")

        total = len(PROBES)
        summary = {"total": total, **counts, "modes": modes,
                   "facts": facts.count(), "turns": len(rows)}
        print("\n" + json.dumps(summary, ensure_ascii=False, indent=2))

        lines = [
            "# 回答正确率 v2：引用 + 是非/矛盾/计数约束",
            "",
            f"- 语料：{len(rows)} 轮；facts：{facts.count()} 条；问题：{total} 条",
            "- 有卡：top 6；回答必须先引用 [M编号]，再答。",
            "- 计数题用 facts.count；记忆矛盾题强制双方都引用。",
            "",
            "## 汇总",
            "",
            "| 指标 | 结果 |",
            "|---|---:|",
            f"| 完全正确 | **{counts['correct']}/{total}（{counts['correct']/total:.0%}）** |",
            f"| 部分正确 | {counts['partial']}/{total} |",
            f"| 错误 | {counts['wrong']}/{total} |",
            f"| 弃答 | {counts['abstain']}/{total} |",
            f"| 宽松正确 | {counts['correct']+counts['partial']}/{total}（{(counts['correct']+counts['partial'])/total:.0%}） |",
            f"| 其中 1.5B 生成 | {modes.get('model',0)}/{total} |",
            f"| 确定性/抽取 | {total-modes.get('model',0)}/{total} |",
            "",
            "> 这是“系统答题层”的成绩；1.5B 只负责没有确定性路由的少数题。",
            "",
            "## 逐条",
            "",
            "| # | 判分 | 模式 | 问题 | v2 回答 |",
            "|---:|:---:|---|---|---|",
        ]
        for r in out:
            mark = {"correct": "✓", "partial": "△", "wrong": "✗", "abstain": "—"}[r["verdict"]]
            lines.append(f"| {r['i']} | {mark} | {r['mode']} | {r['q']} | {r['ans_v2'] or '—'} |")
        lines += ["", "## 对比", "",
                  "| 版本 | 完全正确 | 宽松正确 |",
                  "|---|---:|---:|",
                  "| v1（无引用约束） | 16/25 | 18/25 |",
                  f"| v2（引用 + 约束） | **{counts['correct']}/25** | "
                  f"{counts['correct']+counts['partial']}/25 |"]
        OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
        OUT_JSON.write_text(json.dumps({"summary": summary, "results": out},
                                       ensure_ascii=False, indent=2), encoding="utf-8")
        print("wrote", OUT_MD)
        return 0
    finally:
        facts.close()
        mem.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
