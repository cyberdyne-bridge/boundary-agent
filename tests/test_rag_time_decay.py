"""时间感知检索：event_time 迁移 + 衰减重排。

对应 2026-10-23 的改动：documents 增加 event_time 列，search 按
「相似度 × 时间衰减」重排，并把记录时间写进返回文本。
"""
from __future__ import annotations

import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from boundary_agent.rag import RagStore

OLD_SCHEMA = """
CREATE TABLE documents (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT NOT NULL,
  kind TEXT NOT NULL,
  title TEXT NOT NULL,
  url TEXT NOT NULL DEFAULT '',
  author TEXT NOT NULL DEFAULT '',
  date TEXT NOT NULL DEFAULT '',
  license TEXT NOT NULL DEFAULT '',
  hash TEXT NOT NULL UNIQUE,
  text TEXT NOT NULL DEFAULT '',
  meta TEXT NOT NULL DEFAULT '',
  created REAL NOT NULL
);
"""

BODY = "热茶 温度 记录 备注 " * 20


class RagTimeDecayTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "rag.sqlite3"

    def tearDown(self):
        self.tmp.cleanup()

    def test_migrates_old_database_and_backfills(self):
        con = sqlite3.connect(str(self.db))
        con.executescript(OLD_SCHEMA)
        con.execute(
            "INSERT INTO documents(source,kind,title,hash,text,created)"
            " VALUES ('t','note','old','h1',?,?)", (BODY, 1000.0))
        con.commit()
        con.close()

        store = RagStore(self.db)
        cols = {r[1] for r in store.db.execute("PRAGMA table_info(documents)")}
        self.assertIn("event_time", cols)
        row = store.db.execute("SELECT event_time FROM documents WHERE hash='h1'").fetchone()
        self.assertEqual(row[0], 1000.0)

    def test_recent_document_wins_on_equal_similarity(self):
        store = RagStore(self.db)
        now = time.time()
        store.add_document(source="t", title="old", text=BODY,
                           event_time=now - 400 * 86400)
        store.add_document(source="t", title="new", text=BODY,
                           event_time=now - 1 * 86400)
        hits = store.search("热茶 温度", k=2, now=now)
        self.assertEqual([h["title"] for h in hits], ["new", "old"])
        self.assertEqual(hits[0]["time_decay"], 1.0)
        self.assertEqual(hits[1]["time_decay"], 0.5)

    def test_result_carries_record_time_text(self):
        store = RagStore(self.db)
        now = time.time()
        store.add_document(source="t", title="n", text=BODY, event_time=now)
        hit = store.search("热茶", k=1, now=now)[0]
        self.assertIn("[记录于", hit["text"])
        self.assertIn("event_time_text", hit)
        self.assertGreaterEqual(hit["score"], 0.0)

    def test_k_is_respected_after_rerank(self):
        store = RagStore(self.db)
        now = time.time()
        for i in range(5):
            store.add_document(source="t", title=f"d{i}", text=BODY,
                               event_time=now - i * 86400)
        self.assertEqual(len(store.search("热茶", k=2, now=now)), 2)


if __name__ == "__main__":
    unittest.main()
