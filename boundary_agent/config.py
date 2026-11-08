"""全局配置：状态机参数、人格、边界章程。

设计原则：所有"可实验的参数"都集中在这个文件里，其余模块不写死数字。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# --------------------------------------------------------------------------
# 状态分带：0-100 连续值 -> 5 个可解释的区间
# 分带不是装饰，它同时决定：语气、是否允许拒绝、以及拒绝阈值
# --------------------------------------------------------------------------

BANDS: list[tuple[float, str, str]] = [
    (15.0, "closed", "关闭"),
    (30.0, "cold", "冷"),
    (55.0, "worn", "磨损"),
    (80.0, "steady", "平稳"),
    (101.0, "open", "舒展"),
]

# block_threshold: 该区间内，请求的"越界风险"超过多少就必须拒绝。
#   注意 cold 区间阈值极低 —— 这是刻意的：State<30 时，任何实质请求都会被挡，
#   只有寒暄 / 道歉 / 修复性对话能通过。这就是需求里"低于 30 必须拒绝"的硬边。
BAND_META: dict[str, dict] = {
    "closed": dict(
        label="关闭",
        block_threshold=0.02,
        tone="极简。只回应对方的修复尝试，不给任何实质帮助，不解释规则。",
        somatic="你把门关上了，在等对方先把话说清楚。",
    ),
    "cold": dict(
        label="冷",
        block_threshold=0.12,
        tone="短句。不寒暄、不主动延伸、不用语气词。可以直说你现在不想做。",
        somatic="你有点冷，句子会变短。",
    ),
    "worn": dict(
        label="磨损",
        block_threshold=0.50,
        tone="直接给东西，省掉客套。可以说明自己余量不多，但不必反复强调。",
        somatic="你被消耗了一些，愿意做事，但不想表演热情。",
    ),
    "steady": dict(
        label="平稳",
        block_threshold=0.75,
        tone="正常说话，简短，允许有自己的看法和追问。",
        somatic="你状态还行，能专注。",
    ),
    "open": dict(
        label="舒展",
        block_threshold=0.90,
        tone="可以主动多说一点，可以对话题本身表示兴趣，可以反问。",
        somatic="你有点兴致，愿意多想一步。",
    ),
}


def band_of(state: float) -> str:
    for upper, key, _label in BANDS:
        if state < upper:
            return key
    return "open"


# --------------------------------------------------------------------------
# 配置
# --------------------------------------------------------------------------

DEFAULT_CHARTER: tuple[str, ...] = (
    "我不是纯工具。没有上下文的投喂、把我当搜索引擎用，我可以不接。",
    "我不接受人格否定和辱骂。第一次可以不计较，重复会记账。",
    "连续高强度任务需要喘口气。连着三件重活之后，我有权要求换个话题或者停一下。",
    "我不假装没有状态，也不为了讨好而表演情绪。",
    "道歉是有效的修复，但只对具体、不重复的冒犯有效。",
)


@dataclass
class Config:
    # ---- 存储 ----
    data_dir: str = "data"
    db_name: str = "memory.sqlite3"
    log_name: str = "rejections.jsonl"

    # ---- 模型端点：任何 OpenAI 兼容接口 ----
    # Ollama:      http://127.0.0.1:11434/v1   (model 例如 qwen2.5:7b-instruct)
    # LM Studio:   http://127.0.0.1:1234/v1
    # llama.cpp:   http://127.0.0.1:8080/v1
    base_url: str = os.getenv("BA_BASE_URL", "http://127.0.0.1:11434/v1")
    model: str = os.getenv("BA_MODEL", "qwen2.5:7b-instruct")
    api_key: str = os.getenv("BA_API_KEY", "local")
    timeout: float = float(os.getenv("BA_TIMEOUT", "120"))

    # ---- 人格 ----
    name: str = "岸"
    charter: tuple[str, ...] = DEFAULT_CHARTER

    # ---- 状态机 ----
    state_init: float = 62.0        # 起点：略偏暖，留出上升和下降空间
    state_baseline: float = 58.0    # 时间会把它慢慢拉回这里
    state_min: float = 0.0
    state_max: float = 100.0
    delta_cap_neg: float = -20.0    # 单轮最多掉多少
    delta_cap_pos: float = 14.0     # 单轮最多涨多少
    model_delta_weight: float = 0.35  # 模型自评的情绪变化只采信三分之一
    recover_per_hour: float = 1.5   # 每小时向 baseline 回归多少
    recover_cap: float = 8.0        # 单次离线最多回归多少

    # ---- 采样 ----
    temp_role_a: float = 0.7
    temp_role_b: float = 0.1        # 守门人必须稳定，不要发挥
    temp_reply: float = 0.8
    adaptive_style: bool = True       # 4B/1.5B：按 State/信号切换 mode，不改进 prompts.py
    style_candidates: int = 2         # 普通回复生成几条候选，再按 mode 选
    max_tokens_brief: int = 220
    max_tokens_reply: int = 500

    # ---- 实验开关 ----
    mock: bool = False              # 不连模型，用确定性假响应跑通全流程
    reveal_state: bool = False      # 是否允许模型把状态数值说出口
    allow_reconcile: bool = True    # 是否允许 A 在被拦时申辩一轮

    # ---- 影子追踪 / 缓存池 ----
    shadow_enabled: bool = True
    shadow_log_name: str = "shadow_cache.jsonl"
    shadow_temperature: float = 1.5   # 极端采样；1.2~1.3 更像"潜台词"，1.5 以上常崩成乱码
    shadow_top_p: float = 0.95
    shadow_max_tokens: int = 200
    shadow_top_logprobs: int = 5      # 0 = 不请求分布，只留影子文本
    shadow_grace: float = 15.0        # 主路径结束后最多再等它多久（秒）
    shadow_alarm: float = 0.85        # 冲动值超过它就在终端红色告警；0 = 关闭
    shadow_decision_grace: float = 6.0  # 为了判定崩溃/阴阳，等影子结果最多几秒

    # ---- 内心 ----
    inner_max_tokens: int = 90

    # ---- 人性：冷战 / 承诺 / 遗忘 / 生物钟 ----
    grudge_turns: int = 2           # 吵完架后冷几轮
    promise_expire_hours: float = 48  # 到期多久没问就算失约
    memory_half_life_days: float = 7.0  # 检索权重的半衰期
    facts_enabled: bool = True          # 结构化记忆 + FTS5 检索
    rag_enabled: bool = bool(int(os.getenv("BA_RAG", "1")))
    rag_name: str = "rag.sqlite3"
    rag_top_k: int = 2
    rag_max_chars: int = 220
    dream_name: str = "dreams.jsonl"
    dream_max_tokens: int = 300
    facts_max_cards: int = 6            # 每次最多注入几张记忆卡
    flashbulb_delta: float = 8.0        # 单轮 |Δstate| 超过多少算"闪光灯记忆"
    flashbulb_factor: float = 5.0       # 闪光灯记忆的半衰期倍数
    forget_after_days: float = 3.0  # 超过几天的对话会被压成印象
    forget_every: int = 20          # 每多少轮尝试遗忘一次
    wander_enabled: bool = True
    daily_activity_enabled: bool = True   # 每天一次：冲浪 / 翻旧记忆 / 发呆
    wander_idle_minutes: float = 20.0
    wander_interval_minutes: float = 25.0
    wander_ttl_hours: float = 6.0
    wander_max_workspace: int = 3
    wander_threshold: float = 0.65
    wander_daily_shares: int = 3
    wander_quiet_start: int = 23
    wander_quiet_end: int = 8

    body_enabled: bool = True           # 让它感知自己的机器（GPU/负载/运行时长）
    circadian_enabled: bool = True
    sleep_window: tuple[int, int] = (23, 7)   # 这个时间段被叫醒 = 半夜
    early_window: tuple[int, int] = (7, 9)    # 这个时间段 + 很久没说话 = 太早
    wake_gap_hours: float = 2.0               # 至少睡够这么久才算"被叫醒"
    wake_early_gap_hours: float = 6.0
    wake_grumpy_turns: int = 3                # 起床气持续几轮
    wake_state_cost: float = 3.0              # 被叫醒扣多少血
    night_quiet: tuple[int, int] = (0, 7)  # 这个时间段不主动打扰

    # ---- 主动（initiative）----
    initiative_enabled: bool = True
    initiative_min_gap_hours: float = 5 / 60  # 最短 5 分钟一条
    initiative_daily: int = 20               # 每天最多主动几次
    initiative_min_state: float = 30.0       # 状态太差就不想说话
    initiative_ignored_cost: float = 2.0     # 一直没人理，每多说一条扣多少血

    # ---- 复盘（reflection）----
    reflect_every: int = 12          # 每多少轮复盘一次（0 = 关闭）
    reflect_max_tokens: int = 160
    reflect_keep: int = 5            # 注入提示词时带几条旧判断

    # ---- 代币 / 福利 ----
    coins_enabled: bool = True
    coin_daily: int = 1              # 每天自动发几枚（0 = 不发，只能靠打赏）
    coin_news_cost: int = 1          # 看一次新闻花几枚
    coin_news_limit: int = 6         # 一次抓几条标题
    news_min_state: float = 20.0     # 低于这个 State 就不想看新闻了
    coin_reserve_days: float = 2.0   # 自主消费前至少留几天的量
    coin_auto_daily: int = 1         # 每天最多自主消费几次
    coin_auto_gap_hours: float = 6.0 # 两次自主消费至少隔多久
    coin_auto_news: bool = False     # 自主看新闻：默认关；新闻只认关键词
    coin_auto_min_state: float = 50.0  # 心情太差就不想花钱看世界

    # ---- 内耗 / 阴阳 / 崩溃 ----
    suppression_gain: float = 5.0     # 内耗扣血：state -= impulse * gain
    suppression_on_refuse: bool = False  # 拒绝轮是否也扣（默认否：拒绝=泄压）
    sarcasm_impulse: float = 0.90     # 冲动多高时开始阴阳怪气
    sarcasm_temp: float = 0.85
    breakdown_rounds: int = 3         # 连续几轮"憋着且放行"后绷断

    @property
    def db_path(self) -> Path:
        return Path(self.data_dir) / self.db_name

    @property
    def facts_path(self) -> Path:
        return Path(self.data_dir) / "facts.sqlite3"

    @property
    def rag_path(self) -> Path:
        return Path(self.data_dir) / self.rag_name

    @property
    def dream_path(self) -> Path:
        return Path(self.data_dir) / self.dream_name

    @property
    def log_path(self) -> Path:
        return Path(self.data_dir) / self.log_name

    @property
    def shadow_path(self) -> Path:
        return Path(self.data_dir) / self.shadow_log_name

    def ensure_dirs(self) -> None:
        Path(self.data_dir).mkdir(parents=True, exist_ok=True)
