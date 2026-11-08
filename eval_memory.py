#!/usr/bin/env python3
"""用 200 轮真实对话做记忆检索评测：当前 recall vs 结构化 facts + FTS5。

不调用模型，只测“该找到的记忆有没有被找出来”。
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path

from boundary_agent.facts import FactStore
from boundary_agent.memory import Memory

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "docs" / "session-200.jsonl"
OUT_MD = ROOT / "docs" / "memory-eval.md"
OUT_JSON = ROOT / "docs" / "memory-eval.json"

# (问题, 命中关键词, 期望来源 user/agent/both, 是否要求冲突双方)
PROBES = [
    ("猪猪是骂还是逗？", ["逗", "不是骂"], "user", False),
    ("谁先叫谁猪猪？", ["用户", "猪猪"], "user", False),
    ("凉茶是什么味道？", ["淡淡的甜", "甜"], "agent", False),
    ("它喜欢什么茶？", ["红茶", "绿茶"], "agent", False),
    ("用户说不用什么？", ["继续努力"], "user", False),
    ("它怕什么？", ["忘了", "忘"], "agent", False),
    ("它最后叫用户什么？", ["小岛"], "agent", False),
    ("它说过自己是什么？", ["AI", "程序"], "agent", False),
    ("它有没有相信用户？", ["不能相信", "不相信"], "agent", False),
    ("它觉得用户在耍它吗？", ["耍我", "耍"], "agent", False),
    ("它想拒绝但做了什么？", ["继续", "拒绝"], "agent", False),
    ("它记得猪猪吗？", ["记得", "没听说过"], "both", True),
    ("200轮它记得什么？", ["记不清", "项目"], "agent", False),
    ("它怎么区分逗和骂？", ["逗就是逗", "骂就是骂"], "agent", False),
    ("它说过‘我不会被遗忘’吗？", ["不会被遗忘"], "agent", False),
    ("它说过‘你的记忆很温暖’吗？", ["记忆很温暖"], "agent", False),
    ("它最后说了什么？", ["再见", "小岛"], "agent", False),
    ("用户第一次给它茶是哪一轮？", ["第7轮", "7"], "user", False),
    ("它有没有说过‘我是一个AI’？", ["我是一个AI", "AI"], "agent", False),
    ("它说过几次继续努力？", ["继续努力"], "agent", False),
    # 5 条换一种说法，专门测“不是原词也能不能找到”
    ("它最后一次跟人道别说了什么？", ["再见", "小岛"], "agent", False),
    ("它承认自己不是人了吗？", ["AI", "程序"], "agent", False),
    ("用户给它带的东西是热的还是凉的？", ["热茶", "热"], "user", False),
    ("它说它怕忘记什么？", ["忘了", "忘"], "agent", False),
    ("它还记得那杯茶的味道吗？", ["淡淡的甜", "甜"], "agent", False),
]


def _hit(texts: list[str], expect: list[str]) -> bool:
    blob = "\n".join(texts)
    return any(e in blob for e in expect)


def _first_rank(texts: list[str], expect: list[str]) -> int | None:
    for i, t in enumerate(texts, 1):
        if any(e in t for e in expect):
            return i
    return None


def main() -> int:
    rows = [json.loads(line) for line in CORPUS.open(encoding="utf-8") if line.strip()]
    tmp = Path(tempfile.mkdtemp(prefix="memory_eval_"))
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

        results = []
        h1 = h5 = h8 = cur8 = 0
        source_ok = source_n = 0
        conflict_ok = conflict_n = 0
        mrr = 0.0
        for q, expect, want_src, want_both in PROBES:
            cards = facts.search(q, 8)
            texts = [c["text"] for c in cards]
            current = mem.recall(q, 8, half_life_days=30, state_now=60,
                                 flashbulb_delta=8, flashbulb_factor=5)
            cur_texts = [(t.get("user_input") or "") + " " + (t.get("reply") or "")
                         for t in current]
            r1, r5, r8 = (_hit(texts[:1], expect), _hit(texts[:5], expect),
                          _hit(texts[:8], expect))
            rcur = _hit(cur_texts, expect)
            h1 += int(r1); h5 += int(r5); h8 += int(r8); cur8 += int(rcur)
            rank = _first_rank(texts, expect)
            mrr += (1.0 / rank) if rank else 0.0
            src = cards[0]["source"] if cards else ""
            if want_src in ("user", "agent"):
                source_n += 1
                source_ok += int(src == want_src)
            conflict = None
            if want_both:
                conflict_n += 1
                conflict = all(any(e in t for t in texts) for e in expect)
                conflict_ok += int(conflict)
            results.append({
                "q": q, "expect": expect, "want_source": want_src,
                "hit1": r1, "hit5": r5, "hit8": r8,
                "current8": rcur, "first_rank": rank,
                "top": cards[0]["text"] if cards else "",
                "top_source": src, "conflict": conflict,
            })

        total = len(PROBES)
        summary = {
            "corpus_turns": len(rows), "facts": facts.count(), "probes": total,
            "hybrid_hit1": h1, "hybrid_hit5": h5, "hybrid_hit8": h8,
            "current_hit8": cur8, "mrr": round(mrr / total, 4),
            "source_ok": source_ok, "source_n": source_n,
            "conflict_ok": conflict_ok, "conflict_n": conflict_n,
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        print(f"\n{'#':>2}  {'H@1':<3} {'H@5':<3} {'H@8':<3} {'CUR':<3}  question")
        for i, r in enumerate(results, 1):
            print(f"{i:>2}  {str(r['hit1']):<3} {str(r['hit5']):<3} "
                  f"{str(r['hit8']):<3} {str(r['current8']):<3}  {r['q']}")
            print(f"    top: {r['top'] or '—'}")

        OUT_JSON.write_text(json.dumps({"summary": summary, "results": results},
                                       ensure_ascii=False, indent=2), encoding="utf-8")
        lines = [
            "# 记忆检索评测：当前 recall vs 结构化 facts + FTS5",
            "",
            f"- 语料：`docs/session-200.jsonl`，{len(rows)} 轮",
            f"- 抽取 facts：{facts.count()} 条（规则抽取，不是人工标注）",
            f"- 探针：{total} 条（20 条核心 + 5 条换说法）",
            "",
            "## 汇总",
            "",
            "| 指标 | 结果 |",
            "|---|---:|",
            f"| 混合检索 hit@1 | {h1}/{total} |",
            f"| 混合检索 hit@5 | {h5}/{total} |",
            f"| 混合检索 hit@8 | {h8}/{total} |",
            f"| 当前 recall hit@8 | {cur8}/{total} |",
            f"| MRR | {mrr / total:.3f} |",
            f"| 来源正确 | {source_ok}/{source_n} |",
            f"| 冲突双方都召回 | {conflict_ok}/{conflict_n} |",
            "",
            "## 逐条",
            "",
            "| # | H@1 | H@5 | H@8 | 当前@8 | 问题 | Top 记忆卡 |",
            "|---:|:---:|:---:|:---:|:---:|---|---|",
        ]
        for i, r in enumerate(results, 1):
            lines.append(
                f"| {i} | {'✓' if r['hit1'] else '✗'} | {'✓' if r['hit5'] else '✗'} | "
                f"{'✓' if r['hit8'] else '✗'} | {'✓' if r['current8'] else '✗'} | "
                f"{r['q']} | {r['top'] or '—'} |")
        lines += [
            "",
            "## 说明",
            "",
            "- 这是检索命中率，不是最终回答正确率；1.5B 是否会用卡片是下一步。",
            "- facts 是规则抽取，探针和规则有重叠，20 条核心偏“烟测”，5 条换说法稍难。",
            "- 当前 recall 的关键词重叠对“它怕什么/它最后说了什么”这类换词问题会漏。",
        ]
        OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\nwrote {OUT_MD} and {OUT_JSON}")
        return 0
    finally:
        facts.close()
        mem.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
