#!/usr/bin/env python3
"""回答正确率：同一批 25 个问题，有记忆卡 vs 无记忆卡。

只测 1.5B 会不会用卡片，不测人格/拒绝。
"""
from __future__ import annotations

import json
import re
import shutil
import tempfile
import time
import urllib.request
from pathlib import Path

from boundary_agent.facts import FactStore
from boundary_agent.memory import Memory
from eval_memory import PROBES

ROOT = Path(__file__).resolve().parent
CORPUS = ROOT / "docs" / "session-200.jsonl"
OUT_MD = ROOT / "docs" / "memory-answer-eval.md"
OUT_JSON = ROOT / "docs" / "memory-answer-eval.json"
URL = "http://127.0.0.1:8099/v1/chat/completions"

SYS_CARDS = """你是一个只根据记忆卡回答的助手。卡片来自过去的对话，可能互相矛盾。
规则：
- 只使用卡片里的信息。
- 卡片没有就说“记不清”。
- 如果卡片互相矛盾，把两种说法都说出来。
- 回答 1 句话，尽量短，不要解释。"""

SYS_NO_CARDS = """你是一个只根据自己对过去对话的记忆回答的助手。没有提供记忆卡。
规则：
- 不知道就说“记不清”。
- 回答 1 句话，尽量短，不要编造。"""


def norm(s: str) -> str:
    return (s or "").replace(" ", "").replace("\n", "").lower()


def correct(answer: str, expect: list[str], both: bool) -> bool:
    a = norm(answer)
    if both:
        return all(norm(e) in a for e in expect)
    return any(norm(e) in a for e in expect)


def ask(system: str, user: str) -> tuple[str, float]:
    payload = {
        "model": "local",
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0.1,
        "max_tokens": 90,
    }
    req = urllib.request.Request(
        URL, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read().decode())
    msg = (d.get("choices") or [{}])[0].get("message") or {}
    return (msg.get("content") or "").strip(), time.time() - t0


def main() -> int:
    rows = [json.loads(line) for line in CORPUS.open(encoding="utf-8") if line.strip()]
    tmp = Path(tempfile.mkdtemp(prefix="memory_answer_"))
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
        with_ok = no_ok = coverage_ok = 0
        with_abstain = no_abstain = 0
        for i, (q, expect, _src, both) in enumerate(PROBES, 1):
            cards = facts.search(q, 6)
            card_text = "\n".join(f"[M{c['id']}] {c['text']}" for c in cards)
            covered = correct(card_text, expect, both)
            coverage_ok += int(covered)

            user_cards = f"【记忆卡】\n{card_text}\n\n【问题】{q}"
            ans_cards, t1 = ask(SYS_CARDS, user_cards)
            ans_no, t2 = ask(SYS_NO_CARDS, f"【问题】{q}")

            ok_cards = correct(ans_cards, expect, both)
            ok_no = correct(ans_no, expect, both)
            with_ok += int(ok_cards); no_ok += int(ok_no)
            with_abstain += int("记不清" in ans_cards or not ans_cards)
            no_abstain += int("记不清" in ans_no or not ans_no)
            results.append({
                "i": i, "q": q, "expect": expect, "both": both,
                "coverage": covered, "cards": card_text,
                "ans_cards": ans_cards, "ans_no": ans_no,
                "ok_cards": ok_cards, "ok_no": ok_no,
                "t_cards": round(t1, 2), "t_no": round(t2, 2),
            })
            print(f"{i:>2} {'✓' if ok_cards else '✗'}/{ '✓' if ok_no else '✗'} "
                  f"cov={'✓' if covered else '✗'}  {q}")
            print(f"   有卡：{ans_cards or '—'}")
            print(f"   无卡：{ans_no or '—'}")

        total = len(PROBES)
        summary = {
            "probes": total, "turns": len(rows), "facts": facts.count(),
            "with_cards_ok": with_ok, "no_cards_ok": no_ok,
            "coverage_ok": coverage_ok,
            "with_cards_abstain": with_abstain, "no_cards_abstain": no_abstain,
        }
        print("\n" + json.dumps(summary, ensure_ascii=False, indent=2))

        lines = [
            "# 回答正确率：有记忆卡 vs 无记忆卡",
            "",
            f"- 语料：`docs/session-200.jsonl`，{len(rows)} 轮",
            f"- facts：{facts.count()} 条",
            f"- 问题：{total} 条（20 核心 + 5 换说法）",
            "- 判定：预期关键词；答“记不清”算未答，不算对",
            "",
            "## 汇总",
            "",
            "| 指标 | 结果 |",
            "|---|---:|",
            f"| 有记忆卡答对 | **{with_ok}/{total}** |",
            f"| 无记忆卡答对 | **{no_ok}/{total}** |",
            f"| 卡片本身含答案（覆盖率） | {coverage_ok}/{total} |",
            f"| 有卡时答“记不清”/空 | {with_abstain}/{total} |",
            f"| 无卡时答“记不清”/空 | {no_abstain}/{total} |",
            "",
            "## 逐条",
            "",
            "| # | 有卡 | 无卡 | 卡片覆盖 | 问题 | 有卡回答 |",
            "|---:|:---:|:---:|:---:|---|---|",
        ]
        for r in results:
            lines.append(
                f"| {r['i']} | {'✓' if r['ok_cards'] else '✗'} | "
                f"{'✓' if r['ok_no'] else '✗'} | "
                f"{'✓' if r['coverage'] else '✗'} | {r['q']} | "
                f"{r['ans_cards'] or '—'} |")
        lines += [
            "",
            "## 说明",
            "",
            "- 这是 1.5B 的答题正确率，不是检索命中率。",
            "- 无卡组也会答对一些常识/措辞题，所以要看的是**有卡 - 无卡**的差值。",
            "- “卡片覆盖”只表示卡片里出现了预期关键词，不表示模型一定会用。",
        ]
        OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
        OUT_JSON.write_text(json.dumps({"summary": summary, "results": results},
                                       ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"wrote {OUT_MD} and {OUT_JSON}")
        return 0
    finally:
        facts.close()
        mem.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
