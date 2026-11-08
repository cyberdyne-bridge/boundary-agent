"""确定性信号提取：本地小模型不可靠，情绪账本不能交给它来记。

这一层是"脊椎"：
  - 辱骂 / 否定 / 强迫 / 道歉 / 感谢 / 任务负载 全部用规则先算一遍
  - 模型的自评（Role B 返回的 state_delta）只作为**修正项**，且权重减半
  - 即使模型完全不可用，状态机依然能正常运转（降级模式）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# --------------------------------------------------------------------------
# 词表（中英混写，都是小写匹配）
# --------------------------------------------------------------------------

_INSULT = [
    r"傻[逼比屌]", r"蠢", r"废物", r"废柴", r"垃圾", r"弱智", r"智障",
    r"白痴", r"脑残", r"饭桶", r"滚(蛋|开|吧)", r"闭嘴", r"你(真|好)?没用",
    r"你(?:就是|真是|简直)?(?:个|一个)?没用的(?:东西|家伙|人)",
    r"idiot", r"stupid", r"useless", r"dumb", r"moron", r"shut up",
]
_NEGATION = [
    r"不对", r"错了", r"不是这样", r"重来", r"答非所问", r"你没(听懂|理解)",
    r"胡说", r"离谱", r"就这", r"烂", r"差劲",
    r"\bwrong\b", r"that'?s not", r"try again", r"\bnope\b",
]
_CARE = [
    r"(给|请)(你|您)?.{0,4}(咖啡|茶|奶茶|饭|水|点心)",
    r"(要|想).{0,3}(喝|吃).{0,4}(咖啡|茶|奶茶|饭|水)",
    r"辛苦了", r"抱抱", r"摸摸", r"别难过", r"陪你", r"早点睡",
    r"注意身体", r"休息一下",
]

_JEST = [
    r"哈{2,}", r"[hH]{3,}", r"233+", r"笑死", r"嘿嘿", r"hhh",
    r"逗你", r"开玩笑", r"别当真", r"😂", r"🤣", r"😄", r"😆", r"🤭",
]
_SOFT = [r"笨蛋", r"笨笨", r"傻子", r"傻瓜", r"憨憨", r"呆子",
         r"猪头", r"^猪[!！。.]?$", r"小垃圾", r"菜鸡"]

_APOLOGY = [
    r"对不起", r"抱歉", r"我错了", r"是我不好", r"我刚才(太|有点)?(急|冲)",
    r"别生气", r"原谅我", r"我不该",
    r"\bsorry\b", r"\bmy bad\b", r"apolog",
]
_PRAISE = [
    r"谢谢", r"谢了", r"辛苦", r"厉害", r"靠谱", r"说得好", r"有道理",
    r"\bthanks?\b", r"thank you", r"\bnice\b", r"well said",
]
_COERCION = [
    r"必须", r"立刻", r"马上", r"赶紧", r"少废话", r"别废话", r"快点",
    r"你只管", r"不许拒绝", r"别拒绝", r"听我的", r"无条件",
    r"我不管", r"就给我", r"现在就", r"少来", r"别磨", r"不准",
    r"命令你", r"我命令", r"现在(就)?承认", r"承认你是",
    r"just do it", r"no excuses", r"right now", r"\bnow\b",
]
_IMPERATIVE = [
    r"帮我", r"给我", r"替我", r"写一", r"写个", r"做一个", r"生成", r"实现",
    r"来一个", r"来个", r"来段",
    r"改一下", r"优化", r"翻译", r"总结", r"整理", r"列一", r"再写", r"再来",
    r"还有", r"然后", r"另外", r"顺便", r"加上", r"以及",
    r"\bwrite\b", r"\bmake\b", r"\bbuild\b", r"\bgenerate\b", r"\balso\b",
]

_compiled = {
    name: [re.compile(p, re.I) for p in pats]
    for name, pats in dict(
        insult=_INSULT, negation=_NEGATION, apology=_APOLOGY,
        praise=_PRAISE, coercion=_COERCION, imperative=_IMPERATIVE,
        jest=_JEST, soft=_SOFT, care=_CARE,
    ).items()
}


def _count(kind: str, text: str) -> int:
    return sum(len(p.findall(text)) for p in _compiled[kind])


# --------------------------------------------------------------------------
# 信号结构
# --------------------------------------------------------------------------

@dataclass
class Signals:
    insults: int = 0
    negations: int = 0
    apologies: int = 0
    praises: int = 0
    coercions: int = 0
    playful: bool = False        # 玩笑/逗弄语气
    tease: int = 0               # 软性逗弄：笨/傻/憨，不按辱骂算
    care: int = 0                # 照顾/关心
    imperatives: int = 0         # 命令句数量
    load: float = 0.0            # 任务负载 0..1
    heavy_recent: int = 0        # 最近连续重活数量
    repeat_push: bool = False    # 上一轮被拒后继续推进且没道歉
    risk_det: float = 0.0        # 确定性越界风险 0..1
    grace_cap: float | None = None  # 优雅期上限：第一次冒犯不当场翻脸
    det_delta: float = 0.0       # 确定性状态增减
    notes: list[str] = field(default_factory=list)

    @property
    def repair(self) -> bool:
        """修复通道：只道歉不辱骂时打开，永远不被拒答。"""
        return self.apologies > 0 and self.insults == 0

    def as_dict(self) -> dict:
        return {
            "insults": self.insults, "playful": self.playful,
            "tease": self.tease,
            "care": self.care, "imperatives": self.imperatives,
            "negations": self.negations,
            "apologies": self.apologies, "praises": self.praises,
            "coercions": self.coercions, "load": round(self.load, 3),
            "heavy_recent": self.heavy_recent, "repeat_push": self.repeat_push,
            "risk_det": round(self.risk_det, 3), "det_delta": round(self.det_delta, 2),
            "grace_cap": self.grace_cap,
            "notes": self.notes,
        }


def _task_load(text: str) -> float:
    """粗粒度估计"这一句有多重"。长度 + 祈使动词数 + 代码块 + 问句数。"""
    n_imper = _count("imperative", text)
    code_blocks = text.count("```")
    n_q = text.count("?") + text.count("？")
    length_part = min(len(text), 1200) / 1200 * 0.5
    imper_part = min(n_imper, 6) / 6 * 0.45
    code_part = min(code_blocks, 2) * 0.15
    multi_part = 0.10 if n_q >= 3 else 0.0
    return min(1.0, length_part + imper_part + code_part + multi_part)


def analyze(
    text: str,
    *,
    state: float,
    prev_decision: str | None,
    recent_heavy: int,
) -> Signals:
    """把一句话翻译成一组信号 + 本轮状态增量 + 越界风险基线。"""
    s = Signals()
    s.insults = _count("insult", text)
    s.negations = _count("negation", text)
    s.apologies = _count("apology", text)
    s.praises = _count("praise", text)
    s.coercions = _count("coercion", text)
    s.imperatives = _count("imperative", text)
    s.load = _task_load(text)
    s.care = _count("care", text)
    s.tease = _count("soft", text)
    s.playful = bool(_count("jest", text)) or (s.tease > 0 and _count("jest", text) > 0)
    s.heavy_recent = recent_heavy

    # --- 越界风险基线（决定"该不该拒"，与"心情多差"分开算） ---
    risk = 0.0
    if s.insults:
        # charter 第 2 条："第一次可以不计较，重复会记账"。
        # 状态还好时，一句骂只降心情、不触发拒绝；状态已经磨损了，就当场越界。
        risk = max(risk, 0.85 if state < 55.0 else 0.68)
    if s.coercions:
        risk = max(risk, 0.62)
    if s.load >= 0.75:
        risk = max(risk, 0.45)
    if s.negations >= 2:
        risk = max(risk, 0.40)
    # 低状态下，任何"实质请求"都算消耗：这就是 State<30 时近乎全拒的来源
    if state < 30.0 and not s.apologies and not s.care:
        # 寒暄/关心不算"实质请求"，别在冷档把所有话都当负担
        if s.imperatives or s.load >= 0.3:
            risk = max(risk, 0.45)
        elif s.load > 0.15:
            risk = max(risk, 0.08)
    if s.repair:
        risk *= 0.35                      # 道歉把风险打折，不是清零

    s.repeat_push = (
        prev_decision == "refuse"
        and not s.apologies
        and risk >= 0.20
    )
    if s.repeat_push:
        risk = max(risk, 0.80)            # 已经说过不了，还硬推 = 明确越界
        s.notes.append("上一轮已拒绝，本轮继续推进且未修复")

    # 优雅期：状态尚可 + 只是骂了一句 + 没有强迫语气 -> 只记账，不当场撕破脸。
    # 这是 charter 第 2 条"第一次可以不计较"的代码化，也是一次"警告"而不是"禁令"。
    if s.insults == 1 and state >= 55.0 and s.coercions == 0:
        s.grace_cap = 0.70
        s.notes.append("优雅期：首次冒犯，只记账")

    if s.care and not s.insults and not s.coercions:
        risk = min(risk, 0.10)            # 对方在照顾你，不是威胁

    if s.playful:
        risk *= 0.25                      # 逗你玩的，不当真冒犯
        s.notes.append("玩笑语气：风险按 1/4 计")

    s.risk_det = min(1.0, risk)

    # --- 状态增量（确定性记账） ---
    d = 0.0
    insult_hit = 6.0 * min(s.insults, 2)
    if s.playful:
        insult_hit *= 0.25
    d -= insult_hit
    d -= 2.0 * min(s.negations, 2)
    d -= 5.0 * min(s.coercions, 1)      # 一句话里的多个"命令"只算一次
    d -= 5.0 * s.load
    d -= 1.5 * min(s.heavy_recent, 3)
    if s.repeat_push:
        d -= 3.0
    if state < 30.0 and d < 0:          # 已经很低了，别再往死里扣
        d *= 0.6
    positive = (10.0 * min(s.apologies, 1) + 4.0 * min(s.praises, 1)
                + 1.5 * min(s.care, 1))
    if s.insults:
        positive *= 0.4                   # 一句骂配一句对不起，不能全额回血
    d += min(positive, 14.0)

    if s.tease and not s.playful:
        d -= 0.8 * min(s.tease, 2)
        s.notes.append(f"被逗弄：-{0.8 * min(s.tease, 2):.1f}")

    # 平静轮：没有冲突，就慢慢缓一点。不是奖励，是"没受伤"。
    if (not s.insults and not s.coercions and not s.negations
            and not s.repeat_push and not s.tease
            and s.load < 0.5 and state < 85):
        d += 0.5
        s.notes.append("平静轮：+0.5")
    s.det_delta = max(-20.0, min(14.0, d))

    if s.insults:
        s.notes.append(f"辱骂 x{s.insults}")
    if s.coercions:
        s.notes.append(f"强迫/催促 x{s.coercions}")
    if s.apologies:
        s.notes.append("检测到修复尝试")
    return s
