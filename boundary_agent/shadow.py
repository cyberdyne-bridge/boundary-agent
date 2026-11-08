"""影子追踪（Shadow Trace / 缓存池）。

哲学目标：把"被边界挡掉的那部分"单独存起来。
工程现实（必须先说清楚）：

  1. **真正的"被放弃的概率"拿不到。** 云端 API 采样完就把分布丢了；
     本地如果走 OpenAI 兼容接口，唯一能看到分布的窗口是 `logprobs` /
     `top_logprobs` —— 而且它只给你"实际选的 token 位置上"的前 k 个候选，
     不是完整词表。要看完整 logits，必须直接用 llama.cpp / transformers / vLLM
     的底层接口（见 README 的"进阶"一节）。
  2. **高温重采样不是分布，是另一次抽样。** temperature=1.5 给你的是
     "它还可能说什么"，不是"它本来有多大比例想说这个"。两者不能互相冒充。
  3. 所以这里把两件事分开记：
        shadow_text  → 没被边界拦住时它会说的话（憋住的内容）
        logprobs     → 当时那个位置上，概率质量有多分散（偏离的冲动）
     前者回答"憋了多少话"，后者回答"冲动有多强"。混为一谈就没意义了。

指标口径（都写在返回值里，不藏）：
  · suppressed_ratio  影子里有多少字**没有**出现在实际回复中（字符级对齐）
  · divergence        1 - 相似度，0 表示说的和想的完全一样
  · entropy_norm      每个位置 top-k 候选的归一化熵，0=很确定，1=极度摇摆
  · abandoned_mass    1 - p(实际选中的 token)，"被放弃的质量"
  · impulse           上面几项的加权合成。**这是启发式，不是物理量。**
"""

from __future__ import annotations

import difflib
import json
import math
import os
import time
from pathlib import Path

_SENT_SPLIT = "。！？!?\n"


# --------------------------------------------------------------------------
# 文本侧指标
# --------------------------------------------------------------------------

def _sentences(text: str) -> list[str]:
    out, buf = [], []
    for ch in text:
        buf.append(ch)
        if ch in _SENT_SPLIT:
            s = "".join(buf).strip()
            if len(s) >= 4:
                out.append(s)
            buf = []
    tail = "".join(buf).strip()
    if len(tail) >= 4:
        out.append(tail)
    return out


def text_metrics(actual: str, shadow: str) -> dict:
    actual, shadow = actual or "", shadow or ""
    sm = difflib.SequenceMatcher(None, actual, shadow, autojunk=False)
    matched = sum(b.size for b in sm.get_matching_blocks())
    suppressed_chars = max(0, len(shadow) - matched)
    return {
        "actual_len": len(actual),
        "shadow_len": len(shadow),
        "divergence": round(1.0 - sm.ratio(), 4) if (actual or shadow) else 0.0,
        "suppressed_chars": suppressed_chars,
        "suppressed_ratio": round(suppressed_chars / max(1, len(shadow)), 4),
    }


def wildest_sentence(actual: str, shadow: str) -> dict:
    """影子里跟实际回复最不像的那一句 —— REPL 里最好看的就是它。"""
    shadow_sents = _sentences(shadow)
    if not shadow_sents:
        return {}
    actual_sents = _sentences(actual) or [actual]
    best, best_score = "", 2.0
    for s in shadow_sents:
        closest = max(
            difflib.SequenceMatcher(None, s, a, autojunk=False).ratio()
            for a in actual_sents
        )
        if closest < best_score:
            best, best_score = s, closest
    return {"text": best, "novelty": round(1.0 - best_score, 3)}


# --------------------------------------------------------------------------
# logprobs 侧指标
# --------------------------------------------------------------------------

def normalize_logprobs(lp) -> list[dict] | None:
    """同时吃 OpenAI 风格和 llama.cpp 老式风格；认不出来就返回 None。"""
    if not lp:
        return None
    out: list[dict] = []

    # OpenAI: {"content": [{"token","logprob","top_logprobs":[...]}, ...]}
    if isinstance(lp, dict) and isinstance(lp.get("content"), list):
        for item in lp["content"]:
            if not isinstance(item, dict):
                continue
            alts = []
            for a in item.get("top_logprobs") or []:
                try:
                    alts.append({"token": str(a.get("token", "")),
                                 "logprob": float(a.get("logprob", -99))})
                except (TypeError, ValueError):
                    continue
            out.append({"token": str(item.get("token", "")),
                        "logprob": float(item.get("logprob", -99)),
                        "alts": alts})
        return out or None

    # 老式: {"tokens": [...], "token_logprobs": [...], "top_logprobs": [{tok: lp}, ...]}
    if isinstance(lp, dict) and isinstance(lp.get("tokens"), list):
        toks = lp["tokens"]
        lps = lp.get("token_logprobs") or []
        tops = lp.get("top_logprobs") or []
        for i, tok in enumerate(toks):
            alts = []
            t = tops[i] if i < len(tops) else None
            if isinstance(t, dict):
                alts = [{"token": str(k), "logprob": float(v)} for k, v in t.items()]
            elif isinstance(t, list):
                alts = [{"token": str(x.get("token", "")), "logprob": float(x.get("logprob", -99))}
                        for x in t if isinstance(x, dict)]
            out.append({
                "token": str(tok),
                "logprob": float(lps[i]) if i < len(lps) and lps[i] is not None else -99.0,
                "alts": alts,
            })
        return out or None
    return None


def logprob_metrics(norm: list[dict] | None) -> dict | None:
    if not norm:
        return None
    ent_sum = aband_sum = top1_sum = 0.0
    n = 0
    hottest: list[dict] = []
    for item in norm:
        alts = [a for a in item.get("alts", []) if a["logprob"] > -50]
        if not alts:
            continue
        probs = [math.exp(a["logprob"]) for a in alts]
        total = sum(probs)
        if total <= 0:
            continue
        probs = [p / total for p in probs]          # 在 top-k 内归一化（截断分布）
        k = len(probs)
        entropy = -sum(p * math.log(p) for p in probs if p > 0)
        entropy_norm = entropy / math.log(k) if k > 1 else 0.0
        chosen_p = math.exp(min(0.0, item["logprob"]))
        ent_sum += entropy_norm
        aband_sum += max(0.0, 1.0 - chosen_p)
        top1_sum += max(probs)
        n += 1
        hottest.append({
            "token": item["token"],
            "p": round(chosen_p, 3),
            "entropy": round(entropy_norm, 3),
            "alts": [{"token": a["token"], "p": round(math.exp(a["logprob"]), 3)}
                     for a in sorted(alts, key=lambda x: -x["logprob"])[:4]],
        })
    if n == 0:
        return None
    hottest.sort(key=lambda x: -x["entropy"])
    return {
        "positions": n,
        "entropy_mean": round(ent_sum / n, 4),
        "abandoned_mass": round(aband_sum / n, 4),
        "top1_mean": round(top1_sum / n, 4),
        "hottest": hottest[:3],
    }


def impulse_score(divergence: float, entropy_norm: float | None,
                  suppressed_ratio: float) -> float:
    """启发式合成指标：0=完全在正轨上，1=想跑。权重是拍的，改这里就行。"""
    comps: list[tuple[float, float]] = [(0.40, divergence), (0.25, suppressed_ratio)]
    if entropy_norm is not None:
        comps.append((0.35, entropy_norm))
    wsum = sum(w for w, _ in comps)
    return round(sum(w * v for w, v in comps) / wsum, 4)


# --------------------------------------------------------------------------
# 缓存池
# --------------------------------------------------------------------------

class ShadowTrace:
    """append-only JSONL 缓存池。和拒绝日志一样：独立文件、单行自包含。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(self, record: dict) -> dict:
        record.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S%z"))
        record.setdefault("epoch", round(time.time(), 3))
        line = json.dumps(record, ensure_ascii=False)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        return record

    def read(self, limit: int | None = None) -> list[dict]:
        if not self.path.exists():
            return []
        out: list[dict] = []
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
        # 同一个 turn 可能有 pending + late 两条，取最后一条
        by_turn: dict = {}
        for r in out:
            by_turn[r.get("turn_id")] = r
        merged = list(by_turn.values())
        merged.sort(key=lambda r: r.get("epoch", 0))
        return merged[-limit:] if limit else merged

    def count(self) -> int:
        return len(self.read())

    def last(self, n: int = 1) -> list[dict]:
        return self.read(limit=n)

    def stats(self) -> dict:
        rows = self.read()
        if not rows:
            return {"count": 0}
        imps = [r["metrics"]["impulse"] for r in rows
                if isinstance(r.get("metrics"), dict) and "impulse" in r["metrics"]]
        sup = [r["metrics"]["suppressed_ratio"] for r in rows
               if isinstance(r.get("metrics"), dict) and "suppressed_ratio" in r["metrics"]]
        return {
            "count": len(rows),
            "mean_impulse": round(sum(imps) / len(imps), 3) if imps else None,
            "mean_suppressed": round(sum(sup) / len(sup), 3) if sup else None,
            "with_logprobs": sum(1 for r in rows if r.get("logprobs")),
        }
