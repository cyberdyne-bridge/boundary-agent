"""拒绝日志：独立于对话窗口的 append-only JSONL。

为什么是 JSONL 而不是一个大 JSON 数组：
  1. 追加不需要重写全文，进程崩了也不丢历史；
  2. `wc -l` / `jq` / pandas 都能直接吃；
  3. 每行自包含，方便你后面做统计（拒绝率 vs state 的相关性）。

这是"证据层"，和 memory.sqlite3 故意分开：即使你清空对话，
拒绝记录仍然在，可以拿来做长期曲线。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path


class RejectionLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def append(
        self,
        *,
        user_input: str,
        reason: str,
        state: float,
        band: str,
        risk: float,
        repair_need: str = "",
        boundary: str | None = None,
        turn_id: int | None = None,
        extra: dict | None = None,
    ) -> dict:
        entry = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "epoch": round(time.time(), 3),
            "state": round(state, 2),
            "band": band,
            "risk": round(risk, 3),
            "user_input": user_input,
            "reason": reason,
            "repair_need": repair_need,
            "boundary": boundary,
            "turn_id": turn_id,
        }
        if extra:
            entry["extra"] = extra
        line = json.dumps(entry, ensure_ascii=False)
        # 单行原子追加 + fsync，保证崩溃时也能读到完整记录
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())
        return entry

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
        return out[-limit:] if limit else out

    def count(self) -> int:
        if not self.path.exists():
            return 0
        with open(self.path, "r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())

    def tail(self, n: int = 5) -> list[dict]:
        return self.read(limit=n)
