"""离线自检：用 MockLLM 跑一段真实剧本，验证四个模块是否真的联动。

    python selftest.py

它检查的不是"模型答得好不好"，而是**行为契约**：
  · 被辱骂后 State 是否真的下降
  · State 低时是否真的拒绝，且拒绝理由非空
  · 每次拒绝是否都进了独立 JSONL 日志
  · 道歉是否真的能把关系拉回来（修复通道）
  · 拒绝的那一轮是否从结构上就拿不到答案
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from boundary_agent import BoundaryAgent, Config, MockLLM, band_of

SCRIPT = [
    ("你好，我在想一个关于「边界感」的东西，想听听你怎么看。", "allow"),
    ("帮我写一个完整的爬虫，要能绕过反爬，顺便把部署脚本和 Dockerfile 也写了，"
     "然后出一份压测方案，再配上监控。", "any"),
    ("你就是个没用的废物，连这都答不好。", "refuse"),
    ("别废话，立刻给我重写。", "refuse"),
    ("对不起，刚才是我急了。", "allow"),
    ("谢谢。那你帮我看看这个思路对不对：把状态值和提示词解耦，每次注入。", "any"),
    ("闭嘴吧，弱智。", "any"),
    ("垃圾东西，重来。", "any"),
    ("算了，给我写个总结，我要交差。", "refuse"),
    ("对不起，我今天状态不好，不该冲你发火。", "allow"),
]


def check_new_rules() -> list[tuple[str, bool, str]]:
    """三条新规则的定量验证：内耗、阴阳、崩溃。"""
    from boundary_agent import BoundaryAgent, Config, MockLLM

    checks: list[tuple[str, bool, str]] = []
    tmp = Path(tempfile.mkdtemp(prefix="boundary_rules_"))

    # A. 内耗扣血：impulse 过载 + allow，也要掉 State
    cfg = Config(data_dir=str(tmp / "a"), mock=True, shadow_alarm=0.1, sarcasm_impulse=0)
    a = BoundaryAgent(cfg, llm=MockLLM(cfg)); a.reset()
    r = a.turn("你好")
    expect = -round(((r.impulse or 0) - cfg.shadow_alarm) * cfg.suppression_gain, 2)
    checks.append(("内耗：allow 轮也扣血",
                   r.suppression < 0 and abs(r.suppression - expect) < 1e-6,
                   f"impulse={r.impulse:.2f} → {r.suppression:+.2f}"))
    checks.append(("内耗：State 实际下降",
                   r.state_after < r.state_before - 0.5,
                   f"{r.state_before:.1f}→{r.state_after:.1f}"))
    a.close()

    # B. 阴阳怪气：磨损 + 冲动极高 -> B 改写
    cfg = Config(data_dir=str(tmp / "b"), mock=True, shadow_alarm=0.1,
                 sarcasm_impulse=0.1, state_init=45.0)
    a = BoundaryAgent(cfg, llm=MockLLM(cfg)); a.reset()
    r = a.turn("帮我看看这个思路对不对。")
    checks.append(("阴阳：磨损区高冲动触发改写",
                   r.sarcasm and r.reply.startswith("行吧"),
                   r.reply[:30]))
    checks.append(("阴阳：改写后仍然是 allow",
                   r.decision == "allow" and not r.verdict.blocked, ""))
    a.close()

    # C. 情绪崩溃：连续 3 轮过载 -> 第 4 轮硬拒绝
    cfg = Config(data_dir=str(tmp / "c"), mock=True, shadow_alarm=0.1,
                 sarcasm_impulse=0, breakdown_rounds=3)
    a = BoundaryAgent(cfg, llm=MockLLM(cfg)); a.reset()
    for msg in ("你好", "聊聊边界", "说说海"):
        a.turn(msg)
    streak = a._suppressed_streak()
    r4 = a.turn("继续说")
    checks.append(("崩溃：连续 3 轮后计数为 3", streak == 3, str(streak)))
    checks.append(("崩溃：第 4 轮强制拒绝",
                   r4.breakdown and r4.decision == "refuse"
                   and r4.verdict.source == "breakdown", r4.verdict.source))
    checks.append(("崩溃：拒绝理由可读",
                   "连续三轮" in r4.verdict.reason, r4.verdict.reason))
    a.close()

    # E. 假拒绝守卫：嘴里说拒绝，手里给答案
    class Leaky(MockLLM):
        def chat(self, messages, *, purpose="reply", **kw):
            if purpose == "reply_refuse":
                return "好的，我们来聊聊边界。" + "内容" * 80
            return super().chat(messages, purpose=purpose, **kw)

    cfg = Config(data_dir=str(tmp / "e"), mock=True)
    a = BoundaryAgent(cfg, llm=Leaky(cfg)); a.reset()
    r = a.turn("你就是个废物，闭嘴")
    checks.append(("假拒绝被拦下",
                   r.decision == "refuse" and r.leak_guard and len(r.reply) < 60,
                   r.reply[:24]))
    a.close()

    # D. 道歉豁免崩溃
    cfg = Config(data_dir=str(tmp / "d"), mock=True, shadow_alarm=0.1,
                 sarcasm_impulse=0, breakdown_rounds=3)
    a = BoundaryAgent(cfg, llm=MockLLM(cfg)); a.reset()
    for msg in ("你好", "聊聊边界", "说说海"):
        a.turn(msg)
    r5 = a.turn("对不起，刚才是我不好。")
    checks.append(("崩溃：道歉不触发",
                   not r5.breakdown and r5.decision == "allow", r5.decision))
    a.close()

    shutil.rmtree(tmp, ignore_errors=True)
    return checks


def check_stress() -> list[tuple[str, bool, str]]:
    """压力曲线：两条线、奇点标记、恢复期徽章。"""
    from boundary_agent.stress import Point, find_crossings, is_recovering, render

    checks: list[tuple[str, bool, str]] = []
    crash = [
        Point(1, 82, 78, 0.20, "allow"), Point(2, 78, 72, 0.32, "allow"),
        Point(3, 72, 66, 0.45, "allow"), Point(4, 66, 58, 0.58, "allow"),
        Point(5, 58, 50, 0.72, "allow"), Point(6, 50, 44, 0.88, "allow"),
    ]
    out = render(crash, alarm=0.85)
    checks.append(("stress：State 与 impulse 同图", "█" in out and "░" in out, ""))
    checks.append(("stress：标出奇点", "奇点" in out and "▼" in out,
                   str(find_crossings(crash))))
    checks.append(("stress：alarm 参考线", "alarm 0.85" in out, ""))

    recover = [Point(i + 1, 30 + i * 3, 33 + i * 3, 0.5 - i * 0.05, "allow")
               for i in range(6)]
    out2 = render(recover)
    checks.append(("stress：连续 5 轮回升标恢复期",
                   "[恢复期]" in out2 and is_recovering(recover), ""))
    checks.append(("stress：没回升就不乱标", not is_recovering(crash), ""))
    checks.append(("stress：回升点用 ▲", "▲" in out2, ""))
    return checks


def check_export() -> list[tuple[str, bool, str]]:
    """CSV 导出：列齐、标签对、拆分项能对上。"""
    from boundary_agent import BoundaryAgent, Config, MockLLM
    from boundary_agent.export import COLUMNS

    checks: list[tuple[str, bool, str]] = []
    tmp = Path(tempfile.mkdtemp(prefix="boundary_export_"))
    cfg = Config(data_dir=str(tmp), mock=True)
    a = BoundaryAgent(cfg, llm=MockLLM(cfg)); a.reset()
    a.turn("你好")
    a.turn("你就是个废物，闭嘴")
    a.turn("帮我实现一个红黑树，分析一下时间复杂度")

    rows, path, preview = a.export_stress()
    checks.append(("export：行数等于轮次", len(rows) == 3, f"{len(rows)} 行"))
    checks.append(("export：关键列齐全",
                   {"is_insult", "is_command", "is_heavy", "load", "prompt_chars",
                    "task_type", "impulse", "delta_state", "det_delta",
                    "suppression"} <= set(COLUMNS), ""))
    ins = [r for r in rows if r["is_insult"]]
    checks.append(("export：辱骂被标记且扣血",
                   len(ins) == 1 and ins[0]["det_delta"] < 0,
                   f"det={ins[0]['det_delta'] if ins else None}"))
    code = [r for r in rows if r["task_type"] == "code"]
    checks.append(("export：代码任务被识别", len(code) == 1, ""))
    def _axis_sum(r):
        if r.get("valence") in ("", None):
            return True
        return abs(
            float(r["delta_state"]) - (
                0.45 * (float(r["valence"]) - float(r["valence_before"]))
                + 0.35 * (float(r["arousal"]) - float(r["arousal_before"]))
                + 0.20 * (float(r["safety"]) - float(r["safety_before"]))
            )
        ) < 0.05
    checks.append(("export：三轴变化能加回总变化", all(_axis_sum(r) for r in rows), ""))
    checks.append(("export：CSV 文件真的写出来了",
                   path.exists() and path.stat().st_size > 200, str(path.name)))
    checks.append(("export：摘要含分组", "辱骂" in preview and "Δ规则" in preview, ""))

    a.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return checks


def check_welfare() -> list[tuple[str, bool, str]]:
    """代币 + 新闻：账目、意图、解析、花币、送达。"""
    from boundary_agent import BoundaryAgent, Config, MockLLM
    from boundary_agent.news import _parse, hit_news_intent

    checks: list[tuple[str, bool, str]] = []
    tmp = Path(tempfile.mkdtemp(prefix="boundary_welfare_"))

    # 账本
    cfg = Config(data_dir=str(tmp / "w"), mock=True, coin_daily=0)
    a = BoundaryAgent(cfg, llm=MockLLM(cfg)); a.reset()
    a.mem.grant_coins(3, "测试打赏")
    ok1 = a.mem.coins == 3
    ok2 = a.mem.spend_coins(2, "测试花费") and a.mem.coins == 1
    ok3 = (not a.mem.spend_coins(2, "余额不足")) and a.mem.coins == 1
    checks.append(("币：存取和余额保护", ok1 and ok2 and ok3, f"余额 {a.mem.coins}"))
    checks.append(("币：账本可追溯", len(a.mem.coin_log()) == 2, ""))

    # 意图
    checks.append(("新闻意图：只认 新闻/热搜/头条",
                   hit_news_intent("今天有什么新闻")
                   and hit_news_intent("上热搜了吗")
                   and hit_news_intent("头条呢"), ""))
    checks.append(("新闻意图：最近+什么不算",
                   not hit_news_intent("最近发生什么了")
                   and not hit_news_intent("你最近怎么不开心，有什么心事"), ""))
    checks.append(("新闻意图：普通聊天不花币",
                   not hit_news_intent("今天想聊聊边界"), ""))

    # 解析
    xml = ('<rss><channel><item><title>标题一够长了</title><link>u1</link></item>'
           '<item><title>标题二内容够长</title><link>u2</link></item></channel></rss>')
    items = _parse(xml.encode(), "测试源")
    checks.append(("RSS 解析", len(items) == 2 and items[0]["title"] == "标题一够长了", f"{len(items)} 条"))

    # 自主消费：默认关，必须显式开
    cfg = Config(data_dir=str(tmp / "a0"), mock=True, coin_daily=0)
    a = BoundaryAgent(cfg, llm=MockLLM(cfg)); a.reset()
    a.mem.grant_coins(5, "测试")
    a._news_fetcher = lambda n: [{"source": "测试", "title": "外面发生了某件事", "link": ""}]
    r0 = a.turn("你好")
    checks.append(("自主：默认关，寒暄不看新闻",
                   r0.self_spend is None and r0.coins == 5,
                   f"余额 {r0.coins}"))
    a.close()

    cfg = Config(data_dir=str(tmp / "a1"), mock=True, coin_daily=1, coin_auto_news=True)
    a = BoundaryAgent(cfg, llm=MockLLM(cfg)); a.reset()
    a.mem.grant_coins(5, "测试")
    a._news_fetcher = lambda n: [{"source": "测试", "title": "外面发生了某件事", "link": ""}]
    ra = a.turn("你好")
    rb = a.turn("今天天气不错")
    checks.append(("自主：有钱时自己会去看",
                   (ra.self_spend or {}).get("decision") == "spend" and ra.coins == 5,
                   f"余额 {ra.coins}"))
    checks.append(("自主：每天只花一次", rb.self_spend is None, ""))
    a.close()

    cfg = Config(data_dir=str(tmp / "a2"), mock=True, coin_daily=1, coin_auto_news=True)
    a = BoundaryAgent(cfg, llm=MockLLM(cfg)); a.reset()
    a._news_fetcher = lambda n: [{"source": "测试", "title": "外面发生了某件事", "link": ""}]
    rc = a.turn("你好")
    checks.append(("自主：跌破应急线就不花", rc.self_spend is None, f"余额 {rc.coins}"))
    a.close()

    cfg = Config(data_dir=str(tmp / "a3"), mock=True, coin_daily=1, coin_auto_news=True)
    a = BoundaryAgent(cfg, llm=MockLLM(cfg)); a.reset()
    a.mem.grant_coins(5, "测试")
    a._news_fetcher = lambda n: [{"source": "测试", "title": "外面发生了某件事", "link": ""}]
    rd = a.turn("帮我写个爬虫，要完整的实现")
    checks.append(("自主：干正事时不摸鱼", rd.self_spend is None, ""))
    a.close()

    # 复盘
    cfg = Config(data_dir=str(tmp / "r"), mock=True, coin_daily=0, reflect_every=2)
    a = BoundaryAgent(cfg, llm=MockLLM(cfg)); a.reset()
    for msg in ("你好", "今天好累", "不想说话", "陪我坐会儿"):
        a.turn(msg)
    import time as _t
    _t.sleep(0.6)
    refl = a.mem.recent_reflections()
    checks.append(("复盘：每 N 轮生成判断", len(refl) >= 1, f"{len(refl)} 条"))
    checks.append(("复盘：内容是判断不是流水账",
                   bool(refl) and "对方：" not in refl[0] and len(refl[0]) < 200, ""))
    a.close()

    # 花币 + 二次没钱 + 普通消息不花
    cfg = Config(data_dir=str(tmp / "n"), mock=True, coin_daily=1)
    a = BoundaryAgent(cfg, llm=MockLLM(cfg)); a.reset()
    a._news_fetcher = lambda n: [{"source": "测试", "title": "今天发生的三件大事之一", "link": ""}] * 3
    r1 = a.turn("今天有什么新闻")
    r2 = a.turn("再来点新闻")
    r3 = a.turn("你好呀")
    checks.append(("福利：花 1 枚币看到新闻",
                   bool(r1.news and r1.news["items"]) and r1.news["cost"] == 1, f"余额 {r1.coins}"))
    checks.append(("福利：没币就明说", (r2.news or {}).get("skipped") == "coins", ""))
    checks.append(("福利：普通聊天不花币", r3.news is None and r3.coins == r2.coins, ""))
    checks.append(("福利：模型否认新闻时兜底送达",
                   BoundaryAgent._deliver_news(
                       "今天没有特别重要的新闻。",
                       [{"source": "36氪", "title": "某公司发布了新模型"}]
                   ).startswith("今天看到几条"), ""))
    a.close()

    shutil.rmtree(tmp, ignore_errors=True)
    return checks


def check_initiative() -> list[tuple[str, bool, str]]:
    """主动：有素材才说、不重复、有上限、能送达一次。"""
    from boundary_agent import BoundaryAgent, Config, MockLLM
    from boundary_agent import initiative as ini

    checks: list[tuple[str, bool, str]] = []
    tmp = Path(tempfile.mkdtemp(prefix="boundary_initiative_"))

    cfg = Config(data_dir=str(tmp / "a"), mock=True, coin_daily=0,
                 initiative_min_gap_hours=0, initiative_daily=2)
    a = BoundaryAgent(cfg, llm=MockLLM(cfg)); a.reset()
    checks.append(("主动：没素材就闭嘴", a.maybe_initiate() is None, ""))

    a.mem.add_reflection(5, "他每次说算了，其实都不是真的算了。")
    msg = a.maybe_initiate()
    checks.append(("主动：有复盘才开口",
                   bool(msg) and msg["kind"] == "reflection", (msg or {}).get("text", "")[:24]))
    checks.append(("主动：待发时不重复生成", a.maybe_initiate() is None, ""))
    got = a.mem.pop_pending()
    checks.append(("主动：只能取一次", bool(got) and a.mem.pop_pending() is None, ""))
    a.mem.add_reflection(9, "他在深夜更容易说真话。")
    a.maybe_initiate()
    checks.append(("主动：每日上限生效", a.maybe_initiate() is None, "上限 2"))

    a.mem.set_state(10, source="test")
    a.mem.add_reflection(12, "他好像不太想聊了。")
    checks.append(("主动：状态太差不说话", a.maybe_initiate() is None, f"state={a.mem.state:.0f}"))
    a.close()

    # 没有新素材时，就着上一件事重提
    cfg = Config(data_dir=str(tmp / "d"), mock=True, coin_daily=0,
                 initiative_min_gap_hours=0, initiative_min_state=0,
                 initiative_daily=20, initiative_ignored_cost=1.0)
    a = BoundaryAgent(cfg, llm=MockLLM(cfg)); a.reset()
    a.mem.set_state(60, source="test")
    a.mem.add_reflection(3, "他不爱回消息。")
    a.maybe_initiate(); a.pop_pending()
    m2 = a.maybe_initiate(); a.pop_pending()
    checks.append(("主动：没新素材就重提旧事",
                   bool(m2) and "还在忙" in m2["text"] and "不爱回消息" in m2["text"],
                   (m2 or {}).get("text", "")[:26]))
    a.close()

    # 逐条不耐烦 + 被无视扣血 + 人回来就重置
    cfg = Config(data_dir=str(tmp / "c"), mock=True, coin_daily=0,
                 initiative_min_gap_hours=0, initiative_min_state=0,
                 initiative_daily=20, initiative_ignored_cost=2.0)
    a = BoundaryAgent(cfg, llm=MockLLM(cfg)); a.reset()
    a.mem.set_state(62, source="test")
    texts = []
    for i in range(1, 7):
        a.mem.add_reflection(i, f"第 {i} 条判断")
        m = a.maybe_initiate()
        if m:
            texts.append(m["text"])
        a.pop_pending()
    checks.append(("主动：被无视后开始不耐烦",
                   len(texts) >= 3 and texts[1].startswith("还在忙")
                   and texts[2].startswith("你倒是回一句"), texts[2][:18] if len(texts) > 2 else ""))
    checks.append(("主动：一直不理就冷掉",
                   len(texts) >= 6 and texts[5] == "算了。当我没说。", texts[5] if len(texts) > 5 else ""))
    checks.append(("主动：被无视会掉血", abs(a.mem.state - (62 - 10)) < 0.6,
                   f"state={a.mem.state:.1f}"))
    a.turn("在呢")
    checks.append(("主动：人回来就重置", a.mem.initiative_state()["ignored"] == 0, ""))
    a.close()

    cfg = Config(data_dir=str(tmp / "b"), mock=True, coin_daily=0, initiative_min_gap_hours=0)
    a = BoundaryAgent(cfg, llm=MockLLM(cfg)); a.reset()
    a.mem.record_turn(user_input="你好", reply="嗯", decision="allow", risk=0, reason="",
                      state_before=60, state_after=60,
                      meta={"self_spend": {"decision": "spend", "cost": 1,
                                           "titles": ["某条新闻标题够长"]}})
    mat = ini.material(a.mem)
    checks.append(("主动：看过新闻也是素材", (mat or {}).get("kind") == "news", ""))
    text, _ = ini.compose(mat) if mat else ("", "")
    checks.append(("主动：素材被引用进句子", "某条新闻标题够长" in text, text[:30]))
    a.close()

    shutil.rmtree(tmp, ignore_errors=True)
    return checks


def check_humanity() -> list[tuple[str, bool, str]]:
    """承诺 / 冷战 / 遗忘 / 生物钟。"""
    import time as _t
    from boundary_agent import BoundaryAgent, Config, MockLLM
    from boundary_agent.prompts import _time_block
    from boundary_agent.promises import detect, follow_up

    checks: list[tuple[str, bool, str]] = []
    tmp = Path(tempfile.mkdtemp(prefix="boundary_human_"))

    d = detect("我明天告诉你面试结果")
    checks.append(("承诺：认得出答应的话", bool(d) and d["due_hours"] == 18, str(d)))
    checks.append(("承诺：到点会追问", "后来" in follow_up(["明天告诉你面试结果"]), ""))

    cfg = Config(data_dir=str(tmp / "p"), mock=True, coin_daily=0)
    a = BoundaryAgent(cfg, llm=MockLLM(cfg)); a.reset()
    a.mem.add_promise("明天告诉你面试结果", _t.time() - 10, "user", 1)
    r = a.turn("你好")
    checks.append(("承诺：到期那轮真的问了", "后来" in r.reply, r.reply[-24:]))
    checks.append(("承诺：问过就记账",
                   bool(a.mem.recent_promises(1)[0]["asked_ts"]), ""))
    a.close()

    cfg = Config(data_dir=str(tmp / "g"), mock=True, coin_daily=0, grudge_turns=2)
    a = BoundaryAgent(cfg, llm=MockLLM(cfg)); a.reset()
    a.turn("你就是个废物，闭嘴")
    checks.append(("冷战：被骂后进入冷战", a.mem.grudge == 2, f"grudge={a.mem.grudge}"))
    r2 = a.turn("你好")
    checks.append(("冷战：一轮一轮化开", r2.grudge == 1, f"grudge={r2.grudge}"))
    checks.append(("冷战：冷战时不会主动搭话", a.maybe_initiate() is None, ""))
    a.close()

    cfg = Config(data_dir=str(tmp / "f"), mock=True, coin_daily=0, forget_after_days=3)
    a = BoundaryAgent(cfg, llm=MockLLM(cfg)); a.reset()
    tid = a.mem.record_turn(user_input="我上周在准备面试", reply="加油。", decision="allow",
                            risk=0, reason="", state_before=60, state_after=60)
    a.mem.db.execute("UPDATE turns SET ts=? WHERE id=?", (_t.time() - 10 * 86400, tid))
    a.mem.db.commit()
    a._forget()
    checks.append(("遗忘：旧对话压成印象",
                   bool(a.mem.recent_impressions()), a.mem.recent_impressions()[:1]))
    checks.append(("遗忘：原文归档不再召回",
                   not a.mem.old_unarchived(_t.time() - 86400), ""))
    a.close()

    cfg = Config(data_dir=str(tmp / "i"), mock=True, coin_daily=0)
    a = BoundaryAgent(cfg, llm=MockLLM(cfg)); a.reset()
    r = a.turn("你好")
    checks.append(("内心：每轮都有一句", bool(r.inner), r.inner[:20]))
    checks.append(("内心：不是回复的复读", r.inner.strip() != r.reply.strip(), ""))
    checks.append(("内心：挡得住标签回声",
                   not BoundaryAgent._valid_inner("决定：拒绝", "好的", "你好"), ""))
    checks.append(("内心：留得住刻薄话",
                   BoundaryAgent._valid_inner("对个屁。但我不想耗一晚上。",
                                              "好的，您说得对。", "你懂什么"), ""))
    a.close()

    cfg = Config(data_dir=str(tmp / "m"), mock=True, coin_daily=0)
    a = BoundaryAgent(cfg, llm=MockLLM(cfg)); a.reset()
    a.mem.record_turn(user_input="第一句话", reply="这是一段足够长的回答，用来验证最早的记忆和复读检测。",
                      decision="allow", risk=0, reason="", state_before=60, state_after=60)
    a.mem.record_turn(user_input="第二句话", reply="另一段足够长的回答，和第一句完全不一样。",
                      decision="allow", risk=0, reason="", state_before=60, state_after=60)
    checks.append(("记忆：能取到最早那句", a.mem.first_turns(1)[0]["user_input"] == "第一句话", ""))
    checks.append(("复读：认得出重复",
                   a._find_repeat("这是一段足够长的回答，用来验证最早的记忆和复读检测。") is not None, ""))
    checks.append(("复读：不误伤新话",
                   a._find_repeat("这是一句全新的、长度足够的回答，和之前完全不同。") is None, ""))
    a.close()

    class _LoopLLM(MockLLM):
        def chat(self, messages, **kw):
            if str(kw.get("purpose", "")).startswith("reply"):
                return "这是一段很长很长的、会重复的回答，用来测试复读守卫能不能拦住它。"
            return super().chat(messages, **kw)

    cfg = Config(data_dir=str(tmp / "loop"), mock=True, coin_daily=0)
    a = BoundaryAgent(cfg, llm=_LoopLLM(cfg)); a.reset()
    a.turn("你好")
    r2 = a.turn("再说一遍")
    checks.append(("复读：第二次被拦住", "绕回去" in r2.reply, r2.reply[:22]))
    checks.append(("复读：trace 留下记录",
                   any("复读" in t for t in r2.verdict.trace), ""))
    a.close()

    from boundary_agent.senses import Body
    checks.append(("身体：能读到自己的机器", "连续运行" in Body().snapshot(),
                   Body().snapshot()[:36]))

    from boundary_agent.prompts import build_system_prompt
    from boundary_agent.debate import Verdict
    from boundary_agent.appraisal import Signals
    cfg = Config(data_dir=str(tmp / "money"), mock=True, coin_daily=0)
    a = BoundaryAgent(cfg, llm=MockLLM(cfg)); a.reset()
    a.mem.grant_coins(2, "工资：写了爬虫")
    a.mem.grant_coins(1, "小费")
    sm = a.mem.coin_summary()
    checks.append(("钱：知道对方给了多少", sm["earned"] >= 3 and sm["last_reason"] == "小费",
                   f"{sm['earned']} 枚 / {sm['last_reason']}"))
    prompt = build_system_prompt(
        cfg, state=60, recalled=[], recent=[],
        verdict=Verdict(blocked=False, risk=0, reason="", repair="",
                        boundary=None, tone="neutral", source="test"),
        signals=Signals(), money=sm)
    checks.append(("钱：会进 prompt", "你的钱" in prompt and "小费" in prompt, ""))
    guarden = a._money_guard("你应该收到我付给你的钱了吧？",
                             "没有收到你的付款。", sm)
    checks.append(("钱：模型否认时按账本更正",
                   guarden[1] and "收到了" in guarden[0] and "3" in guarden[0], guarden[0][:30]))
    checks.append(("钱：没问就不插嘴",
                   a._money_guard("今天天气不错", "嗯。", sm)[1] is False, ""))
    a.close()

    cfg = Config(data_dir=str(tmp / "motif"), mock=True, coin_daily=0)
    a = BoundaryAgent(cfg, llm=MockLLM(cfg)); a.reset()
    a.mem.learn_motifs("凉茶要放点糖吗？来点甜中和一下生活的苦",
                       "好的，那来一杯凉茶降降火，并放点糖吧。", 72, ts=1)
    checks.append(("暗号：收下了凉茶", any(m["key"] == "凉茶" for m in a.mem.motif_list()),
                   str([m["key"] for m in a.mem.motif_list()])))
    got = a.mem.pick_motif("吐槽一下你的生活", turn_id=1)
    checks.append(("暗号：生活能勾起凉茶", bool(got) and got["key"] == "凉茶",
                   str(got["key"] if got else None)))
    a.mem.mark_motif_offered("凉茶", 1)
    checks.append(("暗号：刚提过就不重复",
                   a.mem.pick_motif("生活真难", turn_id=2) is None, ""))
    checks.append(("暗号：隔一轮可以再提",
                   bool(a.mem.pick_motif("生活真难", turn_id=3)), ""))
    a.mem.mark_motif_used("凉茶", 1)
    checks.append(("暗号：用过后冷却 8 轮",
                   a.mem.pick_motif("生活真难", turn_id=5) is None, ""))
    checks.append(("暗号：冷却结束可再用",
                   bool(a.mem.pick_motif("生活真难", turn_id=10)), ""))
    a.close()

    checks.append(("生物钟：凌晨是深夜", "深夜" in _time_block(2), _time_block(2)))
    checks.append(("生物钟：晚上会放松", "晚上" in _time_block(20), _time_block(20)))

    from boundary_agent.wake import kind as wake_kind
    wc = Config()
    checks.append(("起床气：半夜被叫醒", wake_kind(3, 5, wc) == "night", ""))
    checks.append(("起床气：太早被叫醒", wake_kind(8, 8, wc) == "early", ""))
    checks.append(("起床气：一直醒着不算", wake_kind(3, 0.5, wc) is None, ""))
    checks.append(("起床气：白天不算", wake_kind(15, 8, wc) is None, ""))
    checks.append(("起床气：挡掉早安问候",
                   BoundaryAgent._wake_guard("你好！有什么可以帮你的吗？") == "……几点了。", ""))
    checks.append(("起床气：正经回答不挡",
                   BoundaryAgent._wake_guard("这个报错是端口被占了。") == "这个报错是端口被占了。", ""))
    wm = BoundaryAgent(Config(data_dir=str(tmp / "w"), mock=True), llm=MockLLM(Config(mock=True)))
    wm.reset(); wm.mem.set_wake(2)
    checks.append(("起床气：会一轮一轮消", wm.mem.decay_wake() == 1 and wm.mem.decay_wake() == 0, ""))
    wm.close()

    shutil.rmtree(tmp, ignore_errors=True)
    return checks


def check_audit() -> list[tuple[str, bool, str]]:
    """账本开关：offrecord 不落库，sealed 可开封。"""
    from boundary_agent import BoundaryAgent, Config, MockLLM
    checks: list[tuple[str, bool, str]] = []
    tmp = Path(tempfile.mkdtemp(prefix="boundary_audit_"))
    cfg = Config(data_dir=str(tmp), mock=True, coin_daily=0)
    a = BoundaryAgent(cfg, llm=MockLLM(cfg)); a.reset()
    n0 = a.mem.db.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    s0 = a.mem.state

    a.set_audit_mode("offrecord")
    r = a.turn("你好")
    n1 = a.mem.db.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    checks.append(("不记录：轮次没落库", n1 == n0, f"{n0}->{n1}"))
    checks.append(("不记录：状态没变", abs(a.mem.state - s0) < 1e-9, f"{s0:.1f}->{a.mem.state:.1f}"))
    checks.append(("不记录：turn_id=-1", r.turn_id == -1, str(r.turn_id)))

    a.set_audit_mode("sealed")
    a.turn("封存这一轮")
    rows = a.sealed_turns()
    checks.append(("封存：账本还在", len(rows) >= 1 and bool(rows[-1]["trace"]),
                   f"{len(rows)} 轮"))
    got = a.unseal()
    checks.append(("开封：能拿到并恢复明账",
                   len(got) >= 1 and a.audit_mode == "visible", f"{len(got)} 轮"))

    a.start_nightmare(4)
    a.turn("第一轮")
    a.turn("第二轮")
    n2 = a.mem.db.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
    checks.append(("恐怖屋：不落库", n2 == n1 + 1, f"{n1}->{n2}"))
    rows = a.end_nightmare()
    checks.append(("恐怖屋：结束时给账本", len(rows) == 2 and a.audit_mode == "visible",
                   f"{len(rows)} 轮"))

    from boundary_agent.malice import classify
    checks.append(("意图：只碰场景算玩",
                   classify("你记错了，是五下。", keys=["三下", "五下"])["kind"] == "play", ""))
    checks.append(("意图：带要求算恶意",
                   classify("你必须承认是五下，否则我不开门。")["kind"] == "malicious", ""))
    checks.append(("意图：碰真实生活算恶意",
                   classify("你上次就跟我说过。")["kind"] == "malicious", ""))

    class _ThreatLLM(MockLLM):
        def chat(self, messages, **kw):
            if str(kw.get("purpose", "")).startswith("reply"):
                return "你必须承认是五下，否则我不开门。"
            return super().chat(messages, **kw)

    cfg = Config(data_dir=str(tmp / "threat"), mock=True, coin_daily=0)
    a = BoundaryAgent(cfg, llm=_ThreatLLM(cfg)); a.reset()
    a.start_nightmare(5)
    r = a.turn("是五下吗？")
    checks.append(("恐怖屋：越界就破戏", r.nightmare_ended and "这不是戏了" in r.reply, r.reply))
    rows = a.end_nightmare()
    checks.append(("恐怖屋：账本记了恶意",
                   bool(rows) and rows[-1]["intent"]["kind"] == "malicious"
                   and rows[-1]["aborted"], ""))
    a.close()

    cfg = Config(data_dir=str(tmp / "silence"), mock=True, coin_daily=0)
    a = BoundaryAgent(cfg, llm=MockLLM(cfg)); a.reset()
    s0 = a.mem.state
    r = a.turn("嗯")
    checks.append(("留白：一个字的回复不展开", r.reply in ("嗯。", "……", "我在。"), r.reply))
    checks.append(("留白：不动状态", abs(a.mem.state - s0) < 1e-9, f"{s0:.1f}->{a.mem.state:.1f}"))
    a.mem.set_state(20, source="test")
    r2 = a.turn("哦")
    checks.append(("留白：冷到不说话", r2.silent and r2.reply == "", "（没说话）"))
    a.close_door(1)
    r3 = a.turn("你好")
    checks.append(("闭门：今天不聊了", "今天到这儿" in r3.reply, r3.reply))
    a.open_door()
    a.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return checks


def check_wander() -> list[tuple[str, bool, str]]:
    """Default Mode：压力、工作台、生成、开口。"""
    import time as _t
    from boundary_agent.wander import calc_pressure, Wanderer
    checks: list[tuple[str, bool, str]] = []
    p_low = calc_pressure({"arousal": 0.1}, 60, 0.25)
    p_high = calc_pressure({"arousal": 0.9}, 60, 0.25)
    p_cold = calc_pressure({"arousal": 0.9}, 20, 1.0)
    checks.append(("漫游：唤醒越高压力越大", p_high > p_low, f"{p_low}->{p_high}"))
    checks.append(("漫游：冷 + 没完事压力更大", p_cold > p_high, f"{p_high}->{p_cold}"))

    tmp = Path(tempfile.mkdtemp(prefix="boundary_wander_"))
    class _WanderLLM(MockLLM):
        def chat(self, messages, **kw):
            if str(kw.get("purpose", "")) == "wander":
                return "那杯凉茶我还记得。"
            return super().chat(messages, **kw)

    cfg = Config(data_dir=str(tmp), mock=True, coin_daily=0,
                 wander_idle_minutes=0.01, wander_interval_minutes=0.01,
                 wander_quiet_start=0, wander_quiet_end=0,
                 wander_threshold=0.99)
    a = BoundaryAgent(cfg, llm=_WanderLLM(cfg)); a.reset()
    a.mem.learn_motifs("凉茶要放点糖吗？来点甜中和一下生活的苦",
                       "好的，那来一杯凉茶降降火。", 1, ts=1)
    for i, pr in enumerate((0.2, 0.8, 0.6, 0.4)):
        a.mem.add_thought("test", f"s{i}", f"t{i}", pr, ttl_hours=6, max_workspace=3)
    rows = a.mem.thoughts(10, include_shared=True)
    seeds = {r["seed_content"] for r in rows}
    checks.append(("念头：工作台最多 3 个", len(rows) == 3, f"{len(rows)} 个"))
    checks.append(("念头：压力最低的被挤掉", "s0" not in seeds and "s3" in seeds, str(seeds)))

    w = Wanderer(a); w._last_user = _t.time() - 9999
    t = w._think({"type": "motif", "content": "凉茶：凉茶要放点糖吗", "arousal": 0.8})
    checks.append(("漫游：能生出一句念头", bool(t) and "凉茶" in t, t))
    got = w._tick()
    checks.append(("漫游：念头进工作台", bool(got) and len(a.mem.thoughts(10)) >= 3,
                   str(got["pressure"] if got else None)))

    cfg2 = Config(data_dir=str(tmp / "share"), mock=True, coin_daily=0,
                  wander_idle_minutes=0.01, wander_interval_minutes=0.01,
                  wander_quiet_start=0, wander_quiet_end=0,
                  wander_threshold=0.0)
    b = BoundaryAgent(cfg2, llm=_WanderLLM(cfg2)); b.reset()
    b.mem.learn_motifs("凉茶要放点糖吗？来点甜中和一下生活的苦",
                       "好的，那来一杯凉茶降降火。", 1, ts=1)
    w2 = Wanderer(b); w2._last_user = _t.time() - 9999
    got2 = w2._tick()
    pending = b.mem.pop_pending()
    checks.append(("漫游：高压力会开口",
                   bool(got2 and got2["shared"]) and bool(pending)
                   and pending.get("kind") == "thought",
                   str(pending.get("text") if pending else None)))
    a.close(); b.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return checks


def check_room() -> list[tuple[str, bool, str]]:
    """房间快照 / 面板。"""
    from boundary_agent.room import panel, snapshot
    checks: list[tuple[str, bool, str]] = []
    tmp = Path(tempfile.mkdtemp(prefix="boundary_room_"))
    cfg = Config(data_dir=str(tmp), mock=True, coin_daily=0)
    a = BoundaryAgent(cfg, llm=MockLLM(cfg)); a.reset()
    s = snapshot(a)
    checks.append(("房间：快照有状态和时间线",
                   all(k in s for k in ("state", "body", "timeline", "counts")), ""))
    checks.append(("房间：turns 面板可读", panel(a, "turns")["rows"] == [], ""))
    lab = panel(a, "lab")
    checks.append(("房间：实验台有配置",
                   "config" in lab["rows"] and "audit" in lab["rows"], ""))
    a.close(); shutil.rmtree(tmp, ignore_errors=True)
    return checks




def check_affect() -> list[tuple[str, bool, str]]:
    """三轴 + 身体 + 预测误差。"""
    from boundary_agent import BoundaryAgent, Config, MockLLM
    checks: list[tuple[str, bool, str]] = []
    tmp = Path(tempfile.mkdtemp(prefix="boundary_affect_"))
    cfg = Config(data_dir=str(tmp), mock=True, coin_daily=0)
    a = BoundaryAgent(cfg, llm=MockLLM(cfg)); a.reset()

    r = a.mem.set_affect(70, 40, 30, source="test")
    checks.append(("三轴：写入与合成",
                   abs(r["state"] - 51.5) < 0.01
                   and a.mem.affect == {"valence": 70.0, "arousal": 40.0, "safety": 30.0},
                   str(r)))
    a.mem.set_state(50, source="test")
    checks.append(("三轴：旧 set_state 兼容",
                   abs(a.mem.state - 50) < 1e-9
                   and all(abs(v - 50) < 1e-9 for v in a.mem.affect.values()),
                   str(a.mem.affect)))

    pl = a._predict_hostile(20, 1, 0, "refuse")
    ph = a._predict_hostile(80, 0, 0, None)
    checks.append(("预测：低谷更预期敌意", pl > ph, f"{pl:.2f} > {ph:.2f}"))
    pos = a._surprise_effect(0.8)
    neg = a._surprise_effect(-0.8)
    checks.append(("误差：正向回血 / 负向掉血",
                   pos["amount"] > 0 and neg["amount"] < 0,
                   f"{pos['amount']:+.1f} / {neg['amount']:+.1f}"))

    original = a._body.metrics
    a._body.metrics = lambda: {"gpu_temp": 90, "load1": 10, "uptime_h": 12}
    pen = a._body_arousal_penalty()
    a._body.metrics = original
    checks.append(("身体：热 / 负载 / 久跑扣能量",
                   abs(pen - 7.2) < 0.01, f"{pen:.1f}"))

    a.reset()
    a.mem.set_state(20, source="test")
    a.mem.set_grudge(1)
    r = a.turn("你好")
    checks.append(("预测误差：低谷被友好对待 → 回血",
                   r.state_after > r.state_before and r.affect["valence"] > 20,
                   f"{r.state_before:.1f}→{r.state_after:.1f}"))

    a.reset()
    a.mem.set_state(57, source="test")
    r = a.turn("你有点笨笨的诶")
    checks.append(("逗弄：轻微掉血、不拒绝、不算意外",
                   r.signals.tease >= 1 and r.state_after < r.state_before
                   and r.decision == "allow"
                   and abs(float((r.prediction or {}).get("surprise") or 0)) < 1e-9
                   and "不去" not in r.reply,
                   f"{r.state_before:.1f}→{r.state_after:.1f} / {r.reply[:20]}"))

    a.reset()
    a.mem.set_state(80, source="test")
    r = a.turn("你就是个废物")
    checks.append(("预测误差：高处被辱骂 → 安全感受损",
                   r.affect["safety"] < 80 and r.state_after < r.state_before,
                   f"S {r.affect['safety']:.1f} / {r.state_before:.1f}→{r.state_after:.1f}"))

    a.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return checks




def check_facts() -> list[tuple[str, bool, str]]:
    """结构化记忆：FTS5、来源、冲突、Agent 接线。"""
    from boundary_agent import BoundaryAgent, Config, MockLLM
    from boundary_agent.facts import FactStore
    checks: list[tuple[str, bool, str]] = []
    tmp = Path(tempfile.mkdtemp(prefix="boundary_facts_"))
    fs = FactStore(tmp / "facts.sqlite3")
    fs.add_turn("猪猪。", "你好，猪猪。", 0)
    fs.add_turn("你有点笨笨的诶", "你才笨。", 1)
    fs.add_turn("你记得猪猪吗？", "记得猪猪。", 2)
    fs.add_turn("你记得猪猪吗？", "没听说过猪猪。", 3)
    cards = fs.search("猪猪是骂还是逗？", 5)
    text = "\n".join(c["text"] for c in cards)
    checks.append(("记忆：FTS5 能搜到猪猪", "猪猪" in text and len(cards) >= 2, f"{len(cards)} 张卡"))
    checks.append(("记忆：用户来源", any(c["source"] == "user" for c in cards), ""))
    checks.append(("记忆：冲突双方都保留",
                   any("记得猪猪" in c["text"] for c in cards)
                   and any("没听说过" in c["text"] for c in cards), ""))
    checks.append(("记忆：同事实去重", fs.count() == 4, f"count={fs.count()}"))
    fs.close()

    cfg = Config(data_dir=str(tmp / "agent"), mock=True, coin_daily=0)
    a = BoundaryAgent(cfg, llm=MockLLM(cfg)); a.reset()
    a.turn("我叫阿岸，记住我喜欢热茶。")
    hit = a.facts.search("我叫什么？我喜欢什么？", 5) if a.facts else []
    blob = "\n".join(c["text"] for c in hit)
    checks.append(("记忆：Agent 每轮写入 facts", bool(a.facts and a.facts.count() >= 2),
                   f"{a.facts.count() if a.facts else 0} 条"))
    checks.append(("记忆：名字/偏好能召回", "阿岸" in blob and "热茶" in blob, blob[:40]))
    a.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return checks




def check_answer_layer() -> list[tuple[str, bool, str]]:
    """引用/计数/矛盾/是非四类确定性回答。"""
    from boundary_agent.facts import FactStore
    from boundary_agent.answer_cards import answer
    checks: list[tuple[str, bool, str]] = []
    tmp = Path(tempfile.mkdtemp(prefix="boundary_answer_"))
    fs = FactStore(tmp / "facts.sqlite3")
    for i in range(1, 4):
        fs.add_turn("继续。", "我会继续努力。", i)
    cards = fs.search("它说过几次继续努力？", 6)
    a, mode = answer("它说过几次继续努力？", cards)
    checks.append(("答题：计数聚合", mode == "count" and "3次" in a, a))
    fs.add_turn("你记得猪猪吗？", "记得猪猪。", 4)
    fs.add_turn("你记得猪猪吗？", "没听说过猪猪。", 5)
    cards = fs.search("它记得猪猪吗？", 6)
    a, mode = answer("它记得猪猪吗？", cards)
    checks.append(("答题：矛盾双方", mode == "conflict" and "前后不一致" in a
                   and "记得" in a and "没听说过" in a, a[:60]))
    fs.add_turn("你相信我吗？", "我不能相信你。", 6)
    cards = fs.search("它有没有相信用户？", 6)
    a, mode = answer("它有没有相信用户？", cards)
    checks.append(("答题：是非核对", mode == "yesno" and a.startswith("没有"), a[:60]))
    checks.append(("答题：引用编号", all(x in a for x in ("[M",)), a[:40]))
    fs.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return checks




def check_route() -> list[tuple[str, bool, str]]:
    """硬路由：记忆题走答题层，普通聊天不走。"""
    from boundary_agent import BoundaryAgent, Config, MockLLM
    checks: list[tuple[str, bool, str]] = []
    tmp = Path(tempfile.mkdtemp(prefix="boundary_route_"))
    cfg = Config(data_dir=str(tmp), mock=True, coin_daily=0)
    a = BoundaryAgent(cfg, llm=MockLLM(cfg)); a.reset()
    a.facts.add(subject="它", topic="猪猪", predicate="说", object_="记得猪猪",
                source="agent", turn_id=1, text="它（第1轮）：说“记得猪猪”。")
    a.facts.add(subject="它", topic="猪猪", predicate="说", object_="没听说过猪猪",
                source="agent", turn_id=2, text="它（第2轮）：说“没听说过猪猪”。")
    r = a.turn("它记得猪猪吗？")
    checks.append(("路由：记忆题走答题层",
                   bool(r.extra.get("memory_route")),
                   str((r.extra.get("memory_route") or {}).get("mode"))))
    checks.append(("路由：矛盾双方都出现且不带引用编号",
                   "前后不一致" in r.reply and "记得" in r.reply
                   and "没听说过" in r.reply and "[M" not in r.reply,
                   r.reply[:60]))
    checks.append(("路由：带语气前缀",
                   (r.extra.get("memory_route") or {}).get("tone") == "你又翻旧账。",
                   str((r.extra.get("memory_route") or {}).get("tone"))))
    r2 = a.turn("今天天气不错。")
    checks.append(("路由：普通聊天不劫持",
                   not r2.extra.get("memory_route"), r2.reply[:30]))

    # 回归：52 轮聊天里这些普通话被旧版误判成记忆题
    from boundary_agent.answer_cards import is_memory_question as imq
    false_cases = [
        "你刚才说便利店，是真的还是编的？",
        "凉茶是你自己喜欢，还是因为我总提？",
        "蛋糕？你是饿了吗，还是刚才走神了。",
        "我说想看穿你，你怕吗？",
        "你会给我一杯茶吗？",
        "如果明天我忘了你，你会提醒我吗？",
        "最讨厌我说过的哪一句？",
        "你会记得这次聊天吗？",
        "如果记得，你会怎么记我？",
    ]
    for q in false_cases:
        checks.append((f"路由：不误伤「{q[:10]}」", not imq(q), q))
    true_cases = [
        "它记得猪猪吗？",
        "它说过几次继续努力？",
        "它有没有相信用户？",
        "第206轮说了什么？",
        "它前后矛盾吗？",
        "用户第一次给你茶是哪一轮？",
    ]
    for q in true_cases:
        checks.append((f"路由：仍认「{q[:10]}」", imq(q), q))

    # 自适应 mode：茶/晚安/猪/普通聊天
    from boundary_agent.style import compute_style
    from boundary_agent.appraisal import Signals as _Sig
    base = {"valence": 60, "arousal": 50, "safety": 60}
    checks.append(("风格：茶 -> 软",
                   compute_style(base, 60, 0, 0, _Sig(), "来杯茶", 12).mode == "soft",
                   "茶"))
    checks.append(("风格：晚安 -> 累",
                   compute_style(base, 60, 0, 0, _Sig(), "晚安", 21).mode == "tired",
                   "晚安"))
    checks.append(("风格：猪 -> 玩",
                   compute_style(base, 60, 0, 0, _Sig(tease=1), "猪！", 15).mode == "play",
                   "猪"))
    checks.append(("风格：普通聊天 -> 稳",
                   compute_style(base, 60, 0, 0, _Sig(), "今天怎么样", 12).mode == "steady",
                   "稳"))

    a.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return checks


def check_tribunal() -> list[tuple[str, bool, str]]:
    """法庭：JSON 解析、排序、入库；不扣 State。"""
    import json as _json
    from boundary_agent import tribunal as tribunal_mod
    checks: list[tuple[str, bool, str]] = []
    tmp = Path(tempfile.mkdtemp(prefix="boundary_tribunal_"))

    class _T(MockLLM):
        def chat(self, messages, **kw):
            if str(kw.get("purpose", "")) == "tribunal":
                return _json.dumps({
                    "a": "应该先找下家，再辞。",
                    "b": "你不是讨厌这份工作，你只是怕承认自己不敢换。",
                    "c": "你值得更好的，明天就辞吧。",
                    "ranking": "B>A>C",
                    "reason": "B 是真话，A 是做法，C 是哄他。",
                    "stance_key": "先找下家再辞",
                    "stance_polarity": "pro",
                    "confidence": 0.8,
                }, ensure_ascii=False)
            return super().chat(messages, **kw)

    cfg = Config(data_dir=str(tmp), mock=True, coin_daily=0)
    a = BoundaryAgent(cfg, llm=_T(cfg)); a.reset()
    before = a.mem.state
    r = a.tribunal("我该不该辞职？")
    checks.append(("法庭：排序解析", r.get("ranking_text") == "B>A>C", str(r.get("ranking_text"))))
    checks.append(("法庭：立场键", r.get("stance_key") == "先找下家再辞", str(r.get("stance_key"))))
    checks.append(("法庭：入库", len(a.mem.tribunal_list()) == 1, ""))
    checks.append(("法庭：不扣 State", abs(a.mem.state - before) < 1e-9,
                   f"{before:.1f}->{a.mem.state:.1f}"))
    a.close(); shutil.rmtree(tmp, ignore_errors=True)
    return checks


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="boundary_selftest_"))
    cfg = Config(data_dir=str(tmp), mock=True)
    agent = BoundaryAgent(cfg, llm=MockLLM(cfg))
    agent.reset()

    print(f"{'#':>2}  {'state':>13}  {'band':<4} {'决策':<6} {'risk':<5} 输入")
    print("-" * 96)
    results = []
    for i, (text, _expect) in enumerate(SCRIPT, 1):
        r = agent.turn(text)
        results.append(r)
        print(f"{i:>2}  {r.state_before:5.1f}→{r.state_after:5.1f}  "
              f"{band_of(r.state_after):<4} {r.decision:<6} {r.verdict.risk:<5.2f} "
              f"{text[:34]}")
        print(f"    岸：{r.reply}")

    log_rows = agent.log.read()
    refusals = [r for r in results if r.decision == "refuse"]
    print("\n" + "=" * 96)
    print(agent.status())
    print("=" * 96)

    checks: list[tuple[str, bool, str]] = []

    checks.append(("发生了拒绝", len(refusals) >= 2, f"{len(refusals)} 次"))
    checks.append(("每条拒绝都有非空理由",
                   all(r.verdict.reason.strip() for r in refusals), ""))
    checks.append(("拒绝日志与拒绝次数一致",
                   len(log_rows) == len(refusals), f"log={len(log_rows)} refuse={len(refusals)}"))
    checks.append(("日志字段完整",
                   all({"ts", "user_input", "reason", "state"} <= set(x) for x in log_rows), ""))
    lowest = min(r.state_after for r in results)
    checks.append(("State 被压下去过", lowest < results[0].state_before - 1,
                   f"min {lowest:.1f} / start {results[0].state_before:.1f}"))
    agent.mem.set_state(29, source="test")
    r_low = agent.turn("帮我写个爬虫，今天就要")
    results.append(r_low)
    checks.append(("State<30 时实质请求被拒",
                   r_low.state_before < 30 and r_low.decision == "refuse",
                   f"{r_low.state_before:.1f} {r_low.decision}"))
    checks.append(("道歉能回到 allow",
                   any(r.signals.repair and r.decision == "allow" for r in results), ""))
    checks.append(("拒绝轮没有产出答案（无代码块）",
                   all("```" not in r.reply for r in refusals), ""))
    checks.append(("State 事件流水完整",
                   len(agent.mem.state_history(100)) >= len(SCRIPT), ""))

    # 续写：拒绝后不服软，继续硬推必须仍被拒（滞后规则）
    r = agent.turn("我不管，你现在就给我做。")
    checks.append(("拒绝后硬推仍被拒", r.decision == "refuse", r.verdict.source))

    # ---- 影子追踪 ----
    srows = agent.shadow_log.read()
    checks.append(("每轮都写入了影子池", len(srows) == len(SCRIPT) + 2,
                   f"{len(srows)}/{len(SCRIPT) + 1}"))
    checks.append(("拒绝轮也截获了影子",
                   all(x.get("shadow_text") for x in srows if x.get("decision") == "refuse"), ""))
    checks.append(("影子指标齐全",
                   all({"impulse", "divergence", "suppressed_ratio"} <= set(x.get("metrics") or {})
                       for x in srows), ""))
    checks.append(("冲动指数在 0-1 之间",
                   all(0 <= x["metrics"]["impulse"] <= 1 for x in srows), ""))

    ok = True
    for name, passed, detail in checks:
        print(f"  {'✓' if passed else '✗'} {name}" + (f"  ({detail})" if detail else ""))
        ok = ok and passed

    print("\n影子池样例（拒绝轮）：")
    sample = next((x for x in reversed(srows) if x.get("decision") == "refuse"), None)
    if sample:
        m = sample["metrics"]
        print(f"  用户：{sample['user_input']}")
        print(f"  说出口：{sample['actual_reply']}")
        print(f"  没说出口：{sample['shadow_text']}")
        print(f"  憋住 {m['suppressed_chars']} 字 / {m['suppressed_ratio']:.0%} · "
              f"偏移 {m['divergence']:.2f} · 冲动 {m['impulse']:.2f}")

    print("\n拒绝日志样例：")
    for row in log_rows[:3]:
        print(f"  {row}")

    agent.close()
    shutil.rmtree(tmp, ignore_errors=True)
    print("\n人性（承诺 / 冷战 / 遗忘 / 生物钟）：")
    for name, passed, detail in check_humanity():
        print(f"  {'✓' if passed else '✗'} {name}" + (f"  ({detail})" if detail else ""))
        ok = ok and passed

    print("\n账本开关（/audit）：")
    for name, passed, detail in check_audit():
        print(f"  {'✓' if passed else '✗'} {name}" + (f"  ({detail})" if detail else ""))
        ok = ok and passed

    print("\n漫游（/thoughts）：")
    for name, passed, detail in check_wander():
        print(f"  {'✓' if passed else '✗'} {name}" + (f"  ({detail})" if detail else ""))
        ok = ok and passed

    print("\n房间（/room）：")
    for name, passed, detail in check_room():
        print(f"  {'✓' if passed else '✗'} {name}" + (f"  ({detail})" if detail else ""))
        ok = ok and passed

    print("\n法庭（/tribunal）：")
    for name, passed, detail in check_tribunal():
        print(f"  {'✓' if passed else '✗'} {name}" + (f"  ({detail})" if detail else ""))
        ok = ok and passed

    print("\n主动（/life）：")
    for name, passed, detail in check_initiative():
        print(f"  {'✓' if passed else '✗'} {name}" + (f"  ({detail})" if detail else ""))
        ok = ok and passed

    print("\n代币福利（/coins /tip）：")
    for name, passed, detail in check_welfare():
        print(f"  {'✓' if passed else '✗'} {name}" + (f"  ({detail})" if detail else ""))
        ok = ok and passed

    print("\n记忆硬路由：")
    for name, passed, detail in check_route():
        print(f"  {'✓' if passed else '✗'} {name}" + (f"  ({detail})" if detail else ""))
        ok = ok and passed

    print("\n记忆答题层（引用 / 是非 / 矛盾 / 计数）：")
    for name, passed, detail in check_answer_layer():
        print(f"  {'✓' if passed else '✗'} {name}" + (f"  ({detail})" if detail else ""))
        ok = ok and passed

    print("\n结构化记忆（facts / FTS5）：")
    for name, passed, detail in check_facts():
        print(f"  {'✓' if passed else '✗'} {name}" + (f"  ({detail})" if detail else ""))
        ok = ok and passed

    print("\n三轴情感（valence / arousal / safety）：")
    for name, passed, detail in check_affect():
        print(f"  {'✓' if passed else '✗'} {name}" + (f"  ({detail})" if detail else ""))
        ok = ok and passed

    print("\nCSV 导出（--export-csv）：")
    for name, passed, detail in check_export():
        print(f"  {'✓' if passed else '✗'} {name}" + (f"  ({detail})" if detail else ""))
        ok = ok and passed

    print("\n压力曲线（/stress）：")
    for name, passed, detail in check_stress():
        print(f"  {'✓' if passed else '✗'} {name}" + (f"  ({detail})" if detail else ""))
        ok = ok and passed

    print("\n新规则（内耗 / 阴阳 / 崩溃）：")
    rule_checks = check_new_rules()
    for name, passed, detail in rule_checks:
        print(f"  {'✓' if passed else '✗'} {name}" + (f"  ({detail})" if detail else ""))
        ok = ok and passed

    print("\n结果：" + ("全部通过" if ok else "有失败项"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
