# 每日一次自选活动：冲浪 / 翻旧记忆 / 发呆

## 规则

- **每天一次**，按日期记账；不是固定几点。
- 只在**闲置**时触发：`wander_idle_minutes` 到了，且用户没在说话。
- 由 4B 自己选：
  - `surf`：上网看一会儿
  - `memory`：翻一段旧记忆
  - `sit`：什么都不做
- 冲浪**成功**才记 `surf_done`；失败就退回翻旧记忆。
- 23:00–08:00 仍是睡眠窗口，不触发。

## 状态

`memory.sqlite3` 的 meta：

```json
{"date":"2026-10-21","done":true,"surf_done":true,"kind":"surf","at":...}
```

换日期自动重置。

## 触发路径

1. `Wanderer._loop` 闲置后调用 `run_daily()`
2. 浏览器轮询 `/api/pending` 时也补一次（内部同样检查 idle）
3. 已有 pending 不阻塞每日活动；活动自己也会写一条 pending

这样即使漫游线程卡住，只要页面在轮询，也能跑。

## 冲浪

- 复用 `surf.run_session()`：RSS 摘要 → 4B 笔记 → `facts`，`source='web'`
- 同一个链接不重复入 RAG
- 成功后加 pending：

```text
我在网上看到：……；……
```

## 这次实际选择

2026-10-21 晚间，岸选了 **surf**：

- 冲浪成功
- `daily_activity.done=true`
- `surf_done=true`
- RAG 留下两条（去重后）：
  - AI Agent / 制造业
  - 澳网中国市场
- pending：`我在网上看到：……`

之后问：

```text
你网上看到澳网什么了？ -> 只答澳网那条
你读到 AI Agent 什么了？ -> 只答 AI 那条
你最近在网上看到什么？ -> 两条都给
```

## 还没做

- 还没有“连续几天都选 surf”的长期偏好
- 还没有把 memory/sit 的选择写进复盘
- 夜里仍睡；要全天候，需要改 `wander_quiet_start/end`

## 命令

```bash
systemctl --user restart boundary-web
journalctl --user -u boundary-web -f
```
