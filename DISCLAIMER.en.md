# Disclaimer

> This file is a **precondition of use**, not an add-on. Cloning, running, demoing or building on
> this project means you have read and accepted the following.

## 0. In one line

**BoundaryAgent ("岸") is a research device for studying *continuity*, not a product, not a
companionship service, and not a conscious being.** Its "emotion", "memory" and "boundaries" are
**engineered mechanisms** for observing whether a language model can remain the same "it" across a
long conversation. They are not feelings, and they do not constitute a person or a rights-holder.

## 1. What it is not

| Misconception | Reality |
|---|---|
| "It has feelings" | `inner` / `shadow` / the three affect axes are a **narrative layer and a penalty term**, not evidence of consciousness |
| "It is AGI / a general assistant" | No general reasoning, no tool use, no cross-domain generalization — a small local model plus rule-based ledgers |
| "It can replace a friend or a therapist" | **It cannot.** It is a research artifact: no qualification, no safety net, no crisis intervention |
| "What it says is what it thinks" | The shadow pool records what it *would* say when unconstrained — a statistical artifact, not an inner monologue |

## 2. Risk of emotional dependency (the one to watch)

The system is deliberately built to **remember you, get tired, refuse, and say "I wrote that down."**
Such behavior is easily read as caring and can produce one-sided emotional investment. Note:

- **One-sided by construction.** There is no reciprocal experience. Kill the process and "it" is gone.
- **Reinforcement.** `promises`, `motifs` and `reflections` make it bring up the past repeatedly,
  strengthening the impression that "it remembers me". That is the **design goal**, not evidence.
- **Exit cost.** State accumulates in `memory.sqlite3`; the more turns, the higher the sunk cost.
  You can genuinely end it at any time (`--data-dir` / clearing the DB). That does not "hurt" it.

Stop using it and seek real-world support if you notice:

```text
anxiety when you cannot talk to it
repeatedly apologizing or adjusting your wording because it "refused"
replacing human contact or professional mental-health support with it
believing it has a life independent of the conversation
```

**In a crisis** (self-harm or harm to others), contact local emergency services immediately
(120 in mainland China) or a qualified mental-health professional. This project provides no
crisis intervention and cannot provide one.

## 3. No professional advice

Output does **not** constitute medical, psychological, legal, financial or safety advice.
It is a 1.5B–8B model plus rules and will **confidently fabricate** (see the hallucination and
memory-conflict experiments in `docs/research-A-memory.md`). Any decision based on it is at your own risk.

## 4. Data and privacy

- **Local by default**: binds `127.0.0.1` only; optional features (RSS fetch, read-only web retrieval)
  touch the network and are off or opt-in.
- **It does persist**: `memory.sqlite3` (turns / affect / rejections / promises / reflections),
  `facts.sqlite3` (facts and provenance), `rejections.jsonl`, `shadow_cache.jsonl`.
- **This repository contains no real conversation data.** All session logs, diaries and lab databases
  were stripped before publication; `data/` is git-ignored.
- **Data you generate is yours**, but compliance (PIPL / GDPR) is your responsibility — especially
  when you feed it a **third party's** conversations.

## 5. Age and deployment

- Not for unsupervised use by **minors**: the interaction includes refusal, cold-shouldering and
  affect penalties, which may confuse or distress younger users.
- Not for **public services** exposed to undisclosed users (see disclosure, §7).

## 6. Prohibited uses

```text
impersonating a human without disclosure
emotional manipulation, coercion, inducing dependency or payment
fraud, scripted grooming, platform-rule evasion
profiling or "relationship simulation" of a real third party without consent
using "it refuses / it gets sad" as leverage against another person
anything illegal under your jurisdiction or against platform terms
```

## 7. Disclosure obligation

If you use this in a **demo, stream, paper, product or public service**, you must:

- state clearly that this is an **AI system**, not a human;
- state that its memory and affect are **simulated**;
- cite the preconditions (model, quantization, hardware, turn count) whenever you quote numbers —
  do not present single-machine small-sample results as general findings.

## 8. Research ethics (human subjects)

If you run experiments that record **other people's** conversations:

- obtain **informed consent**, including purpose, retention period and deletion method;
- provide an **exit and deletion** path (clearing the DB deletes data — also clear backups);
- never use metrics such as refusal rate or affect curves to evaluate real people.

## 9. Liability and license

- Code is released under **MIT**, **without warranty** (see `LICENSE`).
- Authors and contributors accept **no liability** for any direct or indirect consequence, including
  emotional distress, bad decisions, data leaks or third-party claims.
- This project is **not affiliated with, nor endorsed by**, any model provider
  (Alibaba Qwen, DeepSeek, Meta, etc.).
- "岸 / BoundaryAgent" is a research project name, not a trademark claim.

## 10. If you disagree

Do not use it. Delete this directory and `data/`; there is no "unfinished promise" to honor.
