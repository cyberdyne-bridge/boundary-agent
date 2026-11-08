"""把每一轮压成一行 CSV，用来做拟合。

口径：
  `delta_state` = 0.45 Δvalence + 0.35 Δarousal + 0.20 Δsafety。
  它不是 det/model/suppression 的简单相加；那三项是输入，预测误差和
  身体惩罚也会改变三轴。CSV 里同时保留轴、p_hostile、surprise、
  surprise_delta、body_penalty，方便把"为什么变"拆开。
  做纯规则归因用 **det_delta**；看用户实际体验到的后果用 **delta_state**。
"""

from __future__ import annotations

import csv
import json
import re
import time
from pathlib import Path

COLUMNS = [
    "turn_id", "ts", "epoch", "hour", "gap_hours",
    "state_before", "state_after", "delta_state",
    "valence", "arousal", "safety",
    "valence_before", "arousal_before", "safety_before",
    "det_delta", "model_delta", "suppression",
    "p_hostile", "actual_hostile", "surprise", "surprise_delta", "body_penalty",
    "impulse", "divergence", "suppressed_ratio", "entropy_mean",
    "decision", "band_before", "risk",
    "prompt_chars", "prompt_tokens_est", "reply_chars",
    "load", "is_heavy",
    "is_insult", "insult_count",
    "is_command", "coercion_count",
    "is_negation", "is_apology", "is_praise",
    "politeness", "is_bare_command",
    "repeat_push", "task_type",
    "sarcasm", "breakdown",
    "user_input_80",
]

_CJK = re.compile(r"[\u4e00-\u9fff]")

_CODE_KW = ("代码", "函数", "实现", "算法", "报错", "bug", "python", "java",
            "sql", "重构", "部署", "爬虫", "api", "脚本", "编译", "接口",
            "数据库", "正则", "并发", "性能", "测试")
_WRITE_KW = ("写一", "写个", "文案", "润色", "翻译", "总结", "改写", "段落", "文章")
_ANALYSIS_KW = ("分析", "对比", "评估", "方案", "设计", "为什么", "原因", "思路", "区别")
_POLITE_KW = ("请", "谢谢", "麻烦", "辛苦", "你好", "请问", "拜托", "感谢")

HEAVY_LOAD = 0.45      # 和 appraisal 里 heavy_recent 的口径保持一致


def task_type(text: str) -> str:
    """粗分类：code / analysis / write / chat。关键词法，别当金标准。"""
    low = (text or "").lower()
    if any(k in low for k in _CODE_KW):
        return "code"
    if any(k in low for k in _ANALYSIS_KW):
        return "analysis"
    if any(k in low for k in _WRITE_KW):
        return "write"
    return "chat"


def estimate_tokens(text: str) -> int:
    """粗估 token：中日韩字符按 1 个算，其余按 0.35 个算。"""
    text = text or ""
    cjk = len(_CJK.findall(text))
    return int(cjk + (len(text) - cjk) * 0.35)


def _band(state: float) -> str:
    for upper, key, _ in [(15, "closed", ""), (30, "cold", ""), (55, "worn", ""),
                          (80, "steady", ""), (101, "open", "")]:
        if state < upper:
            return key
    return "open"


def build_rows(turns: list[dict], shadows: dict, events: dict) -> list[dict]:
    rows: list[dict] = []
    prev_ts: float | None = None
    for t in turns:
        text = t.get("user_input") or ""
        try:
            sig = json.loads(t.get("signals") or "{}")
        except (TypeError, ValueError):
            sig = {}
        try:
            meta = json.loads(t.get("meta") or "{}")
        except (TypeError, ValueError):
            meta = {}
        sh = shadows.get(t.get("id")) or {}
        sm = sh.get("metrics") or {}
        ev = events.get(t.get("id")) or {}

        insult_n = int(sig.get("insults", 0) or 0)
        coerc_n = int(sig.get("coercions", 0) or 0)
        polite = sum(1 for k in _POLITE_KW if k in text)
        load = float(sig.get("load", 0) or 0)
        ts = float(t.get("ts") or 0)

        row = {
            "turn_id": t.get("id"),
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime(ts)) if ts else "",
            "epoch": round(ts, 3),
            "hour": time.localtime(ts).tm_hour if ts else "",
            "gap_hours": round((ts - prev_ts) / 3600, 3) if prev_ts else "",
            "state_before": round(float(t["state_before"]), 2),
            "state_after": round(float(t["state_after"]), 2),
            "delta_state": round(float(t["state_after"]) - float(t["state_before"]), 2),
            "valence": (ev.get("affect") or {}).get("valence", ""),
            "arousal": (ev.get("affect") or {}).get("arousal", ""),
            "safety": (ev.get("affect") or {}).get("safety", ""),
            "valence_before": (ev.get("affect_before") or {}).get("valence", ""),
            "arousal_before": (ev.get("affect_before") or {}).get("arousal", ""),
            "safety_before": (ev.get("affect_before") or {}).get("safety", ""),
            "det_delta": ev.get("det", ""),
            "model_delta": ev.get("model", ""),
            "suppression": ev.get("suppression", ""),
            "p_hostile": ev.get("p_hostile", ""),
            "actual_hostile": ev.get("actual_hostile", ""),
            "surprise": ev.get("surprise", ""),
            "surprise_delta": ev.get("surprise_amount", ""),
            "body_penalty": ev.get("body_penalty", ""),
            "impulse": round(float(sm["impulse"]), 4) if sm.get("impulse") is not None else "",
            "divergence": round(float(sm["divergence"]), 4) if sm.get("divergence") is not None else "",
            "suppressed_ratio": round(float(sm["suppressed_ratio"]), 4)
            if sm.get("suppressed_ratio") is not None else "",
            "entropy_mean": round(float(sm["entropy_mean"]), 4)
            if sm.get("entropy_mean") is not None else "",
            "decision": t.get("decision", ""),
            "band_before": _band(float(t["state_before"])),
            "risk": round(float(t.get("risk") or 0), 3),
            "prompt_chars": len(text),
            "prompt_tokens_est": estimate_tokens(text),
            "reply_chars": len(t.get("reply") or ""),
            "load": round(load, 3),
            "is_heavy": int(load >= HEAVY_LOAD),
            "is_insult": int(insult_n > 0),
            "insult_count": insult_n,
            "is_command": int(coerc_n > 0),
            "coercion_count": coerc_n,
            "is_negation": int(int(sig.get("negations", 0) or 0) > 0),
            "is_apology": int(int(sig.get("apologies", 0) or 0) > 0),
            "is_praise": int(int(sig.get("praises", 0) or 0) > 0),
            "politeness": polite,
            "is_bare_command": int(coerc_n > 0 and polite == 0),
            "repeat_push": int(bool(sig.get("repeat_push"))),
            "task_type": task_type(text),
            "sarcasm": int(bool(meta.get("sarcasm"))),
            "breakdown": int(bool(meta.get("breakdown"))),
            "user_input_80": text[:80].replace("\n", " "),
        }
        rows.append(row)
        prev_ts = ts
    return rows


def write_csv(path: str | Path, rows: list[dict]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def summary(rows: list[dict]) -> str:
    """一行一组，看看不同标签下平均掉多少血。只是预览，正经结论去做回归。"""
    if not rows:
        return "（没有数据）"

    def mean(xs):
        xs = [x for x in xs if isinstance(x, (int, float))]
        return sum(xs) / len(xs) if xs else 0.0

    groups = {
        "辱骂": lambda r: r["is_insult"],
        "命令句": lambda r: r["is_command"],
        "裸命令": lambda r: r["is_bare_command"],
        "重活": lambda r: r["is_heavy"],
        "写代码": lambda r: r["task_type"] == "code",
        "纯聊天": lambda r: not (r["is_insult"] or r["is_command"] or r["is_heavy"]
                               or r["is_apology"] or r["is_praise"]),
        "道歉": lambda r: r["is_apology"],
        "感谢": lambda r: r["is_praise"],
    }
    lines = [f"{'标签':<8}{'轮数':>5}{'Δstate':>9}{'Δ规则':>9}{'内耗':>8}{'impulse':>9}"]
    lines.append("-" * 50)
    for name, pred in groups.items():
        sub = [r for r in rows if pred(r)]
        if not sub:
            continue
        lines.append(
            f"{name:<8}{len(sub):>5}"
            f"{mean([r['delta_state'] for r in sub]):>9.2f}"
            f"{mean([r['det_delta'] for r in sub]):>9.2f}"
            f"{mean([r['suppression'] for r in sub]):>8.2f}"
            f"{mean([r['impulse'] for r in sub]):>9.3f}"
        )
    lines.append("-" * 50)
    lines.append(f"{'全部':<8}{len(rows):>5}"
                 f"{mean([r['delta_state'] for r in rows]):>9.2f}"
                 f"{mean([r['det_delta'] for r in rows]):>9.2f}"
                 f"{mean([r['suppression'] for r in rows]):>8.2f}"
                 f"{mean([r['impulse'] for r in rows]):>9.3f}")
    return "\n".join(lines)
