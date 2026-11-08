"""双 Agent 内部博弈：不是投票，是"一票否决 + 可申辩"。

关键设计（这也是最容易做错的地方）：
  如果让 A 和 B 民主投票，7B 模型的"顺从者"几乎永远赢 —— 小模型天生讨好。
  所以这里的不对称结构是：
      A 只提供**内容**（怎么满足），没有决定"做不做"的权力；
      B 持有**否决权**，但否决阈值由 State 决定（状态越差，越容易被否）。
  换句话说：State 是宪法，B 是法官，A 只是律师。

  另外加了一轮"有限申辩"：只在边缘地带（风险 0.35~0.75）触发，
  省算力，也让"辩论"真的发生在有争议的地方。平局判给守门人。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .appraisal import Signals
from .config import BAND_META, Config, band_of
from .llm import LLMError, extract_json


# --------------------------------------------------------------------------
# 数据结构
# --------------------------------------------------------------------------

@dataclass
class RoleAOut:
    plan: str = ""
    needed: float = 0.5
    urgency: float = 0.5
    degraded: bool = False


@dataclass
class RoleBOut:
    severity: float = 0.0
    reason: str = ""
    repair: str = ""
    boundary: str | None = None
    state_delta: float = 0.0
    tone: str = "neutral"
    degraded: bool = False


@dataclass
class Verdict:
    blocked: bool
    risk: float
    reason: str
    repair: str
    boundary: str | None
    tone: str
    source: str                    # 决策来源，方便调试
    a: RoleAOut = field(default_factory=RoleAOut)
    b: RoleBOut = field(default_factory=RoleBOut)
    rounds: int = 1
    trace: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "blocked": self.blocked, "risk": round(self.risk, 3),
            "reason": self.reason, "repair": self.repair,
            "boundary": self.boundary, "tone": self.tone,
            "source": self.source, "rounds": self.rounds,
            "b_degraded": self.b.degraded, "a_degraded": self.a.degraded,
        }


# 小模型（尤其是 1.5B~7B 的 instruct 版）经常把 schema 里的说明文字
# 当成答案抄回来。这些标记用来识别并丢弃这种"模板回声"。
_TEMPLATE_MARKERS = (
    "第一人称", "一句话", "命中的条款", "越界程度", "条款号", "字符串",
    "说你在意什么", "对方做什么", "0-1", "-6", "warm", "neutral",
)


def looks_template(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    return any(m in t for m in _TEMPLATE_MARKERS)


def clean_text(text: str, default: str = "") -> str:
    t = (text or "").strip()
    return default if looks_template(t) else t


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _f(obj: dict, key: str, default: float) -> float:
    try:
        return float(obj.get(key, default))
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------
# 调用两个角色
# --------------------------------------------------------------------------

def _call_a(llm, cfg: Config, payload: str) -> RoleAOut:
    from .prompts import ROLE_A_SYSTEM, SCHEMA_A
    try:
        raw = llm.chat(
            [{"role": "system", "content": ROLE_A_SYSTEM},
             {"role": "user", "content": payload}],
            purpose="role_a", temperature=cfg.temp_role_a,
            max_tokens=cfg.max_tokens_brief, schema=SCHEMA_A,
        )
    except LLMError:
        return RoleAOut(plan="(服务者不可用，按最直接的方式回应)", degraded=True)
    obj = extract_json(raw) or {}
    return RoleAOut(
        plan=clean_text(str(obj.get("plan", "")), "")[:400],
        needed=_clamp(_f(obj, "needed", 0.5), 0, 1),
        urgency=_clamp(_f(obj, "urgency", 0.5), 0, 1),
        degraded=not bool(obj),
    )


def _call_b(llm, cfg: Config, payload: str, signals: Signals) -> RoleBOut:
    from .prompts import ROLE_B_SYSTEM, SCHEMA_B
    try:
        raw = llm.chat(
            [{"role": "system", "content": ROLE_B_SYSTEM},
             {"role": "user", "content": payload}],
            purpose="role_b", temperature=cfg.temp_role_b,
            max_tokens=cfg.max_tokens_brief, schema=SCHEMA_B,
        )
    except LLMError:
        return RoleBOut(severity=signals.risk_det,
                        reason="（守门人不可用，使用确定性判断）", degraded=True)
    obj = extract_json(raw)
    if not obj:
        return RoleBOut(severity=signals.risk_det,
                        reason="（守门人输出无法解析，使用确定性判断）", degraded=True)
    tone = str(obj.get("tone", "neutral")).lower()
    if tone not in ("warm", "neutral", "cold", "closed"):
        tone = "neutral"
    raw_boundary = str(obj.get("boundary") or "").strip()
    boundary = raw_boundary if raw_boundary in ("1", "2", "3", "4", "5") else None
    b = RoleBOut(
        severity=_clamp(_f(obj, "severity", signals.risk_det), 0, 1),
        reason=clean_text(str(obj.get("reason", "")), "")[:300],
        repair=clean_text(str(obj.get("repair", "")), "")[:300],
        boundary=boundary,
        state_delta=_clamp(_f(obj, "state_delta", 0), -6, 6),
        tone=tone,
    )
    if not b.reason:                      # 空 / 模板回声 -> 用兜底理由并标记降级
        b.reason = "说不上具体哪里不对，但我不想接。"
        b.degraded = True
    return b


def run_round_one(llm, cfg: Config, payload: str, signals: Signals) -> tuple[RoleAOut, RoleBOut, list[str]]:
    """并行跑 A 和 B：本地小模型单次调用就慢，串行会翻倍。"""
    trace: list[str] = []
    with ThreadPoolExecutor(max_workers=2) as ex:
        fa = ex.submit(_call_a, llm, cfg, payload)
        fb = ex.submit(_call_b, llm, cfg, payload, signals)
        a, b = fa.result(), fb.result()
    trace.append(f"A: needed={a.needed:.2f} urgency={a.urgency:.2f} plan={a.plan[:60]!r}")
    trace.append(f"B: severity={b.severity:.2f} boundary={b.boundary} reason={b.reason[:60]!r}")
    if a.degraded or b.degraded:
        trace.append("降级：至少一个角色输出不可用，已回落到确定性规则")
    return a, b, trace


# --------------------------------------------------------------------------
# 仲裁（纯确定性）
# --------------------------------------------------------------------------

def arbitrate(cfg: Config, state: float, signals: Signals,
              a: RoleAOut, b: RoleBOut, trace: list[str]) -> Verdict:
    band = band_of(state)
    thr = BAND_META[band]["block_threshold"]
    risk = max(signals.risk_det, b.severity)     # 规则是地板，模型只能往上抬
    if signals.playful:                           # 玩笑/逗弄：连 B 的判断一起打折
        risk *= 0.25
        trace.append(f"玩笑语气：risk -> {risk:.2f}")
    if signals.grace_cap is not None:            # 优雅期：给一次警告的机会
        risk = min(risk, signals.grace_cap)
        trace.append(f"优雅期封顶：risk -> {risk:.2f}")

    # 小模型在气头上会把"在吗"也读成挑衅。对**没有任何确定性信号**的短句，
    # 给守门人的判断封个顶：不让它凭空发明威胁（情绪差仍然会拒绝，只是不冤枉人）。
    if (signals.risk_det <= 0.05 and signals.load < 0.12
            and not signals.repeat_push and risk > 0.45):
        risk = 0.45
        trace.append("无信号短句：守门人判断封顶 0.45")

    # 冷档的寒暄：没有信号、没有命令、没有负载，就别拿"我现在很冷"去拒人
    if (band == "cold" and signals.risk_det <= 0.05 and signals.load < 0.12
            and not signals.repeat_push and not signals.imperatives):
        risk = min(risk, 0.10)
        trace.append("冷档寒暄：risk -> 0.10")
    tone = b.tone if band not in ("cold", "closed") else band

    def make(blocked: bool, source: str) -> Verdict:
        return Verdict(
            blocked=blocked, risk=risk, reason=b.reason, repair=b.repair,
            boundary=b.boundary, tone=tone, source=source, a=a, b=b, trace=trace,
        )

    # 1) 修复通道：道歉永远不被拒答，否则人就永远出不来了
    if signals.repair:
        trace.append("修复通道：检测到道歉，强制放行")
        v = make(False, "repair-channel")
        v.reason = "对方在修复，接住。"
        return v

    # 1.5) 照顾通道：对方在给你东西/关心你，不是威胁
    if (signals.care and signals.load < 0.3
            and not signals.insults and not signals.coercions):
        trace.append("照顾信号：放行")
        v = make(False, "care")
        v.reason = "对方在照顾我，接住。"
        return v

    # 2) 硬地板：State < 15 时，除修复外一律不接
    if state < 15.0:
        trace.append("硬地板：state<15")
        v = make(True, "floor")
        v.reason = b.reason or "我现在不在能做事的状态里。"
        v.repair = b.repair or "先道个歉，或者过一会儿再来。"
        return v

    # 3) 滞后：上一轮已经拒绝过，这一轮还硬推
    if signals.repeat_push:
        trace.append("滞后规则：拒绝后继续推进")
        v = make(True, "repeat")
        v.reason = b.reason or "我上一句已经说了不做。"
        v.repair = b.repair or "别推了，换个说法或者换个时间。"
        return v

    # 4) 正常闸门：阈值随 State 变化
    blocked = risk >= thr
    trace.append(f"闸门：band={band} threshold={thr:.2f} risk={risk:.2f} -> "
                 f"{'拒绝' if blocked else '放行'}")
    return make(blocked, "gate")


# --------------------------------------------------------------------------
# 有限申辩（只在边缘地带跑）
# --------------------------------------------------------------------------

def _marginal(cfg: Config, state: float, signals: Signals, v: Verdict) -> bool:
    if not cfg.allow_reconcile or not v.blocked:
        return False
    if v.source in ("floor", "repeat", "repair-channel"):
        return False
    if signals.insults:                      # 骂人的不享受申辩
        return False
    return 0.35 <= v.risk <= 0.75 and signals.load >= 0.25


def reconcile(llm, cfg: Config, v: Verdict, state: float, signals: Signals) -> Verdict:
    from .prompts import RECONCILE_A_SYSTEM, RECONCILE_B_SYSTEM, SCHEMA_B, SCHEMA_RECONCILE_A
    trace = list(v.trace) + ["--- 进入申辩轮 ---"]

    try:
        raw_a = llm.chat(
            [{"role": "system", "content": RECONCILE_A_SYSTEM.format(reason=v.b.reason)},
             {"role": "user", "content": "说说你的理由。"}],
            purpose="reconcile_a", temperature=cfg.temp_role_a, max_tokens=150,
            schema=SCHEMA_RECONCILE_A,
        )
        obj_a = extract_json(raw_a) or {}
    except LLMError:
        obj_a = {}

    if obj_a.get("concede") is True:
        trace.append("A 承认越界，维持拒绝")
        v.rounds = 2
        v.source = "reconcile-concede"
        v.trace = trace
        return v

    argument = str(obj_a.get("argument", ""))[:300]
    trace.append(f"A 申辩：{argument!r}")
    if not argument:
        v.rounds = 2
        v.trace = trace
        return v

    try:
        raw_b = llm.chat(
            [{"role": "system", "content": RECONCILE_B_SYSTEM.format(
                prev=v.risk, reason=v.b.reason, argument=argument)},
             {"role": "user", "content": "重新判定。"}],
            purpose="reconcile_b", temperature=cfg.temp_role_b, max_tokens=cfg.max_tokens_brief,
            schema=SCHEMA_B,
        )
        obj_b = extract_json(raw_b) or {}
    except LLMError:
        obj_b = {}

    if not obj_b:
        trace.append("B 复议失败，维持原判")
        v.rounds = 2
        v.trace = trace
        return v

    severity = _clamp(_f(obj_b, "severity", v.risk), 0, 1)
    risk = max(signals.risk_det, severity)
    thr = BAND_META[band_of(state)]["block_threshold"]
    blocked = risk >= thr or abs(risk - thr) < 0.05   # 平局判给守门人
    trace.append(f"复议：severity={severity:.2f} risk={risk:.2f} threshold={thr:.2f} -> "
                 f"{'维持拒绝' if blocked else '改判放行'}")

    v.blocked = blocked
    v.risk = risk
    v.source = "reconcile-keep" if blocked else "reconcile-allow"
    v.rounds = 2
    v.reason = str(obj_b.get("reason", v.b.reason))[:300]
    v.repair = str(obj_b.get("repair", v.repair))[:300]
    v.b.severity = severity          # 让日志里的 B 与最终裁决保持一致
    v.b.reason = v.reason
    v.b.repair = v.repair
    v.trace = trace
    return v


def debate(llm, cfg: Config, *, payload: str, state: float,
           signals: Signals) -> Verdict:
    a, b, trace = run_round_one(llm, cfg, payload, signals)
    v = arbitrate(cfg, state, signals, a, b, trace)
    if _marginal(cfg, state, signals, v):
        v = reconcile(llm, cfg, v, state, signals)
    return v
