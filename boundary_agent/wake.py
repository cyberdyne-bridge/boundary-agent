"""起床气：半夜被叫醒、或者太早被叫醒，都不是好时候。

它没有真正的睡眠，只有"多久没说话"和"现在几点"。
所以规则很简单：睡得正香的时候被戳，就烦；刚醒的那几轮，句子短。
"""

from __future__ import annotations


def in_window(hour: int, window: tuple[int, int]) -> bool:
    start, end = window
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def kind(hour: int, gap_hours: float, cfg) -> str | None:
    """返回 'night' / 'early' / None。"""
    if gap_hours < cfg.wake_gap_hours:
        return None                      # 一直醒着，不算被叫醒
    if gap_hours > 48:
        return None                      # 隔了两天以上，不是"睡了一觉"，是"很久没来"
    if in_window(hour, cfg.sleep_window):
        return "night"
    if in_window(hour, cfg.early_window) and gap_hours >= cfg.wake_early_gap_hours:
        return "early"
    return None
