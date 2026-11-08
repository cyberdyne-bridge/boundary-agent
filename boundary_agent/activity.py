"""每天一次的自选活动：没人找岸时，她自己选冲浪 / 翻旧记忆 / 发呆。

不绑具体时间；只由“闲置”触发。冲浪成功才记 surf_done。
选什么由 4B 决定；模型失败时用状态兜底。
"""

from __future__ import annotations

import time

from . import surf

SYS = """现在没人找你。你只能选一件事：
- surf：上网看一会儿（今天还没冲过）
- memory：翻一段旧记忆
- sit：什么都不做
只回一个词：surf / memory / sit。"""


def _choose(agent, surf_available: bool) -> str:
    opts = "surf / memory / sit" if surf_available else "memory / sit"
    try:
        text = agent.llm.chat(
            [{"role": "system", "content": SYS},
             {"role": "user", "content": f"今天可选：{opts}。你选哪个？"}],
            purpose="daily", temperature=0.6, max_tokens=8,
        ).strip().lower()
    except Exception:
        text = ""
    if surf_available and ("surf" in text or "冲浪" in text):
        return "surf"
    if "memory" in text or "记忆" in text or "翻" in text:
        return "memory"
    if "sit" in text or "发呆" in text or "不" in text:
        return "sit"
    # 模型没给清楚答案时的兜底
    if surf_available and agent.mem.state >= 45:
        return "surf"
    return "memory" if agent.mem.state >= 35 else "sit"


def run_daily(agent) -> dict | None:
    """跑当天的一次活动；已经做过/状态太差/已有 pending 就跳过。"""
    if not getattr(agent.cfg, "daily_activity_enabled", True):
        return None
    st = agent.mem.daily_activity_state()
    if st.get("done"):
        return None
    if agent.mem.state < 30:
        return None
    w = getattr(agent, "wanderer", None)
    if w is not None:
        idle = max(0.05, float(agent.cfg.wander_idle_minutes)) * 60.0
        if w.idle_seconds() < idle:
            return None

    surf_available = not st.get("surf_done")
    kind = _choose(agent, surf_available)

    if kind == "surf":
        notes = surf.run_session(agent, limit=2)
        if notes:
            text = "我在网上看到：" + "；".join(
                n["note"] for n in notes[:2])[:220]
            agent.mem.add_pending("web", text, "今天自己上网看到")
            st["surf_done"] = True
        else:
            kind = "memory"       # 网络/摘要失败，改翻旧记忆

    if kind == "memory":
        motifs = agent.mem.motif_list(1)
        note = (motifs[0].get("note") or "").strip() if motifs else ""
        if note:
            agent.mem.add_pending(
                "memory", f"翻到一段旧的：{note[:100]}", "今天自己翻旧记忆")
        else:
            kind = "sit"

    st["kind"] = kind
    st["done"] = True
    st["at"] = time.time()
    agent.mem.mark_daily_activity(st)
    return {"kind": kind, "surf_done": bool(st.get("surf_done", False))}
