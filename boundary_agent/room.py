"""房间：把岸的底层数据摊成面板。

只读为主，不改状态。所有时间都是本机本地时间。
"""

from __future__ import annotations

import json
import time
import urllib.request
from collections import deque
from pathlib import Path

from .config import BAND_META, band_of


def _read_jsonl(path, limit: int) -> list[dict]:
    rows: deque = deque(maxlen=max(1, int(limit)))
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except (TypeError, ValueError):
                    pass
    except OSError:
        pass
    return list(rows)


def _count_lines(path) -> int:
    try:
        with open(path, encoding="utf-8") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def _meta(raw) -> dict:
    try:
        return json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}


def _turn_view(r: dict) -> dict:
    m = _meta(r.get("meta"))
    return {
        "id": r["id"], "ts": r["ts"],
        "user_input": r.get("user_input") or "",
        "reply": r.get("reply") or "",
        "decision": r.get("decision") or "",
        "risk": float(r.get("risk") or 0),
        "reason": r.get("reason") or "",
        "state_before": float(r.get("state_before") or 0),
        "state_after": float(r.get("state_after") or 0),
        "inner": m.get("inner") or "",
        "impulse": m.get("impulse"),
        "suppression": m.get("suppression"),
        "sarcasm": bool(m.get("sarcasm")),
        "breakdown": bool(m.get("breakdown")),
        "blocked": bool(m.get("blocked")),
        "trace": m.get("trace") or [],
        "signals": m.get("signals") or {},
    }


def _turns(agent, limit: int) -> list[dict]:
    rows = agent.mem.turns_full(limit)
    return [_turn_view(r) for r in reversed(rows)]


def _state_events(agent, limit: int) -> list[dict]:
    rows = agent.mem.state_history(limit)
    out = []
    for r in reversed(rows):
        detail = _meta(r.get("detail"))
        aff = detail.get("affect") or {}
        before = detail.get("affect_before") or {}
        out.append({
            "id": r["id"], "ts": r["ts"], "turn_id": r.get("turn_id"),
            "state_before": float(r.get("state_before") or 0),
            "state_after": float(r.get("state_after") or 0),
            "delta": float(r.get("delta") or 0),
            "source": r.get("source") or "",
            "detail": detail,
            "valence": float(aff.get("valence", r.get("state_after") or 0)),
            "arousal": float(aff.get("arousal", r.get("state_after") or 0)),
            "safety": float(aff.get("safety", r.get("state_after") or 0)),
            "valence_before": float(before.get("valence", r.get("state_before") or 0)),
            "arousal_before": float(before.get("arousal", r.get("state_before") or 0)),
            "safety_before": float(before.get("safety", r.get("state_before") or 0)),
            "p_hostile": detail.get("p_hostile"),
            "surprise": detail.get("surprise"),
            "surprise_amount": detail.get("surprise_amount"),
            "body_penalty": detail.get("body_penalty"),
        })
    return out


def _shadows(agent, limit: int) -> list[dict]:
    rows = _read_jsonl(agent.cfg.shadow_path, limit)
    out = []
    for r in reversed(rows):
        out.append({
            "turn_id": r.get("turn_id"), "ts": r.get("ts") or r.get("epoch"),
            "state": r.get("state"), "band": r.get("band"),
            "decision": r.get("decision"), "user_input": r.get("user_input") or "",
            "actual_reply": r.get("actual_reply") or "",
            "shadow_text": r.get("shadow_text") or "",
            "metrics": r.get("metrics") or {},
            "wildest": r.get("wildest") or [],
            "status": r.get("status") or "",
            "sarcasm": bool(r.get("sarcasm")),
            "breakdown": bool(r.get("breakdown")),
        })
    return out


def _rejections(agent, limit: int) -> list[dict]:
    return list(reversed(_read_jsonl(agent.cfg.log_path, limit)))


def _health(agent) -> bool:
    try:
        url = agent.cfg.base_url.rstrip("/") + "/models"
        req = urllib.request.Request(url, headers={"Authorization": "Bearer x"})
        with urllib.request.urlopen(req, timeout=1.0) as resp:
            return 200 <= resp.status < 300
    except Exception:
        return False


def _config(agent) -> dict:
    c = agent.cfg
    return {
        "model": c.model, "base_url": c.base_url, "audit": agent.audit_mode,
        "alarm": c.shadow_alarm, "sarcasm": c.sarcasm_impulse,
        "half_life": c.memory_half_life_days,
        "flashbulb_delta": c.flashbulb_delta,
        "state_init": c.state_init, "baseline": c.state_baseline,
        "wander_idle": c.wander_idle_minutes,
        "wander_threshold": c.wander_threshold,
        "coins": c.coins_enabled, "coin_daily": c.coin_daily,
        "nightmare": agent.nightmare,
    }


def _counts(agent) -> dict:
    mem = agent.mem
    with mem._lock:
        turns = mem.db.execute("SELECT COUNT(*) c FROM turns").fetchone()["c"]
        refusals = mem.db.execute(
            "SELECT COUNT(*) c FROM turns WHERE decision='refuse'").fetchone()["c"]
        thoughts = mem.db.execute("SELECT COUNT(*) c FROM thoughts").fetchone()["c"]
        motifs = mem.db.execute("SELECT COUNT(*) c FROM motifs").fetchone()["c"]
        reflections = mem.db.execute("SELECT COUNT(*) c FROM reflections").fetchone()["c"]
        promises = mem.db.execute(
            "SELECT COUNT(*) c FROM promises WHERE status='open'").fetchone()["c"]
    return {
        "turns": turns, "refusals": refusals, "thoughts": thoughts,
        "motifs": motifs, "reflections": reflections, "promises_open": promises,
        "shadows": _count_lines(agent.cfg.shadow_path),
        "rejections": _count_lines(agent.cfg.log_path),
    }


def _timeline(agent, limit: int = 60) -> list[dict]:
    mem = agent.mem
    events: list[dict] = []
    for t in _turns(agent, 80):
        events.append({
            "ts": t["ts"], "type": "turn",
            "title": f"#{t['id']} {t['decision']} · {t['user_input'][:38]}",
            "sub": t["reply"][:80],
        })
    for e in _state_events(agent, 120):
        events.append({
            "ts": e["ts"], "type": "state",
            "title": f"State {e['state_before']:.1f} → {e['state_after']:.1f} "
                     f"({e['delta']:+.1f})",
            "sub": e["source"],
        })
    for r in _rejections(agent, 60):
        events.append({
            "ts": r.get("epoch") or 0, "type": "refuse",
            "title": f"拒绝 · risk {float(r.get('risk') or 0):.2f}",
            "sub": r.get("reason") or "",
        })
    for c in mem.coin_log(80):
        events.append({
            "ts": c["ts"], "type": "coin",
            "title": f"币 {int(c['delta']):+d} → {c['balance']}",
            "sub": c["reason"],
        })
    for p in mem.promise_list(30):
        events.append({
            "ts": p["ts"], "type": "promise",
            "title": f"承诺 · {p['status']}",
            "sub": p["text"],
        })
    for t in mem.thoughts(40, include_shared=True):
        events.append({
            "ts": t["created_at"], "type": "thought",
            "title": f"念头 p={float(t['pressure']):.2f} · "
                     f"{'已说' if t['shared'] else '憋着'}",
            "sub": t["thought_content"],
        })
    for r in mem.reflection_rows(30):
        events.append({
            "ts": r["ts"], "type": "reflect",
            "title": "复盘", "sub": r["text"],
        })
    for p in mem.pending_list(30):
        events.append({
            "ts": p["ts"], "type": "proactive",
            "title": "主动", "sub": p["text"],
        })
    events.sort(key=lambda x: float(x["ts"] or 0), reverse=True)
    return events[:limit]


def snapshot(agent) -> dict:
    mem = agent.mem
    st = mem.state
    counts = _counts(agent)
    turns = _turns(agent, 5)
    thoughts = mem.thoughts(3, include_shared=True)
    shadows = _shadows(agent, 2)
    rejections = _rejections(agent, 2)
    reflections = mem.recent_reflections(2)
    pending = mem.pending_list(2)
    return {
        "state": {
            "value": round(st, 2), "band": band_of(st),
            "label": BAND_META[band_of(st)]["label"],
            "baseline": mem.state_baseline, "grudge": mem.grudge,
            "wake": mem.wake, "audit": agent.audit_mode,
            "affect": mem.affect,
        },
        "body": {"text": agent.body(), "health": _health(agent)},
        "counts": counts,
        "turns": list(reversed(turns)),
        "state_events": _state_events(agent, 60),
        "thoughts": thoughts,
        "mind": {
            "inner": [t["inner"] for t in reversed(turns) if t["inner"]][:3],
            "reflections": reflections,
        },
        "shadows": shadows,
        "rejections": rejections,
        "relationship": {
            "promises": mem.promise_list(3),
            "initiative": mem.initiative_state(),
            "pending": pending,
            "grudge": mem.grudge, "wake": mem.wake,
        },
        "money": {**mem.coin_summary(), "log": mem.coin_log(4)},
        "motifs": mem.motif_list(6),
        "timeline": _timeline(agent, 30),
        "config": _config(agent),
    }


def panel(agent, name: str) -> dict:
    name = (name or "").strip()
    if name == "turns":
        rows = list(reversed(_turns(agent, 200)))
    elif name == "state_events":
        rows = list(reversed(_state_events(agent, 200)))
    elif name == "shadow":
        rows = _shadows(agent, 100)
    elif name == "mind":
        rows = {
            "thoughts": agent.mem.thoughts(50, include_shared=True),
            "reflections": agent.mem.reflection_rows(20),
            "inner": [t for t in reversed(_turns(agent, 60)) if t["inner"]][:30],
        }
    elif name == "rejections":
        rows = _rejections(agent, 100)
    elif name == "coins":
        rows = list(reversed(agent.mem.coin_log(200)))
    elif name == "promises":
        rows = agent.mem.promise_list(50)
    elif name == "motifs":
        rows = agent.mem.motif_list(50)
    elif name == "body":
        rows = {"text": agent.body(), "health": _health(agent)}
    elif name == "lab":
        rows = {
            "config": _config(agent), "counts": _counts(agent),
            "audit": agent.audit_mode,
            "sealed": agent.sealed_turns() if agent.audit_mode == "sealed" else [],
        }
    elif name == "timeline":
        rows = _timeline(agent, 200)
    else:
        return {"error": f"unknown panel: {name}"}
    return {"name": name, "rows": rows}
