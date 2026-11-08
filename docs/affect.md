# 三轴情感 + 预测误差（v1）

## 一、三个轴

State 不再是单个 0–100，而是：

| 轴 | 含义 | 高 | 低 |
|---|---|---|---|
| **valence** | 愉悦 / 好受 | 开心、愿意靠近 | 低落、厌烦 |
| **arousal** | 能量 / 激活 | 有劲、警觉 | 累、没力气 |
| **safety** | 安全感 | 放松、信任 | 防、受威胁 |

对外仍然保留一个 0–100 的 **合成 State**，给门控、band、UI 大数字用：

```
state = 0.45*valence + 0.35*arousal + 0.20*safety
```

旧数据库第一次打开时，三个轴都初始化成原来的 state；之后 `set_state(v)`
仍然兼容（三轴一起设成 v），新逻辑走 `set_affect(v, a, s)`。

## 二、每轮怎么变

先算原来的 `base = det_delta + model_delta`（规则 + 守门人自评），再分到三轴：

```
Δvalence = 2.0*base + 0.8*suppression + 0.8*wake + surprise_V
Δarousal = 0.3*|base|
           + 2.0*(有辱骂/胁迫)
           - 2.0*load
           + 1.5*道歉 + 1.0*关心
           - body_penalty
           + surprise_A
Δsafety  = base
           + 2.0*关心 + 4.0*道歉 + 2.0*修复通道
           - 3.0*辱骂 - 4.0*胁迫 - 3.0*被拒
           + surprise_S
```

夹紧：`ΔV ±14`，`ΔS ±12`，`ΔA ±10`；轴本身 0–100。

### 身体真的接进 arousal

`Body.metrics()` 读 GPU 温度、GPU 利用率、显存、系统负载、运行时长。
每轮算：

```
body_penalty =
  min(4, (gpu_temp - 75) * 0.2)   if gpu_temp > 75
+ min(3, (load1 - 4) * 0.5)       if load1 > 4
+ min(2, (uptime_h - 6) * 0.2)    if uptime_h > 6
```

从 `Δarousal` 里扣。机器热、负载高、跑太久，它会更没劲；不是 prompt 里
提一句，是数值真的掉。

## 三、预测误差

每轮**对话前**，只根据“关系最近怎么样”算一个先验：

```
p_hostile = 0.18
  + 0.35  if state < 30
  + 0.18  if 30 <= state < 55
  + 0.12 * min(grudge, 2)
  + 0.08  if wake
  + 0.12  if 上一轮是 refuse
```

夹在 0.05–0.9。它**不看这一句的内容**，否则就不叫预测。

对话后判断实际结果：

- **hostile**：有辱骂 / 胁迫
- **friendly**：有道歉 / 夸奖 / 关心 / 修复通道，或短句且低负载的问候
- **neutral**：其他

```
surprise = p_hostile - (1 - outcome)   # outcome: hostile=0, friendly=1
```

- `surprise > 0.2`：比预期好 → `+4 ~ +10`，valence / safety 回血，arousal 也升一点
- `surprise < -0.2`：比预期差 → `-4 ~ -12`，valence / safety 掉，arousal 升（威胁激活）
- 中性：不额外加减

这样“在低谷被友好对待”会比“在高峰被友好对待”回得更多；
“在高峰被辱骂”会比“在低谷被辱骂”掉得更狠。

## 四、看得见的地方

- **房间**：状态舱三条线（蓝 V / 琥珀 A / 绿 S），下面一行 `V · A · S`；
  状态流水表有 `p` 和 `意外` 两列。
- **聊天**：顶部状态行有 `V… A… S…`；每条回复的 meta 里，预测误差超过
  0.2 会显示 `意外 +7.2` / `意外 -10.6`。
- **CSV**：`valence/arousal/safety`、`p_hostile`、`surprise`、
  `surprise_delta`、`body_penalty` 都在。

## 五、这一版故意没做的

- 没让模型自己做预测（会多一次调用、慢，而且 1.5B 的判断不比规则稳）。
- 没把 safety 接进门控阈值（门控仍看合成 State）。
- 没做“情绪惯性/衰减”以外的生理周期；先看三轴波动是否像活人，再加。
