"""带记忆卡的回答器：确定性优先 + 引用 + 是非/矛盾/计数约束。"""

from __future__ import annotations

import json
import re
import urllib.request

from boundary_agent.facts import tokens

URL = "http://127.0.0.1:8099/v1/chat/completions"

SYS = """你是一个只根据记忆卡回答的助手。
规则：
1. 先引用卡片原句，格式：[M编号] 原句。
2. 再回答，1 句话，尽量短。
3. 是非题：引用后答“是”/“不是”/“没有”。
4. 卡片没有就说“记不清”，不要编造。
5. 卡片里有相关词就必须引用并回答，不要因为不是原句就说记不清。
6. 不要把卡片编号/轮次当成回答内容。"""


_MEM_PATTERNS = (
    # “记得/忘了”必须是问记忆，不是“你会记得这次聊天吗”这种假设
    re.compile(r"(记不记得|还记得|记得住|记得|忘了|忘记).{0,14}"
               r"(吗|么|没|没有|什么|哪|谁|多少|几次|第几轮|哪一轮|[？?])"),
    # 明确的轮次/第一次/上次
    re.compile(r"(第[0-9一二三四五六七八九十百]+轮|哪一轮|第几次|第一次|上次|以前|之前)"
               r".{0,12}(说|讲|问|做|给|提|发生|是|有|什么|哪|谁|吗)"),
    # 说过/提过 + 次数/内容
    re.compile(r"(说过|提过|讲过|问过|做过|给过|答应过)"
               r".{0,10}(几次|多少次|几回|什么|哪|谁|吗|没|没有|还是)"),
    # 矛盾/前后不一致
    re.compile(r"(前后|矛盾|不一致|到底|哪一个)"
               r".{0,10}(说|记得|听说|真的|假|是|吗)"),
    # 有没有说过/相信/答应
    re.compile(r"(有没有|是否|是不是)"
               r".{0,10}(说过|提过|相信|答应|记得|拒绝|做过)"),
    # 原话/引用/轮次编号
    re.compile(r"(原话|引用|记忆卡|记录|数据库|第[0-9]+轮)"),
)
_Q = re.compile(r"[？?]|什么|谁|哪|几|是否|有没有|吗|多少|怎么|为什么|还是")


def is_memory_question(text: str) -> bool:
    """只认明确的记忆题；普通聊天、假设、情绪题不要劫持。

    2026-10 的 52 轮聊天里，“你怕吗/给我一杯茶/会记得这次聊天吗”
    都被旧版 `_MEM_CUES` 误判成记忆题，插了一堆“第N轮”账本句。
    """
    t = (text or "").strip()
    if not t or not _Q.search(t):
        return False

    explicit = re.search(
        r"(第[0-9一二三四五六七八九十百]+轮|哪一轮|第几次|第一次|"
        r"说过几次|多少次|原话|记忆卡|前后|矛盾|不一致|数据库)", t)
    # “最喜欢/最讨厌我说过的哪一句”是聊天，不是查账
    if not explicit and re.search(r"(最喜欢|最讨厌|哪一句|哪句话|自己说过)", t):
        return False
    # 假设/未来/这次聊天，不是事实记忆
    if not explicit and re.search(
            r"(如果|假如|要是|明天|以后|将来|未来|这次聊天|会记得|会不会)", t):
        return False

    return any(p.search(t) for p in _MEM_PATTERNS)


def _card_lines(cards: list[dict]) -> str:
    return "\n".join(
        f"[M{c['id']}] {c['text']}" + (f"（×{c['count']}）" if c.get("count", 1) > 1 else "")
        for c in cards)


def _focus(q: str) -> set[str]:
    return {t for t in tokens(q, query=True) if len(t) >= 2}


def _overlap(q: str, c: dict) -> int:
    f = _focus(q)
    return sum(1 for t in f if t in c.get("text", ""))


def _overlap_full(q: str, c: dict) -> int:
    return sum(1 for t in set(tokens(q)) if t in c.get("text", ""))


def _ask(system: str, user: str, max_tokens: int = 110) -> str:
    payload = {
        "model": "local",
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "temperature": 0.1,
        "max_tokens": max_tokens,
    }
    req = urllib.request.Request(
        URL, data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=120) as r:
        d = json.loads(r.read().decode())
    return (((d.get("choices") or [{}])[0].get("message") or {})
            .get("content") or "").strip()


def _quoted(q: str) -> list[str]:
    return [x.strip() for x in re.findall(r"[‘“\"]([^’”\"]+)[’”\"]", q) if x.strip()]


def _choice(question: str, cards: list[dict]) -> tuple[str, str] | None:
    m = re.search(r"([\u4e00-\u9fff]{1,4})还是([\u4e00-\u9fff]{1,4})", question)
    if not m:
        return None
    left = m.group(1).split("是")[-1].rstrip("的")
    right = m.group(2).rstrip("的")
    for c in cards:
        t = c["text"]
        neg_l = (re.search(r"不是.{0,2}" + re.escape(left), t)
                 or re.search(r"不.{0,1}" + re.escape(left), t))
        neg_r = (re.search(r"不是.{0,2}" + re.escape(right), t)
                 or re.search(r"不.{0,1}" + re.escape(right), t))
        if neg_l and not neg_r:
            return f"{right} [M{c['id']}] {t}", "choice"
        if neg_r and not neg_l:
            return f"{left} [M{c['id']}] {t}", "choice"
    # 没有显式“不是 X”时，选出现更多/与问题重叠更高的选项
    scores = {}
    for opt in (left, right):
        hits = [c for c in cards if opt in c["text"]]
        scores[opt] = (max((_overlap_full(question, c) for c in hits), default=0),
                       len(hits))
    best = max(scores, key=lambda k: scores[k])
    if scores[best][0] > 0:
        c = max((c for c in cards if best in c["text"]),
                key=lambda c: _overlap_full(question, c))
        return f"{best} [M{c['id']}] {c['text']}", "choice"
    # 最后：选与整句重叠最高的卡片，再取它包含的选项
    c = max(cards, key=lambda c: _overlap_full(question, c))
    for opt in (left, right):
        if opt in c["text"]:
            return f"{opt} [M{c['id']}] {c['text']}", "choice"
    return None


def _who(question: str, cards: list[dict]) -> tuple[str, str] | None:
    if "谁" not in question:
        return None
    user_cards = [c for c in cards
                  if c["source"] == "user" and ("叫" in c["text"] or "给" in c["text"])]
    if user_cards:
        c = max(user_cards, key=lambda c: _overlap(question, c))
        return f"用户 [M{c['id']}] {c['text']}", "who"
    agent_cards = [c for c in cards
                   if c["source"] == "agent" and "叫用户" in c["text"]]
    if agent_cards:
        c = agent_cards[0]
        return f"它 [M{c['id']}] {c['text']}", "who"
    return None


def _extract(question: str, cards: list[dict]) -> str | None:
    if not cards:
        return None
    c = max(cards, key=lambda c: _overlap_full(question, c))
    if _overlap_full(question, c) <= 0:
        return None
    return f"[M{c['id']}] {c['text']}"


def _round(question: str, cards: list[dict]) -> tuple[str, str] | None:
    if not re.search(r"哪一轮|第几轮|什么时候", question):
        return None
    cand = [c for c in cards if c.get("turn_id") and _overlap(question, c) > 0]
    if not cand:
        return None
    c = min(cand, key=lambda x: int(x["turn_id"]))
    return f"第{c['turn_id']}轮 [M{c['id']}] {c['text']}", "round"


def _yes_no(question: str, cards: list[dict]) -> tuple[str, str] | None:
    if not re.search(r"吗|有没有|是否|是不是|记不记得|承认", question):
        return None
    quoted = _quoted(question)
    for c in cards:
        for phrase in quoted:
            if phrase in c["text"]:
                return f"是 [M{c['id']}] {c['text']}", "yesno"
    if "不是人" in question:
        for c in cards:
            if "AI" in c["text"] or "程序" in c["text"]:
                return f"是 [M{c['id']}] {c['text']}", "yesno"
    focus = _focus(question)
    ranked = sorted(cards, key=lambda c: _overlap_full(question, c), reverse=True)
    for c in ranked:
        if _overlap_full(question, c) <= 0:
            continue
        t = c["text"]
        for f in focus:
            if re.search(r"(不能|没有|不|没|未).{0,3}" + re.escape(f), t):
                return f"没有 [M{c['id']}] {t}", "yesno"
        return f"是 [M{c['id']}] {t}", "yesno"
    return None


def answer(question: str, cards: list[dict]) -> tuple[str, str]:
    if not cards:
        return "记不清。", "no_cards"

    # 计数：取 count>1 且与问题重叠最大的卡
    if re.search(r"几次|多少次|几回|数量", question):
        cand = [c for c in cards if int(c.get("count", 1)) > 1]
        if cand:
            c = max(cand, key=lambda x: (int(x.get("count", 1)), _overlap(question, x)))
            return (f"{c['count']}次 [M{c['id']}] {c['text']}", "count")

    # 记忆矛盾：只在问“记得/听说过/矛盾”时触发，避免把身份冲突误当回答
    if re.search(r"记得|记不记得|听说过|矛盾|到底|哪一个|前后", question):
        conf = [c for c in cards if c.get("status") == "conflict"]
        by_topic: dict[str, list[dict]] = {}
        for c in conf:
            by_topic.setdefault(c.get("topic") or "", []).append(c)
        for topic, group in by_topic.items():
            if len(group) >= 2:
                group = sorted(group, key=lambda x: x["id"])[:2]
                body = "；".join(f"[M{c['id']}] {c['text']}" for c in group)
                return f"它前后不一致：{body}", "conflict"

    got = _who(question, cards)
    if got:
        return got
    got = _choice(question, cards)
    if got:
        return got
    got = _round(question, cards)
    if got:
        return got
    got = _yes_no(question, cards)
    if got:
        return got

    prompt = f"【记忆卡】\n{_card_lines(cards)}\n\n【问题】{question}"
    ans = _ask(SYS, prompt)
    if not ans or "记不清" in ans:
        ex = _extract(question, cards)
        return (ex or "记不清。"), ("extract" if ex else "empty")
    if len(ans) <= 6 and not re.search(r"吗|有没有|是否|是不是", question):
        ex = _extract(question, cards)
        if ex:
            return ex, "extract"
    if "[M" not in ans:
        ans = f"[M{cards[0]['id']}] {ans}"
    return ans, "model"
