"""承诺：它会答应"明天告诉我"，然后真的记得问。

只认嘴边的话，不认心意：
  - "明天/下次/回头 + 告诉我/跟我说/说说"
  - "记得/别忘了 + 告诉我"
认出来就存一条，到点由系统保证问出口（模型忘了也会补一句）。
它也会说"改天聊"这种客套话——所以承诺本来就会失约，那是人的一部分。
"""

from __future__ import annotations

import re
import time

_SENT = re.compile(r"[^。！？!?\n]{4,60}")
_CUE = re.compile(r"(明天|下次|回头|改天|到时候|今晚|晚点|过两天)")
_VERB = re.compile(r"(告诉我|告诉你|跟我说|跟你说|说说|汇报|讲给我|讲给你|发给我|发我|回我)")
_SOFT = re.compile(r"(记得|别忘了)")
_ASK = re.compile(r"(结果|怎么样|后来|如何|进展|情况)")


def detect(text: str) -> dict | None:
    """从一句话里认领一个承诺。返回 {text, due_hours} 或 None。"""
    text = (text or "").strip()
    if not text:
        return None
    for sent in _SENT.findall(text):
        has_verb = bool(_VERB.search(sent))
        has_soft = bool(_SOFT.search(sent))
        if not (has_verb or (has_soft and _ASK.search(sent))):
            continue
        due = 12.0
        if "明天" in sent:
            due = 18.0
        elif "今晚" in sent:
            due = 4.0
        elif "过两天" in sent:
            due = 48.0
        elif _CUE.search(sent) or has_soft:
            due = 12.0
        else:
            continue
        return {"text": sent.strip()[:60], "due_hours": due}
    return None


def follow_up(sentences: list[str]) -> str:
    """到点了，替它说一句。保证问出口，不靠模型自觉。"""
    if not sentences:
        return ""
    topic = sentences[0]
    topic = re.sub(r"^(明天|下次|回头|改天|到时候|今晚|晚点|过两天)[，,]?", "", topic).strip()
    topic = topic.rstrip("。！？!?")
    return f"对了，{topic}——后来怎么样了？"
