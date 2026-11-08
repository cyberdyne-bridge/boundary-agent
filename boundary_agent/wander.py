"""Default Mode：没人说话的时候，它自己待着会想什么。

不是定时回复，是内部念头：
  1. 从旧记忆 / 暗号 / 承诺 / 复盘里挑一个种子
  2. 让模型写一句“现在的念头”，不面向用户
  3. 算压力；工作台最多 3 个，压力低的被挤掉
  4. 只有压力过阈值、状态不太冷、今天还没说够，才变成主动消息
"""

from __future__ import annotations

import difflib
import json
import random
import re
import threading
import time

from .llm import LLMError

_TOXIC = re.compile(r"傻逼|废物|垃圾|去死|滚开|恶心|猪")
_BAD = re.compile(r"作为(一个)?(AI|模型|助手)|用户|对不起，我无法|"
                  r"有什么我可以|希望(这些|对你)|作为一个语言模型")


def calc_pressure(seed: dict, state: float, unresolved: float,
                  novelty: float = 1.0) -> float:
    """0–1。唤醒越高、事情越没完、状态越冷、越新鲜，压力越大。"""
    arousal = max(0.0, min(1.0, float(seed.get("arousal", 0.4))))
    low = max(0.0, min(1.0, (55.0 - float(state)) / 55.0))
    p = (0.35 * arousal
         + 0.25 * max(0.0, min(1.0, unresolved))
         + 0.20 * low
         + 0.20 * max(0.0, min(1.0, novelty)))
    return round(max(0.0, min(1.0, p)), 3)


class Wanderer:
    def __init__(self, agent) -> None:
        self.agent = agent
        self.cfg = agent.cfg
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_user = time.time()

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="wander", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def note_user_turn(self) -> None:
        self._last_user = time.time()
        self._wake.set()

    def idle_seconds(self) -> float:
        return max(0.0, time.time() - self._last_user)

    # ------------------------------------------------------------------ loop
    def _loop(self) -> None:
        idle = max(0.05, float(self.cfg.wander_idle_minutes)) * 60.0
        while not self._stop.is_set():
            wait = idle - (time.time() - self._last_user)
            if wait > 0:
                self._wake.wait(min(wait, 60.0))
                self._wake.clear()
                continue
            if self._quiet():
                self._wake.wait(300.0)
                self._wake.clear()
                continue
            try:
                act = run_daily(self.agent)
                if act is None:
                    self._tick()
            except Exception:
                pass
            jitter = random.uniform(0.8, 1.2)
            self._wake.wait(max(5.0, self.cfg.wander_interval_minutes * 60.0 * jitter))
            self._wake.clear()

    def _quiet(self) -> bool:
        h = time.localtime().tm_hour
        a, b = int(self.cfg.wander_quiet_start), int(self.cfg.wander_quiet_end)
        return (h >= a or h < b) if a > b else (a <= h < b)

    # ------------------------------------------------------------------ tick
    def _tick(self) -> dict | None:
        idle = max(0.05, float(self.cfg.wander_idle_minutes)) * 60.0
        if time.time() - self._last_user < idle:
            return None
        if self._quiet():
            return None
        seed = self._pick_seed()
        if not seed:
            return None
        thought = self._think(seed)
        if not thought or self._stop.is_set():
            return None

        mem = self.agent.mem
        unresolved = 1.0 if (mem.open_promise_count() or mem.grudge) else 0.25
        pressure = calc_pressure(seed, mem.state, unresolved, 1.0)
        tid = mem.add_thought(
            seed["type"], seed["content"], thought, pressure,
            ttl_hours=self.cfg.wander_ttl_hours,
            max_workspace=self.cfg.wander_max_workspace,
        )
        if tid is None:                     # 自己就是压力最低的那个，被挤掉了
            return None

        share = (pressure >= self.cfg.wander_threshold
                 and mem.state >= 30.0
                 and not self._quiet()
                 and mem.thoughts_shared_today() < self.cfg.wander_daily_shares
                 and time.time() - self._last_user >= idle)
        if share:
            mem.add_pending("thought", thought, "自己想到的")
            mem.mark_thought_shared(tid)
        return {"id": tid, "pressure": pressure, "thought": thought,
                "seed": seed, "shared": share}

    # ------------------------------------------------------------------ seed
    def _pick_seed(self) -> dict | None:
        mem = self.agent.mem
        used = set(mem.recent_thought_seeds(5))
        options: list[tuple[float, dict]] = []

        # 旧记忆：越重、越近，越容易被想起
        with mem._lock:
            rows = mem.db.execute(
                "SELECT id, user_input, state_before, state_after, meta FROM turns "
                "WHERE id <= (SELECT MAX(id) - 8 FROM turns) "
                "ORDER BY RANDOM() LIMIT 30"
            ).fetchall()
        for r in rows:
            text = (r["user_input"] or "").strip()
            key_text = text[:100]
            if len(text) < 4 or key_text in used or _TOXIC.search(text):
                continue
            try:
                meta = json.loads(r["meta"] or "{}")
            except (TypeError, ValueError):
                meta = {}
            delta = abs(float(r["state_after"]) - float(r["state_before"]))
            arousal = min(1.0, 0.5 * min(delta, 15) / 15
                          + 0.3 * float(meta.get("impulse") or 0)
                          + 0.2 * min(abs(float(meta.get("suppression") or 0)), 5) / 5)
            options.append((0.4 + arousal, {
                "type": "memory", "content": key_text,
                "arousal": arousal, "turn_id": r["id"],
            }))

        for m in mem.motif_list(8):
            content = f"{m['key']}：{m['note'][:80]}"
            if m["count"] >= 2 and content not in used:
                options.append((0.35, {
                    "type": "motif", "content": content, "arousal": 0.55,
                }))

        for p in mem.due_promises(limit=3):
            content = p["text"][:100]
            if content in used:
                continue
            options.append((0.6, {
                "type": "promise", "content": content, "arousal": 0.75,
            }))

        for text in mem.recent_reflections(3):
            t = (text or "").strip()
            content = t[:100]
            if len(t) < 8 or t.startswith("你：") or content in used:
                continue
            options.append((0.3, {
                "type": "reflection", "content": content, "arousal": 0.35,
            }))

        if mem.grudge:
            options.append((0.5, {
                "type": "grudge", "content": "冷战还没化开。", "arousal": 0.8,
            }))

        if not options:
            return None
        weights = [o[0] for o in options]
        return random.choices(options, weights=weights, k=1)[0][1]

    # ----------------------------------------------------------------- think
    def _think(self, seed: dict) -> str:
        base = ("你一个人待着，没有人说话。\n"
                f"你想起：{seed['content']}\n"
                "这是你脑子里的回忆，不是他在跟你说话。\n"
                "写一句你现在的念头。不要对谁说话，不要解释，不要总结，"
                "不要讲道理。1-2 句，具体，像一个具体的人在心里嘀咕。")
        text = self._call(base)
        if text:
            return text
        # 再试一次，收紧
        return self._call(base + "\n别分析，别总结，只说一句具体的念头。")

    def _call(self, system: str) -> str:
        try:
            raw = self.agent.llm.chat(
                [{"role": "system", "content": system},
                 {"role": "user", "content": "……"}],
                purpose="wander", temperature=0.9, max_tokens=90,
            )
        except LLMError:
            return ""
        text = (raw or "").strip().strip('"“”').replace("\n", " ")
        text = re.sub(r"^(念头|心里想)[:：]\s*", "", text).strip()
        if not (4 <= len(text) <= 100):
            return ""
        if _BAD.search(text):
            return ""
        if difflib.SequenceMatcher(None, text, "……").ratio() > 0.9:
            return ""
        return text
