"""岸的文档 RAG：documents / chunks / FTS5。

不依赖向量库。中文用 bigram 建 FTS，先保证可检索、可引用、可维护。
"""
from __future__ import annotations

import hashlib
import html
import re
import sqlite3
import time
from pathlib import Path
from typing import Iterable

SCHEMA = """
CREATE TABLE IF NOT EXISTS documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'doc',
  title TEXT NOT NULL DEFAULT '',
  url TEXT NOT NULL DEFAULT '',
  author TEXT NOT NULL DEFAULT '',
  date TEXT NOT NULL DEFAULT '',
  license TEXT NOT NULL DEFAULT '',
  hash TEXT NOT NULL UNIQUE,
  text TEXT NOT NULL DEFAULT '',
  meta TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  doc_id INTEGER NOT NULL,
  ord INTEGER NOT NULL,
  text TEXT NOT NULL,
  search_text TEXT NOT NULL,
  hash TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
USING fts5(search_text, tokenize='unicode61');
"""

_CJK = re.compile(r"[\u4e00-\u9fff]+")
_WORD = re.compile(r"[A-Za-z0-9_]+")


def terms(text: str, *, query: bool = False) -> list[str]:
    out: set[str] = set()
    for w in _WORD.findall((text or "").lower()):
        out.add(w)
    for run in _CJK.findall(text or ""):
        for ch in run:
            out.add(ch)
        for i in range(len(run) - 1):
            out.add(run[i:i + 2])
    if query:
        out = {t for t in out if len(t) >= 2 or t.isalnum()}
    return sorted(out)


def clean_html(raw: str) -> str:
    raw = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", raw)
    raw = re.sub(r"(?is)<!--.*?-->", " ", raw)
    raw = re.sub(r"(?is)<br\s*/?>", "\n", raw)
    raw = re.sub(r"(?is)</(p|div|li|h[1-6]|tr|section|article)>", "\n", raw)
    raw = re.sub(r"(?s)<[^>]+>", " ", raw)
    raw = html.unescape(raw)
    raw = re.sub(r"[ \t\u00a0]+", " ", raw)
    raw = re.sub(r"\n\s*\n+", "\n", raw)
    return raw.strip()


def chunk_text(text: str, size: int = 450, overlap: int = 60) -> list[str]:
    text = re.sub(r"[ \t]+", " ", (text or "").strip())
    text = re.sub(r"\n{3,}", "\n\n", text)
    if len(text) <= size:
        return [text] if text else []
    out = []
    start = 0
    while start < len(text):
        end = min(len(text), start + size)
        if end < len(text):
            # 尽量在换行/句号处切
            cut = max(text.rfind("\n", start, end), text.rfind("。", start, end),
                      text.rfind("！", start, end), text.rfind("？", start, end))
            if cut > start + size // 2:
                end = cut + 1
        out.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(start + 1, end - overlap)
    return [x for x in out if x]


class RagStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.db = sqlite3.connect(str(self.path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)
        self.db.commit()

    def add_document(self, *, source: str, title: str, text: str, url: str = "",
                     kind: str = "doc", author: str = "", date: str = "",
                     license: str = "", meta: str = "",
                     size: int = 450, overlap: int = 60) -> int | None:
        text = (text or "").strip()
        if len(text) < 80:
            return None
        h = hashlib.sha256((source + "\n" + url + "\n" + title + "\n" + text).encode()).hexdigest()
        row = self.db.execute("SELECT id FROM documents WHERE hash=?", (h,)).fetchone()
        if row:
            return int(row["id"])
        cur = self.db.execute(
            "INSERT INTO documents(source,kind,title,url,author,date,license,hash,text,meta,created)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (source, kind, title[:300], url, author[:200], date, license, h, text, meta, time.time()),
        )
        doc_id = int(cur.lastrowid)
        for i, c in enumerate(chunk_text(text, size=size, overlap=overlap)):
            sf = " ".join(terms(title + "\n" + c))
            ch = hashlib.sha256((str(doc_id) + str(i) + c).encode()).hexdigest()
            ccur = self.db.execute(
                "INSERT INTO chunks(doc_id,ord,text,search_text,hash) VALUES (?,?,?,?,?)",
                (doc_id, i, c, sf, ch),
            )
            self.db.execute("INSERT INTO chunks_fts(rowid,search_text) VALUES (?,?)",
                            (int(ccur.lastrowid), sf))
        self.db.commit()
        return doc_id

    def add_many(self, docs: Iterable[dict]) -> int:
        n = 0
        for d in docs:
            if self.add_document(**d) is not None:
                n += 1
        return n

    def search(self, query: str, k: int = 6) -> list[dict]:
        q = terms(query, query=True)
        if not q:
            return []
        # FTS5 OR 查询；引号避免特殊字符
        expr = " OR ".join('"' + t.replace('"', '""') + '"' for t in q[:40])
        try:
            rows = self.db.execute(
                "SELECT c.id,c.doc_id,c.ord,c.text,d.title,d.source,d.url,"
                " bm25(chunks_fts) AS rank "
                "FROM chunks_fts JOIN chunks c ON c.id=chunks_fts.rowid "
                "JOIN documents d ON d.id=c.doc_id "
                "WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?",
                (expr, k),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [{"id": r["id"], "doc_id": r["doc_id"], "ord": r["ord"],
                 "text": r["text"], "title": r["title"], "source": r["source"],
                 "url": r["url"], "rank": round(float(r["rank"]), 4)} for r in rows]

    def stats(self) -> dict:
        docs = self.db.execute("SELECT count(*) FROM documents").fetchone()[0]
        chunks = self.db.execute("SELECT count(*) FROM chunks").fetchone()[0]
        by_source = self.db.execute(
            "SELECT source, count(*) n FROM documents GROUP BY source ORDER BY n DESC"
        ).fetchall()
        return {"documents": docs, "chunks": chunks,
                "sources": [(r["source"], r["n"]) for r in by_source]}

    def close(self):
        self.db.close()

    def reset(self):
        self.db.executescript("DROP TABLE IF EXISTS chunks_fts; DROP TABLE IF EXISTS chunks; DROP TABLE IF EXISTS documents;")
        self.db.executescript(SCHEMA)
        self.db.commit()
