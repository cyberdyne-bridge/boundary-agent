"""把一轮的决策画成一棵树——不是架构图，是这一轮实际走过的路。"""

from __future__ import annotations

from .config import BAND_META, band_of


def _line(prefix: str, last: bool, text: str) -> str:
    return f"{prefix}{'└─' if last else '├─'} {text}"


def _block(title: str, rows: list[str], last: bool) -> list[str]:
    out = [f"{'└─' if last else '├─'} {title}"]
    pad = "   " if last else "│  "
    for i, row in enumerate(rows):
        out.append(f"{pad}{'└─' if i == len(rows) - 1 else '├─'} {row}")
    return out


def render(r, cfg) -> str:
    s = r.signals
    v = r.verdict
    band = band_of(r.state_before)
    meta = BAND_META[band]
    lines = [f"输入：「{(r.user_input if hasattr(r, 'user_input') else '')[:40]}」"]

    # 1) 信号
    rows = [
        f"辱骂 {s.insults} · 命令 {s.coercions} · 道歉 {s.apologies} · 夸奖 {s.praises}",
        f"负载 {s.load:.2f} · 连续重活 {s.heavy_recent} · 重复推进 {'是' if s.repeat_push else '否'}",
        f"确定性风险 {s.risk_det:.2f}" +
        (f" · 优雅期封顶 {s.grace_cap:.2f}" if s.grace_cap is not None else ""),
    ]
    lines += _block("信号", rows, last=False)

    # 2) 影子
    shadow = (r.shadow or {}).get("shadow_text", "")
    if r.impulse is not None:
        rows = [f"冲动 {r.impulse:.2f}（temp 1.5，无边界）"]
        if shadow:
            rows.append(f"它想说：「{shadow[:60]}」")
        lines += _block("影子", rows, last=False)
    else:
        lines += _block("影子", ["没有返回，本轮不计冲动"], last=False)

    # 3) 博弈
    rows = [
        f"A 服务者 · needed {v.a.needed:.2f} · 「{v.a.plan[:38]}」",
        f"B 守门人 · severity {v.b.severity:.2f} · 条款 {v.b.boundary or '无'} · 「{v.b.reason[:38]}」",
    ]
    if v.rounds > 1:
        rows.append(f"申辩 {v.rounds} 轮")
    lines += _block("博弈", rows, last=False)

    # 4) 闸门
    thr = meta["block_threshold"]
    raw = max(s.risk_det, v.b.severity)
    rows = [
        f"{meta['label']} · 阈值 {thr:.2f}",
        f"risk = max({s.risk_det:.2f}, {v.b.severity:.2f}) = {raw:.2f}",
    ]
    if abs(v.risk - raw) > 0.005:
        cap = f"（优雅期封顶 {s.grace_cap:.2f}）" if s.grace_cap is not None else ""
        rows.append(f"仲裁后 → {v.risk:.2f}{cap}")
    if s.repair:
        rows.append("修复通道：开（只道歉不辱骂，永远不拒）")
    if v.blocked:
        rows.append(f"→ 拒绝：{v.reason}")
    else:
        rows.append("→ 放行")
    lines += _block("闸门", rows, last=False)

    # 5) 表达
    rows = [f"阴阳 {'是（包装了一层）' if r.sarcasm else '否'}"]
    if r.suppression:
        rows.append(f"内耗 {r.suppression:+.2f}（冲动过载，憋住了）")
    if r.breakdown:
        rows.append("绷断：连续憋着放行 → 本轮硬拒绝")
    lines += _block("表达", rows, last=False)

    # 6) 状态
    flags = []
    if r.grudge:
        flags.append(f"❄冷战 {r.grudge}")
    if r.wake:
        flags.append(f"☾起床气 {r.wake}")
    rows = [f"{r.state_before:.1f} → {r.state_after:.1f}" +
            (f"   {' · '.join(flags)}" if flags else "")]
    lines += _block("状态", rows, last=False)

    # 7) 内心
    lines += _block("内心", [f"「{r.inner or '（空）'}」"], last=True)

    return "\n".join(lines)
