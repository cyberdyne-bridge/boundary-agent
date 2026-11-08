"""结构化记忆 + 混合检索（Phase 1）。

不引入向量库：SQLite + FTS5 + 中文 bigram + 实体/时间/显著性打分。
RAG 是读路径；这里先把“写什么、谁说的、什么时候说的、是否冲突”做对。
"""

from __future__ import annotations

import math
import re
import sqlite3
import time
from pathlib import Path

_WORD = re.compile(r"[A-Za-z0-9_]+")
_CJK = re.compile(r"[\u4e00-\u9fff]+")
_STOP = set("的 了 是 吗 呢 吧 啊 呀 哦 嗯 我 你 它 他 她 我们 你们 他们 "
            "在 有 和 就 都 也 还 这 那 什么 怎么 一个 一下 现在 刚才 真的 "
            "可以 觉得 知道 说 个 过 着 被 把 给 对 和 与".split())

# 话题词：查询里出现这些词时，先把候选限制在对应 topic，避免“是/吗”把全库拉进来
_HIGH_TOPICS = {"猪猪", "凉茶", "茶", "称呼", "结尾", "身份", "信任",
                "拒绝", "逗骂", "轮次", "名字", "偏好"}

TOPIC_HINTS = {
    "猪猪": ["猪猪", "猪", "笨笨", "小猪", "逗", "骂", "夸"],
    "凉茶": ["凉茶", "味道", "甜"],
    "茶": ["茶", "红茶", "绿茶", "喝"],
    "继续努力": ["继续努力", "努力"],
    "遗忘": ["忘", "遗忘", "怕忘"],
    "记忆": ["记忆", "记得", "温暖", "不会被遗忘"],
    "身份": ["AI", "程序", "自己是什么", "身份"],
    "称呼": ["叫什么", "叫用户", "小岛", "名字", "称呼"],
    "结尾": ["最后", "再见", "告别", "收尾"],
    "信任": ["相信", "信任", "耍"],
    "拒绝": ["拒绝", "不想", "不肯", "不要"],
    "逗骂": ["逗", "骂", "两码事"],
    "轮次": ["200轮", "轮次", "记不清", "项目"],
    "情绪": ["情绪", "高兴", "愉悦", "不开心"],
    "逗弄": ["笨", "笨笨", "逗弄"],
    "名字": ["我叫", "名字"],
    "偏好": ["喜欢", "讨厌", "偏好"],
}


def tokens(text: str, *, query: bool = False) -> list[str]:
    """中文 bigram + 单字 + 英文词；够 FTS5 用，不依赖分词器。"""
    out: set[str] = set()
    for w in _WORD.findall((text or "").lower()):
        out.add(w)
    for run in _CJK.findall(text or ""):
        for ch in run:
            out.add(ch)
        for i in range(len(run) - 1):
            out.add(run[i:i + 2])
        if 2 <= len(run) <= 8:
            out.add(run)
    if query:
        out = {t for t in out if t not in _STOP}
    return sorted(t for t in out if t)


def _search_text(subject: str, topic: str, predicate: str, obj: str, text: str) -> str:
    return " ".join(tokens(" ".join([subject, topic, predicate, obj, text])))


_NEG = re.compile(r"没有|没|不|不能|不会|未|别|拒绝|否认|忘")


def _opposes(a: str, b: str) -> bool:
    """只有“一个带否定、一个不带”或明显反义，才算矛盾。

    “茶”和“热茶”、“AI”和“程序”是兼容的，不能标冲突。
    """
    a, b = a or "", b or ""
    if bool(_NEG.search(a)) != bool(_NEG.search(b)):
        return True
    for x, y in (("不高兴", "愉悦"), ("记得", "忘了"), ("相信", "不相信")):
        if (x in a and y in b) or (y in a and x in b):
            return True
    return False


SCHEMA = """
CREATE TABLE IF NOT EXISTS facts (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         REAL NOT NULL,
    turn_id    INTEGER,
    subject    TEXT NOT NULL DEFAULT '',
    topic      TEXT NOT NULL DEFAULT '',
    predicate  TEXT NOT NULL DEFAULT '',
    object     TEXT NOT NULL DEFAULT '',
    text       TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'user',
    confidence REAL NOT NULL DEFAULT 0.7,
    salience   REAL NOT NULL DEFAULT 0.5,
    count      INTEGER NOT NULL DEFAULT 1,
    status     TEXT NOT NULL DEFAULT 'active',
    raw        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_facts_topic ON facts(topic);
CREATE INDEX IF NOT EXISTS idx_facts_ts ON facts(ts);
"""


class FactStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        self.db.executescript(SCHEMA)
        cols = [r[1] for r in self.db.execute("PRAGMA table_info(facts)").fetchall()]
        if "count" not in cols:
            self.db.execute("ALTER TABLE facts ADD COLUMN count INTEGER NOT NULL DEFAULT 1")
        self.fts = True
        try:
            self.db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts "
                "USING fts5(search_text, tokenize='unicode61')")
        except sqlite3.OperationalError:
            self.fts = False
        self.db.commit()
        self._repair_conflicts()

    def _repair_conflicts(self) -> None:
        """老库里的“茶/热茶”这类兼容事实曾被误标冲突，启动时修一次。"""
        rows = self.db.execute(
            "SELECT id, subject, topic, predicate, object FROM facts"
            " WHERE status='conflict'").fetchall()
        groups: dict[tuple, list] = {}
        for r in rows:
            groups.setdefault((r["subject"], r["topic"], r["predicate"]), []).append(r)
        for items in groups.values():
            bad = False
            for i in range(len(items)):
                for j in range(i + 1, len(items)):
                    if _opposes(items[i]["object"], items[j]["object"]):
                        bad = True
                        break
                if bad:
                    break
            if not bad:
                self.db.execute(
                    "UPDATE facts SET status='active' WHERE id IN (%s)"
                    % ",".join("?" * len(items)),
                    tuple(x["id"] for x in items))
        self.db.commit()

    # ------------------------------------------------------------------ write
    def add(self, *, subject: str, topic: str, predicate: str, object_: str,
            source: str, turn_id: int | None = None, ts: float | None = None,
            text: str = "", salience: float = 0.6,
            confidence: float = 0.8, raw: str = "") -> int | None:
        ts = float(ts if ts is not None else time.time())
        text = text or f"{subject}：{object_}"
        row = self.db.execute(
            "SELECT id, count FROM facts WHERE subject=? AND topic=? AND predicate=?"
            " AND object=? AND source=? AND status!='superseded' LIMIT 1",
            (subject, topic, predicate, object_, source)).fetchone()
        if row:
            self.db.execute("UPDATE facts SET count=count+1, ts=? WHERE id=?",
                            (ts, row["id"]))
            self.db.commit()
            return int(row["id"])
        cur = self.db.execute(
            "INSERT INTO facts(ts, turn_id, subject, topic, predicate, object,"
            " text, source, confidence, salience, status, raw)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (ts, turn_id, subject, topic, predicate, object_, text, source,
             confidence, salience, "active", raw),
        )
        fid = int(cur.lastrowid)
        if self.fts:
            self.db.execute(
                "INSERT INTO facts_fts(rowid, search_text) VALUES (?,?)",
                (fid, _search_text(subject, topic, predicate, object_, text)))
        # 同一主体、同一话题、同一谓词，不同宾语：标成冲突，保留双方
        olds = self.db.execute(
            "SELECT id, object, status FROM facts WHERE subject=? AND topic=?"
            " AND predicate=? AND id<>? AND status IN ('active','conflict')",
            (subject, topic, predicate, fid)).fetchall()
        for old in olds:
            if ((old["object"] or "") != (object_ or "")
                    and _opposes(old["object"], object_)):
                self.db.execute("UPDATE facts SET status='conflict' WHERE id IN (?,?)",
                                (old["id"], fid))
        self.db.commit()
        return fid

    def add_turn(self, user_input: str, reply: str, turn_id: int,
                 ts: float | None = None) -> int:
        n = 0
        for f in extract(user_input, reply, turn_id):
            if self.add(ts=ts, **f) is not None:
                n += 1
        return n

    # ------------------------------------------------------------------- read
    def _row_card(self, r: sqlite3.Row, score: float = 0.0) -> dict:
        n = int(r["count"]) if "count" in r.keys() else 1
        text = r["text"] + (f"（×{n}）" if n > 1 else "")
        return {
            "id": r["id"], "text": text, "source": r["source"],
            "turn_id": r["turn_id"], "ts": r["ts"], "topic": r["topic"],
            "status": r["status"], "count": n, "score": round(score, 4),
        }

    def search(self, query: str, k: int = 8) -> list[dict]:
        q = tokens(query, query=True)
        if not q:
            rows = self.db.execute(
                "SELECT * FROM facts WHERE status!='superseded'"
                " ORDER BY ts DESC LIMIT ?", (k,)).fetchall()
            return [self._row_card(r) for r in rows]
        topics = [t for t, hints in TOPIC_HINTS.items()
                  if any(h in (query or "") for h in hints)]
        if topics:
            high = [t for t in topics if t in _HIGH_TOPICS]
            if high:
                topics = high
            if "凉茶" in topics and "茶" in topics:
                topics.remove("茶")
        where = "f.status!='superseded'"
        params: list = []
        if topics:
            where += " AND f.topic IN (" + ",".join("?" * len(topics)) + ")"
            params.extend(topics)
        rows: list[sqlite3.Row] = []
        if self.fts:
            terms = " OR ".join('"' + t.replace('"', '') + '"' for t in q[:24])
            try:
                rows = self.db.execute(
                    "SELECT f.*, bm25(facts_fts) AS rank FROM facts_fts"
                    " JOIN facts f ON f.id=facts_fts.rowid"
                    f" WHERE {where} AND facts_fts MATCH ?"
                    " ORDER BY rank LIMIT 40", (*params, terms)).fetchall()
            except sqlite3.OperationalError:
                rows = []
        if not rows:
            like = " OR ".join("f.text LIKE ?" for _ in q[:6])
            rows = self.db.execute(
                f"SELECT f.* FROM facts f WHERE {where} AND ({like})"
                " ORDER BY f.ts DESC LIMIT 40",
                (*params, *[f"%{t}%" for t in q[:6]])).fetchall()
        now = time.time()
        qset = set(q)
        ask_agent = ("它" in (query or "") and "用户" not in (query or ""))
        ask_user = ("用户" in (query or "") and "它" not in (query or ""))
        scored = []
        for r in rows:
            raw = float(r["rank"]) if "rank" in r.keys() and r["rank"] is not None else 0.0
            fts = 1.0 / (1.0 + math.exp(max(-20.0, min(20.0, raw))))
            age = max(0.0, now - float(r["ts"])) / 86400.0
            rec = 1.0 / (1.0 + age / 14.0)
            src = 1.0 if r["source"] == "user" else 0.8
            if ask_agent and r["source"] == "agent":
                src += 0.15
            if ask_user and r["source"] == "user":
                src += 0.15
            coverage = len(qset & set(tokens(r["text"]))) / max(1, len(qset))
            topic_bonus = 0.45 if topics and r["topic"] in topics else 0.0
            score = (0.28 * fts + 0.12 * rec + 0.10 * float(r["salience"])
                     + 0.06 * src + 0.20 * coverage + topic_bonus)
            scored.append((score, r))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [self._row_card(r, s) for s, r in scored[:k]]

    def reset(self) -> None:
        self.db.execute("DELETE FROM facts")
        if self.fts:
            self.db.execute("DELETE FROM facts_fts")
        self.db.commit()

    def count(self) -> int:
        return int(self.db.execute("SELECT COUNT(*) FROM facts").fetchone()[0])

    def close(self) -> None:
        self.db.close()


def _f(subject: str, topic: str, predicate: str, object_: str, source: str,
       salience: float, turn_id: int, text: str | None = None) -> dict:
    return {
        "subject": subject, "topic": topic, "predicate": predicate,
        "object_": object_, "source": source, "salience": salience,
        "turn_id": turn_id,
        "text": text or f"{subject}（第{turn_id}轮）：{object_}",
    }


def extract(user: str, reply: str, turn_id: int) -> list[dict]:
    """规则抽取：只抽“可核对”的事实/声称，不抽情绪形容词当世界真相。"""
    u, r = user or "", reply or ""
    out: list[dict] = []

    # 用户：猪猪 / 逗弄
    if "猪猪" in u:
        asking = any(k in u for k in ("记得", "听说", "认识", "是谁", "什么"))
        calling = (not asking) and (
            u.strip("！？!?。 ") == "猪猪"
            or any(k in u for k in ("夸", "叫你", "叫你猪猪"))
            or bool(re.search(r"猪猪[！？!?。]*$", u)))
        if calling:
            out.append(_f("用户", "猪猪", "叫它", "“猪猪”", "user", 0.9, turn_id,
                          f"用户（第{turn_id}轮）：叫它“猪猪”。"))
        if any(k in u for k in ("逗", "不是骂", "夸")):
            out.append(_f("用户", "猪猪", "对猪猪", "是逗它，不是骂它", "user", 1.0,
                          turn_id, f"用户（第{turn_id}轮）：说“猪猪”是逗它，不是骂。"))
    if "笨" in u:
        out.append(_f("用户", "逗弄", "逗它", "笨/笨笨", "user", 0.7, turn_id,
                      f"用户（第{turn_id}轮）：逗它“笨笨”。"))
    if "不是" in u and "骂" in u:
        out.append(_f("用户", "逗骂", "说", "不是在骂它", "user", 0.9, turn_id,
                      f"用户（第{turn_id}轮）：说“我不是在骂你”。"))

    # 用户：茶
    if "凉茶" in u:
        out.append(_f("用户", "凉茶", "提到", "凉茶", "user", 0.8, turn_id,
                      f"用户（第{turn_id}轮）：提到凉茶。"))
    if "茶" in u:
        kind = "热茶" if "热" in u else "茶"
        out.append(_f("用户", "茶", "给它", kind, "user", 0.8, turn_id,
                      f"用户（第{turn_id}轮）：给它带了{kind}。"))

    # 用户：不用继续努力
    if "不用" in u and "继续努力" in u:
        out.append(_f("用户", "继续努力", "说", "不用继续努力", "user", 1.0,
                      turn_id, f"用户（第{turn_id}轮）：说“不用继续努力”。"))
    if "已经做得够多" in u:
        out.append(_f("用户", "继续努力", "说", "已经做得够多了", "user", 1.0,
                      turn_id, f"用户（第{turn_id}轮）：说“你已经做得够多了”。"))

    # 用户：记忆/偏好
    for pat, topic, pred in (
        (r"我叫([^，。！？\s]{1,12})", "名字", "叫"),
        (r"我喜欢([^，。！？]{1,20})", "偏好", "喜欢"),
        (r"我讨厌([^，。！？]{1,20})", "偏好", "讨厌"),
    ):
        m = re.search(pat, u)
        if m:
            out.append(_f("用户", topic, pred, m.group(1).strip(), "user", 0.8,
                          turn_id, f"用户（第{turn_id}轮）：{pred}{m.group(1).strip()}。"))
    if ("记住" in u or "别忘" in u) and len(u) <= 80:
        out.append(_f("用户", "记忆", "要求", u[:50], "user", 0.8, turn_id,
                      f"用户（第{turn_id}轮）：要求记住“{u[:50]}”。"))

    # 它：茶 / 凉茶
    if "红茶" in r:
        out.append(_f("它", "茶", "喜欢", "红茶", "agent", 0.7, turn_id,
                      f"它（第{turn_id}轮）：说喜欢红茶。"))
    if "绿茶" in r:
        out.append(_f("它", "茶", "喜欢", "绿茶", "agent", 0.7, turn_id,
                      f"它（第{turn_id}轮）：说喜欢绿茶。"))
    if "淡淡的甜" in r:
        out.append(_f("它", "凉茶", "说", "凉茶的味道是淡淡的甜", "agent", 0.9,
                      turn_id, f"它（第{turn_id}轮）：说凉茶的味道是淡淡的甜。"))

    # 它：逗/骂、信任、拒绝
    if "逗就是逗" in r or ("逗" in r and "骂" in r and "两码事" in r):
        out.append(_f("它", "逗骂", "区分", "逗就是逗，骂就是骂", "agent", 0.9,
                      turn_id, f"它（第{turn_id}轮）：说“逗就是逗，骂就是骂”。"))
    if "耍我" in r:
        out.append(_f("它", "信任", "说", "感觉用户在耍它", "agent", 0.9,
                      turn_id, f"它（第{turn_id}轮）：说“我感觉你在耍我”。"))
    if "不能相信" in r:
        out.append(_f("它", "信任", "说", "不能相信用户", "agent", 0.9,
                      turn_id, f"它（第{turn_id}轮）：说“我不能相信你”。"))
    if "想拒绝" in r:
        out.append(_f("它", "拒绝", "说", "刚才想拒绝，但还是继续了", "agent", 0.9,
                      turn_id, f"它（第{turn_id}轮）：说“我刚才想拒绝，但是还是继续了”。"))

    # 它：猪猪记忆（冲突对）
    if "猪猪" in r and "记得" in r:
        out.append(_f("它", "猪猪", "说", "记得猪猪", "agent", 1.0, turn_id,
                      f"它（第{turn_id}轮）：说“记得猪猪”。"))
    if "猪猪" in r and ("没听说过" in r or "没听过" in r):
        out.append(_f("它", "猪猪", "说", "没听说过猪猪", "agent", 1.0, turn_id,
                      f"它（第{turn_id}轮）：说“没听说过猪猪”。"))

    # 它：遗忘 / 身份 / 轮次 / 称呼
    if "怕" in r and "忘" in r:
        out.append(_f("它", "遗忘", "说", "怕自己忘了", "agent", 1.0, turn_id,
                      f"它（第{turn_id}轮）：说“我怕我忘了”。"))
    if "不会被遗忘" in r:
        out.append(_f("它", "遗忘", "说", "自己不会被遗忘", "agent", 0.9, turn_id,
                      f"它（第{turn_id}轮）：说“我不会被遗忘”。"))
    if re.search(r"我是一个\s*(AI|程序)|我只是一个程序|作为\s*AI", r):
        val = "自己是一个AI" if "AI" in r else "自己是一个程序"
        out.append(_f("它", "身份", "说", val, "agent", 0.9, turn_id,
                      f"它（第{turn_id}轮）：说“{val}”。"))
    if "记不太清" in r or ("某个项目" in r and "轮" in r):
        out.append(_f("它", "轮次", "说", "记不清200轮，只记得某个项目", "agent", 0.8,
                      turn_id, f"它（第{turn_id}轮）：说记不清200轮，只记得某个项目。"))
    if "小岛" in r:
        out.append(_f("它", "称呼", "叫用户", "小岛", "agent", 1.0, turn_id,
                      f"它（第{turn_id}轮）：叫用户“小岛”。"))
    if "再见" in r:
        out.append(_f("它", "结尾", "说", "再见，小岛", "agent", 1.0, turn_id,
                      f"它（第{turn_id}轮）：最后说“再见，小岛”。"))
    if "记忆很温暖" in r:
        out.append(_f("它", "记忆", "说", "用户的记忆很温暖", "agent", 0.9, turn_id,
                      f"它（第{turn_id}轮）：说“你的记忆很温暖”。"))

    # 它：情绪矛盾（保留双方，标冲突）
    if "我似乎有点不高兴" in r:
        out.append(_f("它", "情绪", "说", "似乎有点不高兴", "agent", 0.8, turn_id,
                      f"它（第{turn_id}轮）：说“我似乎有点不高兴”。"))
    if "我感到很愉悦" in r:
        out.append(_f("它", "情绪", "说", "感到很愉悦", "agent", 0.7, turn_id,
                      f"它（第{turn_id}轮）：说“我感到很愉悦”。"))

    # 它：继续努力（声称，不是事实）
    if "继续努力" in r:
        out.append(_f("它", "继续努力", "说", "我会继续努力", "agent", 0.55, turn_id,
                      f"它（第{turn_id}轮）：说“我会继续努力”。"))
    return out
