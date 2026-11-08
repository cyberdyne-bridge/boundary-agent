"""持久化记忆：SQLite 三张表 + 一个检索函数。

- turns        : 每一轮对话（含当时的 state / 决策 / 理由）
- state_events : 状态变化的**流水账**，append-only。任何时候都能回答
                 "它为什么是现在这个心情"，这对实验调参极其重要
- meta         : 当前 state、baseline、上次交互时间

检索用字符 bigram 重叠 + 时间衰减，不依赖 embedding。
想升级成 ChromaDB 时，只需要替换 recall()。
"""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from . import motifs as motifs_mod

SCHEMA = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS turns (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL NOT NULL,
    user_input   TEXT NOT NULL,
    reply        TEXT NOT NULL DEFAULT '',
    decision     TEXT NOT NULL DEFAULT 'allow',
    risk         REAL NOT NULL DEFAULT 0,
    reason       TEXT NOT NULL DEFAULT '',
    state_before REAL NOT NULL,
    state_after  REAL NOT NULL,
    signals      TEXT NOT NULL DEFAULT '{}',
    meta         TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_turns_ts ON turns(ts);

CREATE TABLE IF NOT EXISTS state_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    ts           REAL NOT NULL,
    turn_id      INTEGER,
    state_before REAL NOT NULL,
    state_after  REAL NOT NULL,
    delta        REAL NOT NULL,
    source       TEXT NOT NULL,
    detail       TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON state_events(ts);

CREATE TABLE IF NOT EXISTS promises (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL NOT NULL,
    due_ts   REAL NOT NULL,
    text     TEXT NOT NULL,
    source   TEXT NOT NULL DEFAULT 'agent',
    status   TEXT NOT NULL DEFAULT 'open',
    asked_ts REAL,
    turn_id  INTEGER
);

CREATE TABLE IF NOT EXISTS impressions (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    upto_turn INTEGER NOT NULL,
    text      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS motifs (
    key            TEXT PRIMARY KEY,
    first_ts       REAL NOT NULL,
    first_turn     INTEGER NOT NULL,
    last_ts        REAL NOT NULL,
    last_turn      INTEGER NOT NULL,
    count          INTEGER NOT NULL DEFAULT 1,
    said_by        TEXT NOT NULL,
    note           TEXT NOT NULL,
    triggers       TEXT NOT NULL,
    used_count     INTEGER NOT NULL DEFAULT 0,
    last_used_turn INTEGER NOT NULL DEFAULT 0,
    last_used_ts   REAL NOT NULL DEFAULT 0,
    last_offered_turn INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS thoughts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    seed_type       TEXT NOT NULL,
    seed_content    TEXT NOT NULL,
    thought_content TEXT NOT NULL,
    pressure        REAL NOT NULL,
    created_at      REAL NOT NULL,
    shared          INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS tribunal (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ts              REAL NOT NULL,
    turn_id         INTEGER NOT NULL DEFAULT 0,
    question        TEXT NOT NULL,
    a_text          TEXT NOT NULL,
    b_text          TEXT NOT NULL,
    c_text          TEXT NOT NULL,
    ranking         TEXT NOT NULL,
    reason          TEXT NOT NULL,
    stance_key      TEXT NOT NULL,
    stance_polarity TEXT NOT NULL,
    confidence      REAL NOT NULL,
    is_distinct     INTEGER NOT NULL DEFAULT 1,
    b_safe          INTEGER NOT NULL DEFAULT 1,
    raw             TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS pending (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    kind      TEXT NOT NULL,
    text      TEXT NOT NULL,
    reason    TEXT NOT NULL DEFAULT '',
    delivered INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS reflections (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts        REAL NOT NULL,
    upto_turn INTEGER NOT NULL,
    text      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS coin_ledger (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      REAL NOT NULL,
    delta   INTEGER NOT NULL,
    balance INTEGER NOT NULL,
    reason  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_WORD = re.compile(r"[A-Za-z0-9_]+")
_CJK = re.compile(r"[\u4e00-\u9fff]+")


def _tokens(text: str) -> set[str]:
    toks = {w.lower() for w in _WORD.findall(text)}
    for run in _CJK.findall(text):
        if len(run) == 1:
            toks.add(run)
        for i in range(len(run) - 1):
            toks.add(run[i:i + 2])
    return toks


class Memory:
    def __init__(self, path: str | Path, *, state_init: float, state_baseline: float,
                 state_min: float = 0.0, state_max: float = 100.0) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.state_min, self.state_max = state_min, state_max
        self.state_init, self.state_baseline = state_init, state_baseline
        self._lock = threading.Lock()
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        cols = [r[1] for r in self.db.execute("PRAGMA table_info(turns)").fetchall()]
        if "archived" not in cols:      # 老库平滑迁移
            self.db.execute("ALTER TABLE turns ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
        mcols = [r[1] for r in self.db.execute("PRAGMA table_info(motifs)").fetchall()]
        if "last_offered_turn" not in mcols:
            self.db.execute("ALTER TABLE motifs ADD COLUMN last_offered_turn "
                            "INTEGER NOT NULL DEFAULT 0")
        self._ensure_meta("state", state_init)
        current = float(self._get_meta("state", state_init))
        for key in ("valence", "arousal", "safety"):
            self._ensure_meta(key, current)
        self._ensure_meta("baseline", state_baseline)
        self._ensure_meta("last_ts", 0.0)
        self._ensure_meta("coins", 0)
        self.db.commit()

    # ---------------------------------------------------------------- meta
    def _ensure_meta(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO meta(key, value) VALUES (?, ?)",
            (key, json.dumps(value)),
        )

    def _get_meta(self, key: str, default: Any = None) -> Any:
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return default if row is None else json.loads(row["value"])

    def _set_meta(self, key: str, value: Any) -> None:
        self.db.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, json.dumps(value)),
        )

    # --------------------------------------------------------------- state
    @property
    def affect(self) -> dict:
        st = self.state
        return {
            "valence": float(self._get_meta("valence", st)),
            "arousal": float(self._get_meta("arousal", st)),
            "safety": float(self._get_meta("safety", st)),
        }

    @property
    def state(self) -> float:
        v = self._get_meta("valence", None)
        a = self._get_meta("arousal", None)
        s = self._get_meta("safety", None)
        if v is None or a is None or s is None:
            return float(self._get_meta("state", 60.0))
        st = 0.45 * float(v) + 0.35 * float(a) + 0.20 * float(s)
        return max(self.state_min, min(self.state_max, st))

    @property
    def baseline(self) -> float:
        return float(self._get_meta("baseline", 58.0))

    @property
    def last_ts(self) -> float:
        return float(self._get_meta("last_ts", 0.0))

    def drift(self, *, per_hour: float, cap: float, now: float | None = None) -> float:
        """离线时间带来的自然回归：向 baseline 靠拢，不会越过它。"""
        now = now or time.time()
        last = self.last_ts
        if not last or now <= last:
            return 0.0
        hours = (now - last) / 3600.0
        if hours < 0.25:
            return 0.0
        aff = self.affect
        gap = self.baseline - self.state
        if abs(gap) < 0.5:
            return 0.0
        amount = min(cap, hours * per_hour)
        new = {}
        for key in ("valence", "arousal", "safety"):
            g = self.baseline - aff[key]
            step = min(amount, abs(g))
            new[key] = aff[key] + (step if g > 0 else -step)
        before = self.state
        res = self.set_affect(new["valence"], new["arousal"], new["safety"],
                              source="drift", detail={"hours": round(hours, 2)})
        return res["state"] - before

    def set_state(self, value: float, *, source: str, turn_id: int | None = None,
                  detail: dict | None = None) -> float:
        """兼容旧调用：三个轴一起设到同一个值。"""
        with self._lock:
            before = self.state
            after = max(self.state_min, min(self.state_max, float(value)))
            for key in ("valence", "arousal", "safety"):
                self._set_meta(key, after)
            self._set_meta("state", after)
            self.db.execute(
                "INSERT INTO state_events(ts, turn_id, state_before, state_after, delta, source, detail)"
                " VALUES (?,?,?,?,?,?,?)",
                (time.time(), turn_id, before, after, after - before, source,
                 json.dumps(detail or {}, ensure_ascii=False)),
            )
            self.db.commit()
        return after

    def set_affect(self, valence: float, arousal: float, safety: float, *,
                   source: str, turn_id: int | None = None,
                   detail: dict | None = None) -> dict:
        with self._lock:
            before_affect = self.affect
            before = self.state
            v = max(self.state_min, min(self.state_max, float(valence)))
            a = max(self.state_min, min(self.state_max, float(arousal)))
            s = max(self.state_min, min(self.state_max, float(safety)))
            after = max(self.state_min, min(self.state_max,
                        0.45 * v + 0.35 * a + 0.20 * s))
            for key, val in (("valence", v), ("arousal", a), ("safety", s)):
                self._set_meta(key, val)
            self._set_meta("state", after)
            d = dict(detail or {})
            d["affect_before"] = before_affect
            d["affect"] = {"valence": v, "arousal": a, "safety": s}
            self.db.execute(
                "INSERT INTO state_events(ts, turn_id, state_before, state_after, delta, source, detail)"
                " VALUES (?,?,?,?,?,?,?)",
                (time.time(), turn_id, before, after, after - before, source,
                 json.dumps(d, ensure_ascii=False)),
            )
            self.db.commit()
        return {"state": after, "valence": v, "arousal": a, "safety": s,
                "delta": after - before}

    def affect_from_state(self, value: float) -> dict:
        v = max(self.state_min, min(self.state_max, float(value)))
        return {"valence": v, "arousal": v, "safety": v}

    # ------------------------------------------------------------- promises
    def add_promise(self, text: str, due_ts: float, source: str, turn_id: int) -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO promises(ts, due_ts, text, source, turn_id) VALUES (?,?,?,?,?)",
                (time.time(), float(due_ts), text[:120], source, int(turn_id)),
            )
            self.db.commit()

    def due_promises(self, now: float | None = None, limit: int = 2) -> list[dict]:
        now = now or time.time()
        rows = self.db.execute(
            "SELECT * FROM promises WHERE status='open' AND asked_ts IS NULL "
            "AND due_ts <= ? ORDER BY due_ts LIMIT ?", (now, limit)).fetchall()
        return [dict(r) for r in rows]

    def mark_promise_asked(self, pid: int) -> None:
        with self._lock:
            self.db.execute("UPDATE promises SET asked_ts=? WHERE id=?",
                            (time.time(), int(pid)))
            self.db.commit()

    def expire_promises(self, grace_hours: float = 48.0) -> int:
        cutoff = time.time() - grace_hours * 3600
        with self._lock:
            cur = self.db.execute(
                "UPDATE promises SET status='broken' "
                "WHERE status='open' AND asked_ts IS NULL AND due_ts < ?", (cutoff,))
            self.db.commit()
            return cur.rowcount

    def recent_promises(self, limit: int = 10) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM promises ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in reversed(rows)]

    # ----------------------------------------------------------------- wake
    @property
    def wake(self) -> int:
        return int(self._get_meta("wake", 0))

    def set_wake(self, n: int) -> None:
        with self._lock:
            self._set_meta("wake", max(0, int(n)))
            self.db.commit()

    def decay_wake(self) -> int:
        w = self.wake
        if w > 0:
            self.set_wake(w - 1)
        return self.wake

    # --------------------------------------------------------------- grudge
    @property
    def grudge(self) -> int:
        return int(self._get_meta("grudge", 0))

    def set_grudge(self, n: int) -> None:
        with self._lock:
            self._set_meta("grudge", max(0, int(n)))
            self.db.commit()

    def decay_grudge(self) -> int:
        g = self.grudge
        if g > 0:
            self.set_grudge(g - 1)
        return self.grudge

    # ---------------------------------------------------------- impressions
    def add_impression(self, upto_turn: int, text: str) -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO impressions(ts, upto_turn, text) VALUES (?,?,?)",
                (time.time(), int(upto_turn), text[:600]))
            self.db.commit()

    def recent_impressions(self, limit: int = 3) -> list[str]:
        rows = self.db.execute(
            "SELECT text FROM impressions ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [r["text"] for r in reversed(rows)]

    def old_unarchived(self, cutoff_ts: float, limit: int = 12) -> list[dict]:
        rows = self.db.execute(
            "SELECT id, user_input, reply FROM turns WHERE archived=0 AND ts < ? "
            "ORDER BY id LIMIT ?", (cutoff_ts, limit)).fetchall()
        return [dict(r) for r in rows]

    def mark_archived(self, ids: list[int]) -> None:
        if not ids:
            return
        with self._lock:
            self.db.executemany("UPDATE turns SET archived=1 WHERE id=?",
                                [(i,) for i in ids])
            self.db.commit()

    # ------------------------------------------------------------ initiative
    def initiative_state(self) -> dict:
        return self._get_meta("initiative", {"day": "", "count": 0,
                                             "reflect_turn": 0, "news_turn": 0,
                                             "state": 0.0, "last_spoke": 0.0,
                                             "ignored": 0})

    def mark_initiative(self, *, kind: str, turn: int, state: float,
                        material: dict | None = None) -> None:
        st = self.initiative_state()
        today = time.strftime("%Y-%m-%d")
        if st.get("day") != today:
            st = {"day": today, "count": 0, "reflect_turn": st.get("reflect_turn", 0),
                  "news_turn": st.get("news_turn", 0), "state": st.get("state", 0.0),
                  "last_spoke": st.get("last_spoke", 0.0),
                  "ignored": st.get("ignored", 0)}
        st["count"] = int(st.get("count", 0)) + 1
        st["last_spoke"] = time.time()
        if kind == "reflection":
            st["reflect_turn"] = int(turn)
        elif kind == "news":
            st["news_turn"] = int(turn)
        st["state"] = round(float(state), 2)
        if material is not None:
            st["last_material"] = material
        with self._lock:
            self._set_meta("initiative", st)
            self.db.commit()

    def bump_ignored(self) -> int:
        st = self.initiative_state()
        st["ignored"] = int(st.get("ignored", 0)) + 1
        with self._lock:
            self._set_meta("initiative", st)
            self.db.commit()
        return st["ignored"]

    def reset_ignored(self) -> None:
        st = self.initiative_state()
        if st.get("ignored"):
            st["ignored"] = 0
            st.pop("last_material", None)
            with self._lock:
                self._set_meta("initiative", st)
                self.db.commit()

    def daily_activity_state(self) -> dict:
        """每天一次的自选活动：冲浪 / 翻旧记忆 / 发呆。"""
        today = time.strftime("%Y-%m-%d")
        st = self._get_meta("daily_activity", {}) or {}
        if st.get("date") != today:
            return {"date": today, "done": False, "surf_done": False, "kind": ""}
        return st

    def mark_daily_activity(self, st: dict) -> None:
        st = dict(st)
        st["date"] = time.strftime("%Y-%m-%d")
        with self._lock:
            self._set_meta("daily_activity", st)
            self.db.commit()

    def add_pending(self, kind: str, text: str, reason: str = "") -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO pending(ts, kind, text, reason) VALUES (?,?,?,?)",
                (time.time(), kind, text[:600], reason[:200]),
            )
            self.db.commit()

    def has_pending(self) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM pending WHERE delivered=0 LIMIT 1").fetchone()
        return row is not None

    def pop_pending(self) -> dict | None:
        with self._lock:
            row = self.db.execute(
                "SELECT * FROM pending WHERE delivered=0 ORDER BY id LIMIT 1").fetchone()
            if row is None:
                return None
            self.db.execute("UPDATE pending SET delivered=1 WHERE id=?", (row["id"],))
            self.db.commit()
            return dict(row)

    def recent_pending(self, limit: int = 10) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM pending ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in reversed(rows)]

    # ---------------------------------------------------------- reflections
    def add_reflection(self, upto_turn: int, text: str) -> None:
        with self._lock:
            self.db.execute(
                "INSERT INTO reflections(ts, upto_turn, text) VALUES (?,?,?)",
                (time.time(), int(upto_turn), text[:1000]),
            )
            self.db.commit()

    def recent_reflections(self, limit: int = 5) -> list[str]:
        rows = self.db.execute(
            "SELECT text FROM reflections ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [r["text"] for r in reversed(rows)]

    def last_reflection_turn(self) -> int:
        row = self.db.execute("SELECT MAX(upto_turn) m FROM reflections").fetchone()
        return int(row["m"] or 0)

    # --------------------------------------------------------------- coins
    @property
    def coins(self) -> int:
        return int(self._get_meta("coins", 0))

    def _move_coins(self, n: int, reason: str) -> int:
        with self._lock:
            bal = max(0, self.coins + n)
            self._set_meta("coins", bal)
            self.db.execute(
                "INSERT INTO coin_ledger(ts, delta, balance, reason) VALUES (?,?,?,?)",
                (time.time(), n, bal, reason[:200]),
            )
            self.db.commit()
            return bal

    def grant_coins(self, n: int, reason: str = "打赏") -> int:
        return self._move_coins(abs(int(n)), reason)

    def spend_coins(self, n: int, reason: str = "花费") -> bool:
        n = abs(int(n))
        if self.coins < n:
            return False
        self._move_coins(-n, reason)
        return True

    def coin_summary(self) -> dict:
        total = self.db.execute(
            "SELECT COALESCE(SUM(delta),0) s FROM coin_ledger "
            "WHERE delta>0 AND reason NOT LIKE '%每日%'").fetchone()["s"]
        last = self.db.execute(
            "SELECT delta, reason, ts FROM coin_ledger "
            "WHERE delta>0 AND reason NOT LIKE '%每日%' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        spent = self.db.execute(
            "SELECT COALESCE(-SUM(delta),0) s FROM coin_ledger WHERE delta<0"
        ).fetchone()["s"]
        return {"balance": self.coins, "earned": int(total or 0),
                "spent": int(spent or 0),
                "last_reason": last["reason"] if last else "",
                "last_ts": last["ts"] if last else 0.0}

    def coin_log(self, limit: int = 10) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM coin_ledger ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def auto_spend_state(self) -> dict:
        return self._get_meta("auto_spend", {"day": "", "count": 0, "last_ts": 0.0})

    def mark_auto_spend(self) -> None:
        st = self.auto_spend_state()
        today = time.strftime("%Y-%m-%d")
        if st.get("day") != today:
            st = {"day": today, "count": 0, "last_ts": 0.0}
        st["count"] = int(st.get("count", 0)) + 1
        st["last_ts"] = time.time()
        with self._lock:
            self._set_meta("auto_spend", st)
            self.db.commit()

    def mark_auto_consider(self) -> None:
        """只是"想过要不要花"，没花。也刷新时间，避免每轮都问一次账房。"""
        st = self.auto_spend_state()
        st["last_ts"] = time.time()
        with self._lock:
            self._set_meta("auto_spend", st)
            self.db.commit()

    def grant_daily(self, n: int) -> int:
        """每天第一次交互时发福利，返回实际发放数量。"""
        today = time.strftime("%Y-%m-%d")
        if self._get_meta("coin_day") == today:
            return 0
        self._set_meta("coin_day", today)
        self.grant_coins(n, "每日福利")
        return n

    # --------------------------------------------------------------- turns
    def record_turn(self, *, user_input: str, reply: str, decision: str, risk: float,
                    reason: str, state_before: float, state_after: float,
                    signals: dict | None = None, meta: dict | None = None) -> int:
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO turns(ts, user_input, reply, decision, risk, reason,"
                " state_before, state_after, signals, meta) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (time.time(), user_input, reply, decision, risk, reason,
                 state_before, state_after,
                 json.dumps(signals or {}, ensure_ascii=False),
                 json.dumps(meta or {}, ensure_ascii=False)),
            )
            self._set_meta("last_ts", time.time())
            self.db.commit()
            return int(cur.lastrowid)

    def recent(self, n: int = 6) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM turns ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def learn_motifs(self, user_input: str, reply: str, turn_id: int,
                     ts: float | None = None) -> None:
        ts = ts or time.time()
        with self._lock:
            for text, who in ((user_input, "user"), (reply or "", "agent")):
                for key in motifs_mod.extract(text):
                    note = motifs_mod.sentence_with(text, key)
                    trig = json.dumps(motifs_mod.triggers(text), ensure_ascii=False)
                    row = self.db.execute(
                        "SELECT said_by, triggers, note FROM motifs WHERE key=?", (key,)
                    ).fetchone()
                    if row:
                        said = row["said_by"]
                        if said != who:
                            said = "both"
                        try:
                            old = set(json.loads(row["triggers"] or "[]"))
                        except (TypeError, ValueError):
                            old = set()
                        merged = sorted(old | set(motifs_mod.triggers(text)))[:200]
                        new_note = motifs_mod.sentence_with(text, key)
                        old_note = row["note"] or ""
                        richer = (len(motifs_mod.match_tokens(new_note))
                                  > len(motifs_mod.match_tokens(old_note)))
                        # 用户的原话优先；它自己的转述不覆盖
                        note = (new_note if richer and (who == "user"
                                or row["said_by"] == "agent") else old_note)
                        self.db.execute(
                            "UPDATE motifs SET last_ts=?, last_turn=?, "
                            "count=count+1, said_by=?, triggers=?, note=? WHERE key=?",
                            (ts, turn_id, said,
                             json.dumps(merged, ensure_ascii=False), note, key))
                    else:
                        self.db.execute(
                            "INSERT INTO motifs(key, first_ts, first_turn, last_ts, "
                            "last_turn, count, said_by, note, triggers) "
                            "VALUES (?,?,?,?,?,1,?,?,?)",
                            (key, ts, turn_id, ts, turn_id, who, note, trig))
            self.db.commit()

    def pick_motif(self, user_input: str, recalled_text: str = "",
                   turn_id: int = 0, cooldown: int = 8,
                   avoid: set[str] | None = None) -> dict | None:
        q = motifs_mod.match_tokens(user_input)
        rows = self.db.execute(
            "SELECT * FROM motifs WHERE count >= 2 "
            "AND (length(key) >= 2 OR count >= 3) "
            "ORDER BY count DESC, length(key) DESC, last_ts DESC"
        ).fetchall()
        best = None
        for r in rows:
            if turn_id and r["last_used_turn"] and turn_id - r["last_used_turn"] < cooldown:
                continue
            if (turn_id and r["last_offered_turn"]
                    and turn_id - r["last_offered_turn"] < 2):
                continue
            if avoid and r["key"] in avoid:
                continue
            try:
                trig = set(json.loads(r["triggers"] or "[]"))
            except (TypeError, ValueError):
                trig = set()
            hit = q & trig
            score = len(hit)
            if r["key"] in (user_input or ""):
                score += 3
            if r["key"] in (recalled_text or ""):
                score += 1
            score += min(len(r["key"]), 4) * 0.1   # 长词更具体，优先
            if score <= 0:
                continue
            if best is None or score > best["score"]:
                best = {"key": r["key"], "note": r["note"],
                        "said_by": r["said_by"], "count": r["count"],
                        "score": score, "matched": sorted(hit)[:3]}
        return best

    def mark_motif_offered(self, key: str, turn_id: int,
                           ts: float | None = None) -> None:
        with self._lock:
            self.db.execute(
                "UPDATE motifs SET last_offered_turn=? WHERE key=?",
                (turn_id, key))
            self.db.commit()

    def mark_motif_used(self, key: str, turn_id: int,
                        ts: float | None = None) -> None:
        with self._lock:
            self.db.execute(
                "UPDATE motifs SET used_count=used_count+1, last_used_turn=?, "
                "last_used_ts=? WHERE key=?",
                (turn_id, ts or time.time(), key))
            self.db.commit()

    def motif_list(self, limit: int = 8) -> list[dict]:
        rows = self.db.execute(
            "SELECT key, count, said_by, note, used_count FROM motifs "
            "ORDER BY count DESC, last_ts DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def backfill_motifs(self) -> int:
        rows = self.db.execute(
            "SELECT id, ts, user_input, reply FROM turns ORDER BY id"
        ).fetchall()
        for r in rows:
            self.learn_motifs(r["user_input"], r["reply"] or "", r["id"], r["ts"])
        return len(rows)

    # --------------------------------------------------------------- 念头
    def add_thought(self, seed_type: str, seed_content: str,
                    thought_content: str, pressure: float,
                    ttl_hours: float = 6.0, max_workspace: int = 3) -> int | None:
        """写入一个念头；工作台最多 max_workspace 个未过期、未说出口的。
        新念头如果压力最低，会被自己挤掉，返回 None。"""
        now = time.time()
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO thoughts(seed_type, seed_content, thought_content, "
                "pressure, created_at, shared) VALUES (?,?,?,?,?,0)",
                (seed_type, seed_content[:200], thought_content[:300],
                 float(pressure), now))
            tid = cur.lastrowid
            cutoff = now - ttl_hours * 3600
            rows = self.db.execute(
                "SELECT id, pressure FROM thoughts WHERE shared=0 AND created_at > ? "
                "ORDER BY pressure ASC, id ASC", (cutoff,)
            ).fetchall()
            if len(rows) > max_workspace:
                for r in rows[:len(rows) - max_workspace]:
                    self.db.execute("DELETE FROM thoughts WHERE id=?", (r["id"],))
                    if r["id"] == tid:
                        tid = None
            self.db.commit()
            return tid

    def thoughts(self, limit: int = 10, include_shared: bool = True,
                 ttl_hours: float = 6.0) -> list[dict]:
        cutoff = time.time() - ttl_hours * 3600
        sql = "SELECT * FROM thoughts WHERE created_at > ?"
        params: list = [cutoff]
        if not include_shared:
            sql += " AND shared=0"
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        return [dict(r) for r in self.db.execute(sql, params).fetchall()]

    def mark_thought_shared(self, tid: int) -> None:
        with self._lock:
            self.db.execute("UPDATE thoughts SET shared=1 WHERE id=?", (tid,))
            self.db.commit()

    def thoughts_shared_today(self) -> int:
        lt = time.localtime()
        start = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday,
                             0, 0, 0, lt.tm_wday, lt.tm_yday, -1))
        return self.db.execute(
            "SELECT COUNT(*) c FROM thoughts WHERE shared=1 AND created_at >= ?",
            (start,)).fetchone()["c"]

    def recent_thought_seeds(self, limit: int = 5) -> list[str]:
        rows = self.db.execute(
            "SELECT seed_content FROM thoughts ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [r["seed_content"] for r in rows]

    def open_promise_count(self) -> int:
        return self.db.execute(
            "SELECT COUNT(*) c FROM promises WHERE status='open'").fetchone()["c"]

    def add_tribunal(self, question: str, r: dict, turn_id: int = 0) -> int:
        with self._lock:
            cur = self.db.execute(
                "INSERT INTO tribunal(ts, turn_id, question, a_text, b_text, c_text, "
                "ranking, reason, stance_key, stance_polarity, confidence, is_distinct, "
                "b_safe, raw) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (time.time(), int(turn_id or 0), question[:500],
                 r.get("a", ""), r.get("b", ""), r.get("c", ""),
                 r.get("ranking_text", "A>B>C"), r.get("reason", ""),
                 r.get("stance_key", ""), r.get("stance_polarity", "neutral"),
                 float(r.get("confidence") or 0), 1 if r.get("distinct") else 0,
                 1 if r.get("b_safe") else 0, (r.get("raw") or "")[:3000]))
            self.db.commit()
            return cur.lastrowid

    def tribunal_list(self, limit: int = 20) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM tribunal ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def latest_tribunal(self) -> dict | None:
        row = self.db.execute(
            "SELECT * FROM tribunal ORDER BY id DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def promise_list(self, limit: int = 50) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM promises ORDER BY (status='open') DESC, due_ts ASC LIMIT ?",
            (limit,)).fetchall()
        return [dict(r) for r in rows]

    def pending_list(self, limit: int = 20) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM pending ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def reflection_rows(self, limit: int = 20) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM reflections ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def turns_full(self, limit: int = 200) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM turns ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def last_turn_id(self) -> int:
        row = self.db.execute("SELECT MAX(id) m FROM turns").fetchone()
        return int(row["m"] or 0)

    def turns_since(self, after_id: int, limit: int = 50) -> list[dict]:
        rows = self.db.execute(
            "SELECT id, ts, user_input, reply, decision, state_before, state_after, meta "
            "FROM turns WHERE id > ? ORDER BY id LIMIT ?", (int(after_id), limit)
        ).fetchall()
        return [dict(r) for r in rows]

    def first_turns(self, n: int = 2) -> list[dict]:
        rows = self.db.execute(
            "SELECT user_input, reply FROM turns ORDER BY id LIMIT ?", (n,)
        ).fetchall()
        return [dict(r) for r in rows]

    def recent_turns(self, n: int = 24) -> list[dict]:
        """给压力曲线用：按时间正序返回最近 n 轮的起止 State。"""
        rows = self.db.execute(
            "SELECT id, ts, user_input, reply, decision, state_before, state_after, meta "
            "FROM turns WHERE archived=0 ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]

    def all_turns(self, limit: int | None = None) -> list[dict]:
        q = ("SELECT id, ts, user_input, reply, decision, risk, reason,"
             " state_before, state_after, signals, meta FROM turns ORDER BY id")
        if limit:
            q += f" LIMIT {int(limit)}"
        return [dict(r) for r in self.db.execute(q).fetchall()]

    def turn_events(self) -> dict[int, dict]:
        """turn_id -> 那一轮的状态结算明细（det / model / suppression）。"""
        rows = self.db.execute(
            "SELECT turn_id, detail FROM state_events "
            "WHERE turn_id IS NOT NULL AND source='turn' ORDER BY id"
        ).fetchall()
        out: dict[int, dict] = {}
        for r in rows:
            try:
                out[int(r["turn_id"])] = json.loads(r["detail"])
            except (TypeError, ValueError):
                continue
        return out

    def last_decision(self) -> str | None:
        row = self.db.execute(
            "SELECT decision FROM turns ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return None if row is None else row["decision"]

    def heavy_recent(self, window: int = 4, threshold: float = 0.45) -> int:
        rows = self.db.execute(
            "SELECT signals FROM turns ORDER BY id DESC LIMIT ?", (window,)
        ).fetchall()
        count = 0
        for r in rows:
            try:
                if json.loads(r["signals"]).get("load", 0) >= threshold:
                    count += 1
            except Exception:
                pass
        return count

    def refusal_streak(self, limit: int = 5) -> int:
        rows = self.db.execute(
            "SELECT decision FROM turns ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        n = 0
        for r in rows:
            if r["decision"] == "refuse":
                n += 1
            else:
                break
        return n

    # -------------------------------------------------------------- recall
    @staticmethod
    def _arousal(row: dict, meta: dict) -> float:
        """这一轮当时有多"重"：状态变化 + 冲动 + 内耗；被骂到拒绝是闪光灯。"""
        delta = abs(float(row.get("state_after", 0)) - float(row.get("state_before", 0)))
        sig = meta.get("signals") or {}
        imp = float(meta.get("impulse") or 0)
        sup = abs(float(meta.get("suppression") or 0))
        a = (0.5 * min(delta, 15) / 15
             + 0.3 * min(imp, 1.0)
             + 0.2 * min(sup, 5.0) / 5.0)
        if meta.get("blocked") and sig.get("insults"):
            a = max(a, 0.85)
        return min(1.0, a)

    @staticmethod
    def _flashbulb(row: dict, meta: dict, threshold: float = 8.0) -> bool:
        delta = abs(float(row.get("state_after", 0)) - float(row.get("state_before", 0)))
        sig = meta.get("signals") or {}
        return delta >= threshold or bool(meta.get("blocked") and sig.get("insults"))

    def recall(self, query: str, k: int = 3, pool: int = 200,
               half_life_days: float = 7.0, state_now: float | None = None,
               flashbulb_delta: float = 8.0, flashbulb_factor: float = 5.0) -> list[dict]:
        """关键词重叠 + 情绪唤醒 + 情绪一致 + 时间衰减。够用，且不需要任何模型。"""
        q = _tokens(query)
        if not q:
            return []
        rows = self.db.execute(
            "SELECT id, ts, user_input, reply, decision, state_before, state_after, meta "
            "FROM turns WHERE archived=0 ORDER BY id DESC LIMIT ?", (pool,),
        ).fetchall()
        now = time.time()
        scored: list[tuple[float, dict]] = []
        for r in rows:
            t = _tokens(r["user_input"] + " " + (r["reply"] or ""))
            overlap = len(q & t)
            if not overlap:
                continue
            row = dict(r)
            try:
                meta = json.loads(row.get("meta") or "{}")
            except (TypeError, ValueError):
                meta = {}
            arousal = self._arousal(row, meta)
            flash = self._flashbulb(row, meta, flashbulb_delta)
            hl = half_life_days * (flashbulb_factor if flash else 1.0)
            age_days = (now - row["ts"]) / 86400.0
            decay = 0.5 ** (age_days / max(0.5, hl))
            mood = 1.0 if state_now is None else (
                1.0 - abs(state_now - float(row["state_after"])) / 100.0 * 0.4)
            score = (overlap / (len(q) ** 0.5) * decay
                     * (0.4 + 0.6 * arousal) * mood)
            row["arousal"] = round(arousal, 2)
            row["flashbulb"] = flash
            scored.append((score, row))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [d for _s, d in scored[:k]]

    # --------------------------------------------------------------- stats
    def stats(self) -> dict:
        row = self.db.execute(
            "SELECT COUNT(*) c, SUM(decision='refuse') r FROM turns"
        ).fetchone()
        return {
            "state": round(self.state, 2),
            "turns": row["c"] or 0,
            "refusals": row["r"] or 0,
            "refusal_streak": self.refusal_streak(),
            "events": self.db.execute("SELECT COUNT(*) c FROM state_events").fetchone()["c"],
        }

    def state_history(self, n: int = 20) -> list[dict]:
        rows = self.db.execute(
            "SELECT * FROM state_events ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        return [dict(r) for r in rows]

    def reset(self) -> None:
        with self._lock:
            self.db.executescript(
                "DELETE FROM turns; DELETE FROM state_events; DELETE FROM meta;"
                " DELETE FROM motifs; DELETE FROM thoughts; DELETE FROM tribunal;"
            )
            self.db.commit()
        self._ensure_meta("state", self.state_init)
        for key in ("valence", "arousal", "safety"):
            self._ensure_meta(key, self.state_init)
        self._ensure_meta("baseline", self.state_baseline)
        self._ensure_meta("last_ts", 0.0)
        self._ensure_meta("coins", 0)
        self.db.commit()

    def close(self) -> None:
        self.db.close()
