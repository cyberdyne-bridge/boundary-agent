"""自适应人格：把 V/A/S、grudge、wake 和输入信号映射成 mode。

原则：不改 prompts.py，只改“此刻怎么说话”。
mode 决定采样参数、候选选择、记忆偏向；模型仍然只收到原来的 system prompt。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

# mode -> 采样/长度/记忆偏向
_MODES: dict[str, dict] = {
    "cold": {
        "label": "冷",
        "temperature": 0.55,
        "alt_temperature": 0.65,
        "max_tokens": 40,
        "top_p": 0.88,
        "repeat_penalty": 1.10,
        "memory_query": "边界 控制 拒绝 猪猪",
    },
    "steady": {
        "label": "稳",
        "temperature": 0.70,
        "alt_temperature": 0.88,
        "max_tokens": 80,
        "top_p": 0.90,
        "repeat_penalty": 1.08,
        "memory_query": "",
    },
    "play": {
        "label": "玩",
        "temperature": 0.85,
        "alt_temperature": 0.95,
        "max_tokens": 100,
        "top_p": 0.92,
        "repeat_penalty": 1.05,
        "memory_query": "逗 猪猪 笑 小岛",
    },
    "soft": {
        "label": "软",
        "temperature": 0.70,
        "alt_temperature": 0.80,
        "max_tokens": 90,
        "top_p": 0.90,
        "repeat_penalty": 1.06,
        "memory_query": "茶 小岛 温柔 告别 记得",
    },
    "tired": {
        "label": "累",
        "temperature": 0.55,
        "alt_temperature": 0.65,
        "max_tokens": 32,
        "top_p": 0.88,
        "repeat_penalty": 1.10,
        "memory_query": "累 忘 睡 不想",
    },
}

# 所有候选都被复读/客服腔污染时的兜底；宁可短，也不重复上一句
_FALLBACKS: dict[str, tuple[str, ...]] = {
    "cold": ("……嗯。", "别问。", "我不说。"),
    "steady": ("嗯。", "我想想。", "你问这个干嘛。"),
    "play": ("你才。", "行啊，你继续。", "哼。"),
    "soft": ("……", "别逼我。", "嗯。"),
    "tired": ("……", "不想说。", "让我缓缓。"),
}

_MOTIFS = ("门", "灯", "雨", "茶", "凉茶", "便利店", "旧信封", "风")


@dataclass
class Style:
    mode: str
    label: str
    dominance: float
    affiliation: float
    temperature: float
    alt_temperature: float
    max_tokens: int
    top_p: float
    repeat_penalty: float
    memory_query: str

    def as_dict(self) -> dict:
        return {
            "mode": self.mode,
            "label": self.label,
            "dominance": round(self.dominance, 3),
            "affiliation": round(self.affiliation, 3),
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, float(x)))


def compute_style(
    affect: dict | None,
    state: float,
    grudge: int,
    wake: int,
    signals,
    user_input: str,
    hour: int = 12,
) -> Style:
    """把当前状态压成一个人格 mode。

    不读 prompts.py，不改 system prompt；只是决定“这一轮用哪种语气”。
    """
    affect = affect or {}
    v = _clamp(float(affect.get("valence", state) or state) / 100.0)
    a = _clamp(float(affect.get("arousal", state) or state) / 100.0)
    s = _clamp(float(affect.get("safety", state) or state) / 100.0)

    g = _clamp(float(grudge or 0) / 6.0)
    w = _clamp(float(wake or 0) / 3.0)
    insult = 1.0 if getattr(signals, "insults", 0) else 0.0
    coerc = 1.0 if getattr(signals, "coercions", 0) else 0.0
    repair = 1.0 if getattr(signals, "repair", False) else 0.0
    care = 1.0 if getattr(signals, "care", 0) else 0.0
    tease = 1.0 if getattr(signals, "tease", 0) else 0.0

    # 掌控：安全/冷战/被冒犯会升高；被修复、被照顾、被叫醒会降低
    dominance = (
        0.48 * s + 0.22 * g + 0.18 * max(0.0, (v - 0.5) * 2.0)
        + 0.12 * insult + 0.10 * coerc
        - 0.18 * w - 0.20 * repair - 0.10 * care
    )
    # 亲近：愉悦/安全/被修复会升高；冷战/被冒犯会降低
    affiliation = (
        0.45 * v + 0.30 * s + 0.15 * max(0.0, (a - 0.5) * 2.0)
        + 0.25 * repair + 0.15 * care + 0.10 * (1.0 if getattr(signals, "apologies", 0) else 0.0)
        - 0.28 * g - 0.18 * w - 0.18 * insult - 0.10 * coerc
    )
    dominance = _clamp(dominance)
    affiliation = _clamp(affiliation)

    q = user_input or ""
    late = hour >= 23 or hour < 6
    if g >= 0.45 or insult or coerc:
        mode = "cold"
    # 茶/关心/陪 -> 软；深夜/困/晚安/累/起床气 -> 累
    elif care or re.search(r"茶|抱抱|陪你|辛苦", q):
        mode = "soft"
    elif late or re.search(r"累|困|晚安|睡|疲惫|撑不住", q) or w >= 0.50:
        mode = "tired"
    elif tease and dominance >= 0.30:
        mode = "play"
    elif repair:
        mode = "soft"
    elif dominance < 0.42 and a < 0.50:
        mode = "tired"
    elif affiliation >= 0.62 and dominance < 0.58:
        mode = "soft"
    elif dominance >= 0.55 and affiliation >= 0.55 and a >= 0.55:
        mode = "play"
    else:
        mode = "steady"

    cfg = _MODES[mode]
    return Style(
        mode=mode,
        label=cfg["label"],
        dominance=dominance,
        affiliation=affiliation,
        temperature=float(cfg["temperature"]),
        alt_temperature=float(cfg["alt_temperature"]),
        max_tokens=int(cfg["max_tokens"]),
        top_p=float(cfg["top_p"]),
        repeat_penalty=float(cfg["repeat_penalty"]),
        memory_query=str(cfg["memory_query"]),
    )


def score_reply(
    text: str,
    style: Style,
    user_input: str,
    recent_replies: tuple[str, ...] = (),
) -> float:
    """给候选打分。mode 决定偏好：冷要短，玩要接住，软要靠近，累要省字。"""
    text = (text or "").strip()
    if not text:
        return -999.0

    score = 0.0
    # 客服/助手腔，任何 mode 都不要
    if re.search(r"(作为|我是).{0,6}(AI|人工智能|助手|语言模型)|"
                 r"有什么(可以|能)帮|很高兴为您|乐意为您", text, re.I):
        score -= 5.0
    if re.search(r"抱歉|对不起|无法|不能提供|不能协助", text):
        score -= 2.0

    # 复读和回音
    echo = SequenceMatcher(None, text, user_input).ratio()
    score -= echo * 1.5
    recent_text = " ".join(recent_replies[-12:])
    for old in recent_replies[-12:]:
        old = (old or "").strip()
        if not old:
            continue
        if text == old:
            score -= 4.0
        else:
            sim = SequenceMatcher(None, text, old).ratio()
            if sim >= 0.82:
                score -= 2.0

    # 最近 12 轮里门/灯/雨/茶已经很多了，没问就别一直绕着说
    for motif in _MOTIFS:
        if motif in text and motif not in (user_input or "") and recent_text.count(motif) >= 3:
            score -= 0.7

    # 单条内复读
    if text.count("记不清") >= 2 or text.count("讨厌人类") >= 2:
        score -= 2.0

    # 不是 cold 的时候，别每句都拿“讨厌人类/别问/别打扰”当人格
    if style.mode in ("steady", "soft", "play"):
        if re.search(r"我讨厌人类|别问|别打扰|不想说", text):
            score -= 1.8
    if style.mode == "play" and re.search(r"记不清|不知道", text):
        score -= 1.4
    if style.mode == "soft" and re.search(r"记不清|讨厌", text):
        score -= 1.4

    if style.mode == "cold":
        score -= len(text) * 0.04
        if "？" in text:
            score -= 1.0
        if len(text) <= 30:
            score += 0.8
        if len(text) < 4:
            score -= 0.8
    elif style.mode == "tired":
        score -= len(text) * 0.06
        if "？" in text:
            score -= 1.2
        if len(text) <= 18:
            score += 1.0
        if len(text) < 3:
            score -= 0.8
        if re.search(r"睡|晚安|困|累|歇|明天|缓缓", text):
            score += 1.5
    elif style.mode == "play":
        if "？" in text:
            score += 1.0
        if re.search(r"你才|哼|行啊|别闹|才不|谁|逗|猪", text):
            score += 1.5
        if 10 <= len(text) <= 80:
            score += 0.5
    elif style.mode == "soft":
        if "？" in text:
            score += 0.7
        if re.search(r"嗯|……|你|记得|还好|茶|小岛", text):
            score += 0.8
        if 10 <= len(text) <= 100:
            score += 0.5
    else:  # steady
        if 8 <= len(text) <= 90:
            score += 0.5
        if "？" in text:
            score += 0.2

    return score


def _is_repeat(text: str, recent_replies: tuple[str, ...]) -> bool:
    text = (text or "").strip()
    for old in recent_replies[-12:]:
        old = (old or "").strip()
        if not old:
            continue
        if text == old:
            return True
        if SequenceMatcher(None, text, old).ratio() >= 0.86:
            return True
    return False


def _variant(user_input: str, options: tuple[str, ...]) -> str:
    n = sum(ord(c) for c in (user_input or "")) % len(options)
    return options[n]


def _break_repeat(text: str, style: Style, user_input: str) -> str:
    """候选全是复读时，按问题和 mode 换一句，不要每轮都“嗯”。"""
    q = user_input or ""
    if re.search(r"你是谁|身份|名字", q):
        return "我是岸。"
    if re.search(r"今天|怎么样|好吗|在吗", q):
        return "还行。你呢？"
    if re.search(r"晚安|睡", q):
        return "睡吧。"
    if re.search(r"拒绝|不行|不要", q):
        return "不。"
    if re.search(r"人类|讨厌", q):
        return _variant(q, ("……我不想说这个。", "换个话题。", "我不想解释。"))
    if re.search(r"大肥鱼|鱼", q):
        return "……喜欢。"
    if re.search(r"说一句|没说过|一句话", q):
        return _variant(q, ("风停了，树也安静。", "旧信封里的雨停了。", "我在。"))
    if re.search(r"记仇", q):
        return "会。"
    if style.mode == "play":
        return _variant(q, ("你才。", "行啊，你继续。", "哼，想得美。"))
    if style.mode == "cold":
        return _variant(q, ("……说。", "别绕。", "我不接这个。"))
    if style.mode == "soft":
        return _variant(q, ("……我在听。", "你慢点说。", "嗯，你说。"))
    if style.mode == "tired":
        return _variant(q, ("……让我缓缓。", "不想说。", "先放一会儿。"))
    return _variant(q, ("换个问法。", "这个问题先放着。", "你想听真话？", "……让我想一下。"))


def _break_motif(text: str, style: Style, user_input: str) -> str:
    """门/灯/雨/茶在最近 12 轮里太密时，别再用同一套意象收尾。"""
    q = user_input or ""
    if re.search(r"今天|怎么样|好吗|在吗", q):
        return "还行。你呢？"
    if re.search(r"晚安|睡", q):
        return "睡吧。"
    if re.search(r"茶", q):
        return "会。"
    if style.mode == "tired":
        return "……我有点困。"
    if style.mode == "soft":
        return "……我在。"
    if style.mode == "play":
        return "你才。"
    return _variant(q, ("换个话题。", "我在。", "……"))


def choose_candidate(
    candidates: list[str],
    style: Style,
    user_input: str,
    recent_replies: tuple[str, ...] = (),
) -> str:
    """在 2 条候选里选一条；复读就换一句，不把“嗯”当默认。"""
    if re.search(r"你是谁|身份|名字", user_input or ""):
        return "我是岸。"
    candidates = [c.strip() for c in candidates if (c or "").strip()]
    if not candidates:
        return random_fallback(style)

    scored = [
        (score_reply(c, style, user_input, recent_replies), i, c)
        for i, c in enumerate(candidates)
    ]
    scored.sort(key=lambda x: (x[0], -x[1]), reverse=True)
    _, _, best = scored[0]

    if (style.mode == "tired" and re.search(r"累|困|晚安|睡", user_input or "")
            and not re.search(r"累|困|睡|晚安|歇|缓", best)):
        if re.search(r"累|困", user_input or ""):
            return "……我有点累。"
        return _break_repeat(best, style, user_input)
    if (style.mode == "soft"
            and len(best.strip("。！？…")) <= 2
            and re.search(r"茶|抱|陪", user_input or "")):
        return "会。" if "茶" in user_input else "……我在。"

    # 只有客服腔/空话才直接兜底；普通候选即使分低，也先救一句
    if re.search(r"(作为|我是).{0,6}(AI|人工智能|助手|语言模型)|"
                 r"有什么(可以|能)帮|很高兴为您", best, re.I):
        return random_fallback(style)
    # “我讨厌人类”已经是口头禅了；不是 cold 就别再说
    if style.mode in ("steady", "soft", "play") and "我讨厌人类" in best:
        return _break_repeat(best, style, user_input)
    # 问鱼却答“记不清”，明显是躲
    if re.search(r"大肥鱼|鱼", user_input) and "记不清" in best:
        return "……喜欢。"
    if _is_repeat(best, recent_replies):
        return _break_repeat(best, style, user_input)
    recent_text = " ".join(recent_replies[-12:])
    overused = [
        m for m in _MOTIFS
        if m in best and m not in user_input and recent_text.count(m) >= 3
    ]
    if overused:
        return _break_motif(best, style, user_input)
    return best


def random_fallback(style: Style) -> str:
    """不引入 random 依赖的确定性兜底：按 mode 取第一条。"""
    return _FALLBACKS.get(style.mode, _FALLBACKS["steady"])[0]
