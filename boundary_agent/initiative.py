"""主动：没人问的时候，它自己开口说一句。

三条底线：
  1. 必须有素材（真看过新闻 / 真复盘过 / 状态真变了），不凭空抒情。
  2. 不索取。说完就完了，不问"你在吗"，不等你回复。
  3. 可以不说。门槛没到就闭嘴——沉默是默认值，不是失败。

所以这里不调用模型：素材是真的，句子用模板拼，宁可朴素也不编。
"""

from __future__ import annotations

import json


def _short(text: str, n: int = 40) -> str:
    """按句子截断，别在逗号中间砍一刀。"""
    text = (text or "").strip().replace("\n", " ")
    if len(text) <= n:
        return text
    cut = text[:n]
    idx = max(cut.rfind("。"), cut.rfind("！"), cut.rfind("？"))
    if idx >= 8:
        return cut[:idx]
    return cut + "…"


def material(mem) -> dict | None:
    """找出一个"值得说"的由头。没有就返回 None。"""
    st = mem.initiative_state()

    # 1) 有新复盘（它想明白了一件事）
    last_refl = mem.last_reflection_turn()
    if last_refl > int(st.get("reflect_turn", 0)):
        texts = mem.recent_reflections(1)
        if texts and texts[0].strip():
            return {"kind": "reflection", "text": texts[0], "turn": last_refl}

    # 2) 上次自己花钱看了新闻，还没跟人提过
    turns = mem.recent_turns(1)
    if turns:
        t = turns[-1]
        try:
            meta = json.loads(t.get("meta") or "{}")
        except (TypeError, ValueError):
            meta = {}
        sp = meta.get("self_spend") or {}
        if (sp.get("decision") == "spend" and sp.get("titles")
                and int(t["id"]) > int(st.get("news_turn", 0))):
            return {"kind": "news", "titles": sp["titles"], "turn": int(t["id"])}

    # 3) 没有新素材，但上次那条一直没人理 -> 就着同一件事再说一遍
    if int(st.get("ignored", 0)) >= 1:
        last = st.get("last_material")
        if isinstance(last, dict) and last.get("kind"):
            return {**last, "repeat": True}

    # 4) 状态自己缓回来了一截
    prev = float(st.get("state") or 0.0)
    if prev > 0 and mem.state - prev >= 6:
        return {"kind": "recovered", "delta": mem.state - prev, "turn": 0}
    return None


# 被无视的次数越多，这几句越不耐烦。第 6 次开始不说话了，只留一句冷的。
_LEVELS = {
    1: "{body}也没别的事。",
    2: "还在忙？{body}",
    3: "你倒是回一句啊。{body}",
    4: "我说了这么多，你一个字都不回。{body}",
    5: "行吧，你忙。{body}",
}


def _body(mat: dict) -> str:
    kind = mat.get("kind")
    if kind == "reflection":
        text = _short(mat.get("text", ""), 60).rstrip("。")
        return f"我这两天想明白一件事：{text}。"
    if kind == "news":
        titles = [t for t in (mat.get("titles") or []) if t]
        if not titles:
            return ""
        if len(titles) >= 2:
            return (f"你不在的时候我看了眼新闻：{_short(titles[0])}。"
                    f"还有一条，{_short(titles[1])}。")
        return f"你不在的时候我看了眼新闻：{_short(titles[0])}。"
    if kind == "recovered":
        return "缓过来点了。刚才那阵有点低。"
    return ""


def compose(mat: dict, ignored: int = 1) -> tuple[str, str]:
    """返回 (说出口的话, 给日志看的原因)。模板，不编。"""
    body = _body(mat)
    if not body and ignored <= 5:
        return "", ""
    reason = {"reflection": "复盘有新结论", "news": "自己花钱看了新闻",
              "recovered": f"状态回升 {mat.get('delta', 0):+.1f}"}.get(mat.get("kind"), "")
    if ignored >= 6:
        return "算了。当我没说。", (reason + f"（被无视 {ignored - 1} 次）").strip()
    text = _LEVELS.get(ignored, _LEVELS[1]).format(body=body)
    if ignored > 1:
        reason += f"（被无视 {ignored - 1} 次）"
    return text, reason
