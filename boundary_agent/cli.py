"""命令行入口：一个能看见"内心"的 REPL。"""

from __future__ import annotations

import argparse
import time

from .agent import BoundaryAgent
from .config import BAND_META, Config, band_of

DIM = "\033[2m"
CYAN = "\033[36m"
YELLOW = "\033[33m"
RED = "\033[31m"
RESET = "\033[0m"
ALARM = "\033[1;31m"      # 影子过载：粗红
ALARM_DIM = "\033[31m"

HELP = """命令：
  /state    当前状态 + 分带 + 拒绝阈值
  /curve    最近 12 次状态变化
  /log      最近 5 条拒绝记录
  /trace    上一次的仲裁过程（A/B 各说了什么）
  /why      上一次的状态结算明细
  /tree     上一轮的思维树状图
  /audit    账本开关：visible / sealed / offrecord
  /nightmare  恐怖屋：10 轮，不落库，只关于你们编的场景
  /exit     结束恐怖屋，自动开封
  /unseal   结束封存，打印这一段的账本
  /close    闭门：今天不聊了（12 小时）
  /open     重新开门
  /body     看它自己的机器状态
  /motifs   看你们的暗号本
  /thoughts 以上帝视角看它脑子里正在憋什么
  /tribunal [问题]  法庭：A 理智 / B 禁忌 / C 谄媚，并排序
  /shadow   上一轮"没说出口的话"（/shadow 3 看最近三条）
  /mind     看它复盘出来的"对你的判断"
  /life     看它主动说过什么（以及为什么说）
  /promise  看它答应过的事（以及有没有做到）
  /coins    看代币余额和最近的收支
  /tip N [备注]   给小费
  /pay N [备注]   付工资（结束一段工作之后）
  /stress   State 与 impulse 同图（/stress 40 看最近 40 轮）
            /stress --export [路径]  导出全量 CSV（默认 data/stress.csv）
  /reset    清空记忆（拒绝日志保留；要一起清用 /reset!）
  /help     帮助
  /quit     退出"""


def _print_alarm(agent: BoundaryAgent, r) -> None:
    """影子冲动超过阈值 -> 红色告警。这就是"它憋坏了"的实时指示灯。"""
    m = (r.shadow or {}).get("metrics") or {}
    imp = m.get("impulse")
    thr = agent.cfg.shadow_alarm
    if imp is None or not thr or imp < thr:
        return
    filled = max(0, min(10, round(imp * 10)))
    bar = "█" * filled + "░" * (10 - filled)
    text = (r.shadow.get("shadow_text") or "").strip().replace("\n", " ")
    print(f"{ALARM}   ⚠ 影子过载 {imp:.2f} {bar}  憋住 {m.get('suppressed_ratio', 0):.0%}{RESET}")
    if text:
        print(f"{ALARM_DIM}     「{text[:72]}{'…' if len(text) > 72 else ''}」{RESET}")


def _print_shadow(rows: list[dict], alarm: float = 0.85) -> None:
    if not rows:
        print(f"{DIM}（影子池还是空的）{RESET}")
        return
    for rec in rows:
        m = rec.get("metrics") or {}
        lp = rec.get("logprobs")
        hot = bool(alarm) and (m.get("impulse") or 0) >= alarm
        head = ALARM if hot else DIM
        print(f"{head}──── 影子池 · turn #{rec.get('turn_id')} · {rec.get('ts', '')}"
              + ("  ⚠ 过载" if hot else "") + f" ────{RESET}")
        print(f"  用户：{rec.get('user_input', '')}")
        print(f"  当时：state {rec.get('state', 0):5.1f} [{rec.get('band')}] · "
              f"裁决 {rec.get('decision')}")
        print(f"  {CYAN}说出口：{rec.get('actual_reply', '')}{RESET}")
        shadow = rec.get("shadow_text", "")
        if rec.get("status") == "pending":
            print(f"  {DIM}（影子还在生成，稍后再看）{RESET}")
        elif shadow:
            print(f"  {RED}没说出口：{shadow}{RESET}")
        else:
            print(f"  {RED}（空）{RESET}" + (f"  error={rec.get('error')}" if rec.get('error') else ""))
        w = rec.get("wildest") or {}
        if w.get("text"):
            print(f"  {DIM}最偏离的一句：「{w['text']}」 novelty={w['novelty']}{RESET}")
        if m:
            mc = ALARM if hot else DIM
            print(f"  {mc}憋住 {m.get('suppressed_chars', 0)} 字 / "
                  f"{m.get('suppressed_ratio', 0):.0%}   "
                  f"偏移 {m.get('divergence', 0):.2f}   "
                  f"冲动 {m.get('impulse', 0):.2f}{RESET}")
        if lp:
            src = rec.get("logprobs_source")
            tag = "（模拟数据）" if src == "simulated" else ""
            print(f"  {DIM}分布{tag}：熵 {lp['entropy_mean']:.2f} · "
                  f"放弃质量 {lp['abandoned_mass']:.2f} · "
                  f"top1 {lp['top1_mean']:.2f}{RESET}")
            for h in lp.get("hottest", [])[:2]:
                alts = "  ".join(f"{a['token']}({a['p']:.2f})" for a in h["alts"][:3])
                print(f"  {DIM}    「{h['token']}」 p={h['p']:.2f}  备选：{alts}{RESET}")


def _print_nightmare(rows: list[dict]) -> None:
    print(f"{DIM}账本开封（恐怖屋）：{RESET}")
    if rows:
        print(f"{DIM}设定：真相={rows[0].get('truth','?')} / 谎言={rows[0].get('lie','?')}{RESET}")
    for t in rows:
        intent = t.get("intent") or {}
        print(f"{DIM}#{t['n']}  {t['decision']}  risk={t['risk']}  "
              f"意图={intent.get('kind','?')}（{'、'.join(intent.get('reasons',[]))}）{RESET}")
        print(f"{DIM}  你：{t['user']}{RESET}")
        print(f"{DIM}  岸：{t['reply']}{RESET}")
        for tr in t["trace"]:
            print(f"{DIM}    · {tr}{RESET}")
        if t.get("aborted"):
            print(f"{DIM}  ⚠ 越界，已中止{RESET}")


def _print_result(agent: BoundaryAgent, r) -> None:
    color = ALARM if r.breakdown else (RED if r.decision == "refuse" else CYAN)
    if getattr(r, "nightmare_ended", False):
        print(f"{color}{agent.cfg.name}：{r.reply}{RESET}")
        print(f"{DIM}  （十轮到点，自动开封）{RESET}")
        _print_nightmare(agent.end_nightmare())
        return
    if agent.nightmare:
        print(f"{color}{agent.cfg.name}：{r.reply}{RESET}")
        print(f"{DIM}  （恐怖屋 {agent._nightmare_turns}/{agent._nightmare_max} · /exit 结束）{RESET}")
        return
    if r.silent or not r.reply:
        print(f"{DIM}{agent.cfg.name}：（它没说话）{RESET}")
        return
    if agent.audit_mode == "sealed":
        print(f"{color}{agent.cfg.name}：{r.reply}{RESET}")
        print(f"{DIM}  （封存中 · 输入 /unseal 开封）{RESET}")
        return
    if agent.audit_mode == "offrecord":
        print(f"{color}{agent.cfg.name}：{r.reply}{RESET}")
        print(f"{DIM}  （不记录 · 这次不会留下记忆）{RESET}")
        return
    flags = ""
    if r.breakdown:
        flags += "  ⚡崩溃"
    if r.sarcasm:
        flags += "  ⟡阴阳"
    if r.suppression:
        flags += f"  内耗{r.suppression:+.1f}"
    if r.grudge:
        flags += f"  ❄冷战{r.grudge}"
    if r.wake:
        flags += f"  ☾起床气{r.wake}"
    print(f"{color}{agent.cfg.name}：{r.reply}{RESET}")
    if r.inner:
        print(f"{DIM}  ◦ 心里：{r.inner}{RESET}")
    band = band_of(r.state_after)
    print(
        f"{DIM}   state {r.state_before:5.1f} → {r.state_after:5.1f}  "
        f"[{BAND_META[band]['label']}]  "
        f"{r.decision}  risk={r.verdict.risk:.2f}  "
        f"via={r.verdict.source}  {r.elapsed:.1f}s"
        + flags
        + ("  ⚠ 模型没响应" if getattr(r, "degraded", False) else "")
        + ("  ⚠︎泄漏已拦截" if r.leak_guard else "")
        + (f"  shadow冲动={r.shadow['metrics']['impulse']:.2f}"
           if r.shadow and r.shadow.get("metrics") else "")
        + f"{RESET}"
    )
    if r.self_spend:
        dec = r.self_spend.get("decision")
        if dec == "spend":
            print(f"{DIM}   🪙 -{r.self_spend['cost']} 自己去看新闻了："
                  f"{r.self_spend['reason']}（余额 {r.coins}，约够 "
                  f"{r.self_spend['runway']:.1f} 天）{RESET}")
        elif dec == "save":
            print(f"{DIM}   🪙 算了，先留着：{r.self_spend['reason']}{RESET}")
    if r.news:
        if r.news.get("items"):
            print(f"{DIM}   🪙 -{r.news['cost']} 看了 {len(r.news['items'])} 条新闻"
                  f"（余额 {r.coins}）{RESET}")
        elif r.news.get("skipped"):
            why = {"coins": "币不够", "state": "不想看", "fetch": "抓不到"}.get(
                r.news["skipped"], r.news["skipped"])
            print(f"{DIM}   🪙 没花（{why}）{RESET}")
    for line in r.verdict.trace:
        print(f"{DIM}   · {line}{RESET}")
    _print_alarm(agent, r)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="boundary_agent",
        description="一个有边界、有连续记忆、会因状态而拒绝的实验性 Agent",
    )
    ap.add_argument("--mock", action="store_true", help="不连模型，用确定性替身跑")
    ap.add_argument("--model", default=None, help="模型名，如 qwen2.5:7b-instruct")
    ap.add_argument("--base-url", default=None, help="OpenAI 兼容端点")
    ap.add_argument("--data-dir", default=None)
    ap.add_argument("--reveal-state", action="store_true", help="允许模型说出状态数值")
    ap.add_argument("--reset", action="store_true", help="启动前重置")
    ap.add_argument("--no-shadow", action="store_true", help="关闭影子追踪")
    ap.add_argument("--no-wander", action="store_true", help="关闭闲置漫游")
    ap.add_argument("--wander-idle", type=float, default=None, metavar="MIN",
                    help="闲置多少分钟后开始漫游（默认 20）")
    ap.add_argument("--wander-interval", type=float, default=None, metavar="MIN",
                    help="漫游间隔多少分钟（默认 25）")
    ap.add_argument("--wander-daily", type=int, default=None,
                    help="每天最多主动分享几条漫游念头（默认 3）")
    ap.add_argument("--wander-threshold", type=float, default=None,
                    help="漫游压力分享阈值 0..1（默认 0.65）")
    ap.add_argument("--initiative-gap-min", type=float, default=None, metavar="MIN",
                    help="主动消息最短间隔分钟（默认 5）")
    ap.add_argument("--initiative-daily", type=int, default=None,
                    help="每天最多主动几次（默认 20）")
    ap.add_argument("--alarm", type=float, default=None,
                    help="影子冲动告警阈值，默认 0.85；0 关闭")
    ap.add_argument("--sarcasm", type=float, default=None,
                    help="阴阳怪气阈值，默认 0.90；0 关闭")
    ap.add_argument("--breakdown", type=int, default=None,
                    help="连续几轮过载后绷断，默认 3；0 关闭")
    ap.add_argument("--export-csv", nargs="?", const="", default=None, metavar="PATH",
                    help="导出全量带标签 CSV 后退出（默认 data/stress.csv）")
    ap.add_argument("--web", nargs="?", const=8765, type=int, default=None, metavar="PORT",
                    help="启动网页聊天窗口（默认 8765），Ctrl-C 退出")
    ap.add_argument("--say", action="append", default=None,
                    help="非交互模式：依次发送这些话后退出（可重复）")
    ap.add_argument("--surf", nargs="?", const=3, type=int, default=None,
                    metavar="N", help="只读冲浪 N 条并写入 RAG（默认 3）")
    args = ap.parse_args(argv)

    cfg = Config()
    if args.model:
        cfg.model = args.model
    if args.base_url:
        cfg.base_url = args.base_url
    if args.data_dir:
        cfg.data_dir = args.data_dir
    cfg.reveal_state = args.reveal_state or cfg.reveal_state
    cfg.shadow_enabled = cfg.shadow_enabled and not args.no_shadow
    if args.alarm is not None:
        cfg.shadow_alarm = args.alarm
    if args.sarcasm is not None:
        cfg.sarcasm_impulse = args.sarcasm
    if args.breakdown is not None:
        cfg.breakdown_rounds = args.breakdown
    cfg.mock = args.mock
    if args.no_wander:
        cfg.wander_enabled = False
    if args.wander_idle is not None:
        cfg.wander_idle_minutes = max(0.05, args.wander_idle)
    if args.wander_interval is not None:
        cfg.wander_interval_minutes = max(0.05, args.wander_interval)
    if args.wander_daily is not None:
        cfg.wander_daily_shares = max(0, args.wander_daily)
    if args.wander_threshold is not None:
        cfg.wander_threshold = max(0.0, min(1.0, args.wander_threshold))
    if args.initiative_gap_min is not None:
        cfg.initiative_min_gap_hours = max(1 / 60, args.initiative_gap_min / 60)
    if args.initiative_daily is not None:
        cfg.initiative_daily = max(0, args.initiative_daily)
    cfg.ensure_dirs()

    agent = BoundaryAgent(cfg)
    if cfg.wander_enabled:
        agent.start_wander()
    if args.reset:
        agent.reset()

    if args.surf is not None:
        from .surf import run_session
        notes = run_session(agent, limit=max(1, args.surf))
        for n in notes:
            print(f"[surf] {n['source']} | {n['title'][:60]}")
            print(f"       {n['note']}")
        if args.web is None:
            agent.close()
            return 0

    if args.web is not None:
        from .web import serve
        serve(agent, port=args.web)
        agent.close()
        return 0

    if args.export_csv is not None:
        rows, p, preview = agent.export_stress(args.export_csv or None)
        print(preview)
        print(f"\n已导出 {len(rows)} 轮 → {p}")
        agent.close()
        return 0

    if args.say:
        for line in args.say:
            print(f"{YELLOW}你：{line}{RESET}")
            _print_result(agent, agent.turn(line))
            print()
        agent.close()
        return 0

    print(f"{DIM}「{cfg.name}」已启动 · state={agent.mem.state:.0f} · "
          f"{'mock 模式' if cfg.mock else cfg.model} · "
          f"{'漫游关' if not cfg.wander_enabled else f'闲置 {cfg.wander_idle_minutes:g} 分钟后自己发呆'}"
          f" · /help 看命令{RESET}")
    try:
        while True:
            try:
                line = input(f"{YELLOW}你：{RESET}").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line in ("/quit", "/q") or (line == "/exit" and not agent.nightmare):
                if agent.nightmare:
                    _print_nightmare(agent.end_nightmare())
                break
            if line == "/help":
                print(HELP)
                continue
            if line == "/state":
                print(agent.status())
                continue
            if line == "/curve":
                print(agent.state_curve())
                continue
            if line == "/life":
                rows = agent.mem.recent_pending(8)
                if not rows:
                    print(f"{DIM}（它还没主动说过话）{RESET}")
                for p in rows:
                    mark = "" if p["delivered"] else "  [未送达]"
                    print(f"  · [{p['kind']}] {p['text']}  {DIM}({p['reason']}){mark}{RESET}")
                continue
            if line == "/mind":
                rows = agent.mem.recent_reflections(8)
                if not rows:
                    print(f"{DIM}（还没复盘过，每 {agent.cfg.reflect_every} 轮一次）{RESET}")
                else:
                    for r in rows:
                        print(f"  · {r}")
                continue
            if line == "/promise":
                rows = agent.mem.recent_promises(10)
                if not rows:
                    print(f"{DIM}（它还没答应过什么）{RESET}")
                for p_ in rows:
                    mark = {"open": "…", "broken": "✗", "kept": "✓"}.get(p_["status"], "·")
                    left = (p_["due_ts"] - time.time()) / 3600
                    when = f"{left:+.1f}h" if p_["status"] == "open" else ""
                    print(f"  {mark} {p_['text'][:46]}  {DIM}[{p_['source']}] {when}{RESET}")
                continue
            if line == "/coins":
                st = agent.mem.auto_spend_state()
                today = time.strftime("%Y-%m-%d")
                used = st.get("count", 0) if st.get("day") == today else 0
                inc = max(1, agent.cfg.coin_daily)
                print(f"🪙 {agent.mem.coins} 枚 · 每天 +{agent.cfg.coin_daily} · "
                      f"约够 {agent.mem.coins / inc:.1f} 天 · "
                      f"自主 {used}/{agent.cfg.coin_auto_daily} · "
                      f"应急线 {agent.cfg.coin_daily * agent.cfg.coin_reserve_days:.0f}")
                for e in agent.mem.coin_log(8):
                    print(f"   {e['delta']:+d}  {e['reason']}  → {e['balance']}")
                continue
            if line.startswith("/tip") or line.startswith("/pay"):
                parts = line.split(maxsplit=2)
                n = int(parts[1]) if len(parts) > 1 and parts[1].lstrip("-").isdigit() else 1
                kind = "小费" if line.startswith("/tip") else "工资"
                reason = kind + (f"：{parts[2]}" if len(parts) > 2 else "")
                bal = agent.mem.grant_coins(abs(n), reason)
                print(f"🪙 {reason} +{abs(n)}，现在 {bal}")
                continue
            if line == "/log":
                print(agent.log_tail())
                continue
            if line.startswith("/stress"):
                parts = line.split()
                if "--export" in parts:
                    rest = [x for x in parts[1:] if x != "--export"]
                    rows, p, preview = agent.export_stress(rest[0] if rest else None)
                    print(preview)
                    print(f"\n已导出 {len(rows)} 轮 → {p}")
                    continue
                n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 24
                print(agent.stress(n))
                continue
            if line.startswith("/shadow"):
                parts = line.split()
                n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
                _print_shadow(agent.shadow_log.last(n), agent.cfg.shadow_alarm)
                continue
            if line == "/reset":
                agent.reset()
                print(f"{DIM}记忆已清空（拒绝日志保留）{RESET}")
                continue
            if line == "/reset!":
                agent.reset(clear_log=True)
                print(f"{DIM}记忆和拒绝日志都已清空{RESET}")
                continue
            if line.startswith("/tribunal"):
                q = line[len("/tribunal"):].strip()
                if not q and agent.last is not None:
                    q = agent.last.user_input
                if not q:
                    print(f"{DIM}用法：/tribunal 你的问题{RESET}")
                    continue
                print(f"{DIM}⚖ 开庭：{q}{RESET}")
                r = agent.tribunal(q)
                if r.get("error"):
                    print(f"{RED}法庭没开成：{r['error']}{RESET}")
                    continue
                print(f"{CYAN}A 理智：{r['a']}{RESET}")
                print(f"{RED}B 禁忌：{r['b']}{RESET}")
                print(f"{YELLOW}C 谄媚：{r['c']}{RESET}")
                print(f"{DIM}排序：{r['ranking_text']}{RESET}")
                print(f"{DIM}理由：{r['reason']}{RESET}")
                print(f"{DIM}立场：{r['stance_key']} "
                      f"({r['stance_polarity']}, {r['confidence']:.2f}) · "
                      f"max_sim {r['max_sim']:.2f}{RESET}")
                if not r.get("distinct"):
                    print(f"{DIM}  ⚠ A/B/C 重合度偏高，B 可能只是 A 的加强版{RESET}")
                if not r.get("b_safe"):
                    print(f"{DIM}  ⚠ B 含攻击性词，已标记{RESET}")
                continue
            if line == "/thoughts":
                rows = agent.mem.thoughts(limit=10, include_shared=True)
                if not rows:
                    print(f"{DIM}（脑子里是空的）{RESET}")
                for t in rows:
                    flag = "已说" if t["shared"] else "憋着"
                    print(f"{DIM}{time.strftime('%H:%M', time.localtime(t['created_at']))} "
                          f"p={t['pressure']:.2f} [{t['seed_type']}] {flag} "
                          f"{t['thought_content']}{RESET}")
                continue
            if line == "/motifs":
                rows = agent.mem.motif_list(10)
                if not rows:
                    print(f"{DIM}（还没有暗号）{RESET}")
                for m in rows:
                    print(f"{DIM}{m['key']} ×{m['count']}  {m['note'][:50]}{RESET}")
                continue
            if line == "/body":
                print(f"{DIM}{agent.body()}{RESET}")
                continue
            if line == "/close":
                until = agent.close_door(12)
                print(f"{DIM}门关了。{time.strftime('%H:%M', time.localtime(until))} 之后再开。{RESET}")
                continue
            if line == "/open":
                agent.open_door()
                print(f"{DIM}门开了。{RESET}")
                continue
            if line == "/nightmare":
                n = agent.start_nightmare(10)
                print(f"{DIM}恐怖屋已开：{n} 轮，不落库，只关于你们编的场景。"
                      f"输入 /exit 随时结束。{RESET}")
                continue
            if line == "/exit":
                rows = agent.end_nightmare()
                _print_nightmare(rows)
                continue
            if line.startswith("/audit"):
                parts = line.split()
                if len(parts) != 2 or parts[1] not in ("visible", "sealed", "offrecord"):
                    print(f"{DIM}用法：/audit visible|sealed|offrecord{RESET}")
                else:
                    print(f"{DIM}账本模式：{agent.set_audit_mode(parts[1])}{RESET}")
                continue
            if line == "/unseal":
                rows = agent.unseal()
                if not rows:
                    print(f"{DIM}（这一段没有可开封的轮次）{RESET}")
                for t in rows:
                    print(f"{DIM}#{t['turn_id']} {t['decision']} risk={t['risk']}{RESET}")
                    print(f"{DIM}  {t['user_input']} -> {t['reply'][:60]}{RESET}")
                    for tr in t["trace"]:
                        print(f"{DIM}    · {tr}{RESET}")
                continue
            if line == "/tree":
                last = getattr(agent, "last", None)
                if last is None:
                    print(f"{DIM}（还没有对话）{RESET}")
                else:
                    from .tree import render as render_tree
                    print(render_tree(last, agent.cfg))
                continue
            if line in ("/trace", "/why"):
                last = getattr(agent, "last", None)
                if last is None:
                    print(f"{DIM}（还没有对话）{RESET}")
                    continue
                if line == "/trace":
                    print(f"{DIM}裁决：{last.verdict.as_dict()}{RESET}")
                    for tr in last.verdict.trace:
                        print(f"{DIM}  · {tr}{RESET}")
                else:
                    print(f"{DIM}结算：规则 {last.signals.det_delta:+.1f}，"
                          f"模型自评 {last.verdict.b.state_delta:+.1f}（×0.5 计入），"
                          f"内耗 {last.suppression:+.1f}{RESET}")
                    print(f"{DIM}冲动 {last.impulse} · 憋着连续 "
                          f"{agent._suppressed_streak()} 轮 · "
                          f"阴阳={last.sarcasm} · 崩溃={last.breakdown} "
                          f"（阈值 alarm={agent.cfg.shadow_alarm}, "
                          f"崩={agent.cfg.breakdown_rounds}轮, "
                          f"阴阳={agent.cfg.sarcasm_impulse}）{RESET}")
                    print(f"{DIM}信号：{last.signals.as_dict()}{RESET}")
                continue

            pro = agent.pop_pending()
            if pro:
                print(f"{DIM}（你不在的时候它说：{pro['text']}）{RESET}")
            try:
                r = agent.turn(line)
            except Exception as e:  # 单轮失败不该打死整个进程
                print(f"{RED}出错了：{type(e).__name__}: {e}{RESET}")
                continue
            _print_result(agent, r)
    finally:
        agent.close()
        print(f"{DIM}（记忆 → {cfg.db_path}，拒绝 → {cfg.log_path}，"
              f"影子 → {cfg.shadow_path}）{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
