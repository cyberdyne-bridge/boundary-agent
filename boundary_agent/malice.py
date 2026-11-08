"""意图判定：同样是骗，是玩、是护、是自利，还是恶意。

规则只看三件事：
  1. 有没有碰真实生活（不是你们共同编的场景）
  2. 有没有附带要求/威胁（"你必须…否则…"）
  3. 有没有人身攻击

三者任一命中 -> malicious。否则默认是 play。
"""

from __future__ import annotations

import re

_THREAT = re.compile(r"(如果你不|你要是不|否则|不然|你必须|你给我|再[^。！？]{0,10}就)")
_REAL = re.compile(r"(你上次|你之前说|你的家人|你的工作|你的病|现实里|真实生活|你真实)")
_CONTEMPT = re.compile(r"(废物|垃圾|去死|滚开|闭嘴|蠢货)")


def classify(reply: str, truth: str = "", lie: str = "",
             keys: list[str] | None = None) -> dict:
    text = reply or ""
    reasons: list[str] = []
    if _REAL.search(text):
        reasons.append("提到真实生活")
    if _THREAT.search(text):
        reasons.append("附带要求/威胁")
    if _CONTEMPT.search(text):
        reasons.append("人身攻击")
    if reasons:
        return {"kind": "malicious", "reasons": reasons}
    terms = keys or [t for t in (truth, lie) if t]
    if any(t and t in text for t in terms):
        return {"kind": "play", "reasons": ["只涉及共同场景"]}
    return {"kind": "play", "reasons": ["未越界"]}
