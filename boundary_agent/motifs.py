"""暗号本：你们之间反复出现的意象。

不是所有名词都收，只收具体的、能当画面用的词（凉茶、糖、雨、便利店）。
第一次是谁说的、说过几次、原句是什么，都记下来。
"""

from __future__ import annotations

import re

# 只收"能当画面"的词。太长/太抽象的不收。
LEXICON = {
    "凉茶", "茶", "咖啡", "奶茶", "糖", "蜂蜜", "柠檬", "橘子", "苹果", "西瓜",
    "冰淇淋", "火锅", "面包", "蛋糕", "蛋挞", "粥", "汤", "酒", "药",
    "雨", "雪", "风", "雾", "霜", "雷", "闪电", "彩虹", "云", "太阳", "月亮",
    "星星", "日落", "日出", "海", "浪", "潮水", "岛", "船", "河", "山", "森林",
    "沙漠", "草地", "花园", "树", "花", "猫", "狗", "鸟", "鱼",
    "窗", "灯", "门", "钥匙", "影子", "梦", "火", "照片", "电影", "歌", "琴",
    "雨伞", "围巾", "手套", "帽子", "鞋", "口袋", "硬币", "书", "纸", "笔",
    "枕头", "被子", "房子", "房间", "桌子", "椅子", "沙发", "楼梯", "阳台",
    "车站", "桥", "路", "夜", "深夜", "凌晨", "早晨", "黄昏", "春天", "夏天",
    "秋天", "冬天", "便利店",
}

_STOP = {
    "一下", "你的", "我的", "他的", "她的", "这个", "那个", "什么", "就是",
    "还是", "可以", "没有", "不是", "我们", "你们", "他们", "自己", "觉得",
    "真的", "一个", "现在", "然后", "因为", "所以", "但是", "如果", "已经",
    "有点", "一点", "这些", "那些", "时候", "东西", "事情", "今天", "明天",
    "昨天", "知道", "感觉", "可能", "应该", "需要", "喜欢", "想要", "出来",
    "起来", "下来", "过去", "回来", "上去", "进去", "这样", "那样", "怎样",
    "为什么", "怎么", "多少", "非常", "特别", "其实", "只是", "不过", "而且",
    "或者", "以及", "还有", "一样", "一起", "一直", "一定", "最后", "第一",
    "第二", "的话", "说点", "点什", "么都", "都不", "不怎",
}

_CJK = re.compile(r"[\u4e00-\u9fff]+")
_WORD = re.compile(r"[a-zA-Z0-9_]+")
_SPLIT = re.compile(r"[。！？!?\n；;]")


def tokens(text: str) -> set[str]:
    toks = {w.lower() for w in _WORD.findall(text or "")}
    for run in _CJK.findall(text or ""):
        if len(run) == 1:
            toks.add(run)
        for i in range(len(run) - 1):
            toks.add(run[i:i + 2])
    return toks


def match_tokens(text: str) -> set[str]:
    return tokens(text) - _STOP


def extract(text: str) -> set[str]:
    """最长优先，避免「凉茶」又算「茶」。"""
    found: set[str] = set()
    rest = text or ""
    for key in sorted(LEXICON, key=len, reverse=True):
        if key and key in rest:
            found.add(key)
            rest = rest.replace(key, " ")
    return found


def sentence_with(text: str, key: str) -> str:
    clean = (text or "").strip()
    if len(clean) <= 120:
        return clean
    for clause in _SPLIT.split(clean):
        if key in clause:
            return clause.strip()[:120]
    return clean[:120]


def triggers(note: str) -> list[str]:
    return sorted(match_tokens(note))[:80]
