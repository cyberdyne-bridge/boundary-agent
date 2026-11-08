# A → B → C → D 收尾

## A. 记忆路由：只认明确的记忆题

旧版 `_MEM_CUES` 只要句子里有「茶 / 怕 / 记得 / 刚才 / 最」就可能劫持。  
52 轮聊天里 11 次把普通话插成账本。

现在只认：

- `你还记得…吗`
- `第几轮 / 哪一轮 / 第一次`
- `说过几次 / 有没有说过`
- `前后矛盾 / 不一致`
- `原话 / 引用 / 记忆卡`

明确排除：

- `你会给我一杯茶吗`
- `你怕吗`
- `你会记得这次聊天吗`
- `如果明天我忘了你`
- `最讨厌我说过的哪一句`
- `凉茶是你自己喜欢，还是因为我总提`

实测：

```text
你会给我一杯茶吗？  -> route=None，正常聊天
你会记得这次聊天吗？-> route=None，正常聊天
它记得猪猪吗？      -> route=conflict，仍走账本
```

测试：`selftest.py` **175 项全过**，新增 9 条假阳性 + 6 条真记忆题回归。

## B. 开机自启

用户服务：

- `boundary-model.service`：`serve.sh` 默认 4B，4K / b512 / ub128
- `boundary-web.service`：8765，`BOUNDARY_NO_BROWSER=1`，不再每次弹浏览器

状态：

```text
boundary-model.service  active / enabled
boundary-web.service    active / enabled
Linger=yes
```

即使用户没登录，也会随 user manager 起来。  
仓库里放了副本：`deploy/boundary-*.service`。

常用命令：

```bash
systemctl --user status boundary-model boundary-web
systemctl --user restart boundary-web
journalctl --user -u boundary-web -f
```

## C. 自适应第二版

### 触发

- 茶 / 抱抱 / 陪你 / 辛苦 -> **软**
- 深夜 / 困 / 累 / 晚安 / 睡 / 起床气 -> **累**
- 猪 / 逗 -> **玩**
- 普通聊天 -> **稳**

### 减少「门 / 灯 / 雨 / 茶」

- 候选打分：最近 12 轮里同一种意象出现 ≥3 次，未问它就扣分
- 选中句仍带旧意象 -> `_break_motif` 换一句
- `pick_motif` 增加 `avoid`：最近回复里太密的意象不再主动塞进 prompt

### 复读变体

`换个问法` 不再固定，按 mode/问题轮换：

- 稳：`换个问法。` / `这个问题先放着。` / `你想听真话？`
- 冷：`……说。` / `别绕。` / `我不接这个。`
- 玩：`你才。` / `行啊，你继续。` / `哼，想得美。`
- 软：`……我在听。` / `你慢点说。` / `嗯，你说。`
- 累：`……让我缓缓。` / `不想说。` / `先放一会儿。`

### 实测

```text
今天怎么样？      -> 稳 还行。你呢？
用一句话说晚安。  -> 累 睡吧。
我今天有点累。    -> 累 ……我有点累。
抱抱。            -> 软 ……我在听。
猪！              -> 玩 哼，想得美。
你会给我一杯茶吗？-> 软 会。糖在旁边，你自己加吧。
你是谁？          -> 我是岸。
```

## D. 1 分钟一条

web service 参数：

```text
--wander-idle 1
--wander-interval 1
--wander-daily 60
--wander-threshold 0.35
--initiative-gap-min 1
--initiative-daily 60
```

实测：

- 21:36:20 念头：`门缝里漏进来的光，又斜了一寸。`
- 21:37:13 念头：`他们总在夜里打开灯，可从来没人看过自己影子。`
- 间隔 53 秒
- `/api/pending` 能取到：
  ```json
  {"kind":"thought","text":"他们总在夜里打开灯，可从来没人看过自己影子。","reason":"自己想到的"}
  ```

注意：23:00–08:00 是 `wander_quiet`，会停。  
要整夜刷，需要再改 quiet window。

## 当前

- 8099：4B，4K，健康
- 8765：岸，`model=true`
- RSS：约 2.76GB（wander 跑起来后）
- 显存：3.54GB
- 服务：active / enabled / linger
- 测试：175 项全过
