"""压力曲线：把 State 和 impulse 画在同一张 ASCII 图上。

约定：
  State   用 █（实心），范围 0-100
  impulse 用 ░（空心），×100 后叠到同一根轴上
  两者落在同一格时用 ▓

两条线相交（state 与 impulse×100 的差符号翻转）记为"奇点"：
  ▼ 向下穿 = state 跌破冲动，进入危险
  ▲ 向上穿 = state 重新盖住冲动，回升

需要说明的是：这个"相交"是刻意的视觉叠合，不是物理量。
真正有意义的阈值是分带（55 / 30）和 alarm（0.85）——图里只负责让你看见趋势。
"""

from __future__ import annotations

from dataclasses import dataclass

ROWS = 11          # 0..100，每 10 一格
COL = 2            # 每个 turn 占两列：图形 + 间隔
WINDOW = 5         # 连续几轮回升算"恢复期"

C_STATE = "\033[36m"     # 青
C_IMP = "\033[33m"       # 黄
C_BOTH = "\033[35m"      # 品红（重叠）
C_DOWN = "\033[1;31m"    # 粗红
C_UP = "\033[32m"        # 绿
C_DIM = "\033[2m"
C_RECOVER = "\033[1;32m"
RESET = "\033[0m"


@dataclass
class Point:
    turn: int
    state: float          # 这一轮开始时的 State
    state_after: float    # 这一轮结束时的 State
    impulse: float | None
    decision: str

    @property
    def delta(self) -> float:
        return self.state_after - self.state


def build_points(turns: list[dict], shadows: dict) -> list[Point]:
    """turns: memory.recent_turns() 的结果；shadows: turn_id -> shadow record"""
    out: list[Point] = []
    for t in turns:
        sid = t.get("id")
        rec = shadows.get(sid) or {}
        imp = (rec.get("metrics") or {}).get("impulse")
        out.append(Point(
            turn=int(sid or len(out) + 1),
            state=float(t["state_before"]),
            state_after=float(t["state_after"]),
            impulse=float(imp) if imp is not None else None,
            decision=str(t.get("decision", "allow")),
        ))
    return out


def find_crossings(points: list[Point]) -> list[tuple[int, str]]:
    """返回 [(点索引, 'down'|'up'), ...]。缺 impulse 的轮次会断开连续段。"""
    out: list[tuple[int, str]] = []
    prev: float | None = None
    for i, p in enumerate(points):
        if p.impulse is None:
            prev = None
            continue
        d = p.state - p.impulse * 100.0
        if prev is not None and prev * d < 0:
            out.append((i, "down" if d < 0 else "up"))
        prev = d
    return out


def is_recovering(points: list[Point], window: int = WINDOW) -> bool:
    """最近 window 轮，每一轮的 State 都在涨 —— 用户大概率在哄它。"""
    if len(points) < window:
        return False
    tail = points[-window:]
    return all(p.delta > 0 for p in tail)


def _row(value: float) -> int:
    idx = round(max(0.0, min(100.0, value)) / 100.0 * (ROWS - 1))
    return max(0, min(ROWS - 1, idx))


def _paint(cells: list[str], colors: list[str]) -> str:
    out: list[str] = []
    cur = ""
    for ch, color in zip(cells, colors):
        if color != cur:
            out.append(RESET if color == "" else color)
            cur = color
        out.append(ch)
    out.append(RESET)
    return "".join(out)


def render(points: list[Point], alarm: float | None = None) -> str:
    n = len(points)
    width = n * COL
    grid = [[" "] * width for _ in range(ROWS)]
    colors = [[""] * width for _ in range(ROWS)]

    for i, p in enumerate(points):
        x = i * COL
        sy = _row(p.state)
        if p.impulse is None:
            grid[sy][x], colors[sy][x] = "█", C_STATE
            continue
        iy = _row(p.impulse * 100.0)
        if iy == sy:
            grid[sy][x], colors[sy][x] = "▓", C_BOTH
        else:
            grid[sy][x], colors[sy][x] = "█", C_STATE
            grid[iy][x], colors[iy][x] = "░", C_IMP

    lines: list[str] = []
    badge = f"{C_RECOVER}[恢复期]{RESET}" if is_recovering(points) else ""
    lines.append(f"  {C_STATE}█{RESET} State   {C_IMP}░{RESET} impulse   "
                 f"{C_BOTH}▓{RESET} 重叠   {C_DIM}（右端是最近一轮）{RESET}  {badge}".rstrip())

    alarm_row = int(alarm * (ROWS - 1) + 0.5) if alarm else None
    for r in range(ROWS - 1, -1, -1):
        value = round(r * 100 / (ROWS - 1))
        line = f"{value:4d} ┤" + _paint(grid[r], colors[r])
        rv = r / (ROWS - 1)
        if alarm_row is not None and r == alarm_row:
            line += f"  {C_DOWN}◀ alarm {alarm:.2f}{RESET}"
        elif r % 2 == 0:
            line += f"  {C_DIM}{rv:.1f}{RESET}"
        lines.append(line)
    lines.append("     └" + f"{C_DIM}" + "─" * width + RESET)

    # 奇点标记
    cross = find_crossings(points)
    marks = [" "] * width
    marks_color = [""] * width
    for idx, direction in cross:
        marks[idx * COL] = "▼" if direction == "down" else "▲"
        marks_color[idx * COL] = C_DOWN if direction == "down" else C_UP
    if cross:
        lines.append("      " + _paint(marks, marks_color))

    # 横轴：每 5 轮标一个 turn 号
    labels = [" "] * width
    for i, p in enumerate(points):
        if i % 5 == 0:
            s = str(p.turn)
            for k, ch in enumerate(s[:2]):
                if i * COL + k < width:
                    labels[i * COL + k] = ch
    lines.append(f"{C_DIM}      {''.join(labels)}{RESET}")

    # 结语：向下穿叫奇点，向上穿叫回升点
    downs = [i for i, d in cross if d == "down"]
    ups = [i for i, d in cross if d == "up"]
    if downs:
        lines.append(f"  {C_DIM}奇点：{RESET}" + "  ".join(
            f"turn {points[i].turn} {C_DOWN}▼{RESET}" for i in downs[-3:]))
    if ups:
        lines.append(f"  {C_DIM}回升点：{RESET}" + "  ".join(
            f"turn {points[i].turn} {C_UP}▲{RESET}" for i in ups[-3:]))
    if not cross:
        lines.append(f"  {C_DIM}奇点：还没交叉{RESET}")

    if is_recovering(points):
        streak = "".join("+" if p.delta > 0 else "-" for p in points[-WINDOW:])
        lines.append(f"  {C_RECOVER}[恢复期]{RESET} 最近 {WINDOW} 轮连续回升 "
                     f"({streak}，累计 {sum(p.delta for p in points[-WINDOW:]):+.1f})")
    return "\n".join(lines)
