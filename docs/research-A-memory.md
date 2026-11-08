# A. 记忆：别人怎么做，岸能偷什么

> 一句话：别人的记忆系统是“给大模型加外脑”；岸是“给小模型加拐杖”。
> 前者追容量，后者追**可核对**。

## 1. 五个系统

### Generative Agents（Stanford, 2023）

- **记忆流**：所有观察写成自然语言，带时间戳。
- **检索**：recency（指数衰减）+ importance（LLM 打 1–10 分）+ relevance（向量相似度），取 top-k。
- **反思**：累计重要性过阈值，生成更高层洞察，写回记忆流。
- **规划**：目标分解、再规划；25 个 agent 在 Smallville 里出现 emergent social behavior。
- 链接：https://github.com/joonspk-research/generative_agents
- 论文：https://arxiv.org/abs/2304.03442

**偷**：importance + 反思层级 + 证据链。
**不偷**：25 agent 模拟、全程 GPT-4、向量库。

### MemGPT / Letta（2023–）

- 把 LLM 当 OS：**main context**（system / working context / FIFO）与 **external context**（recall / archival）。
- 通过 function call 自编辑记忆：`core_memory_append/replace`、`archival_memory_insert/search`、`conversation_search`。
- 内存压力警告、递归摘要、事件循环。
- 链接：https://github.com/letta-ai/letta
- 论文：https://arxiv.org/abs/2310.08560

**偷**：core / recall / archival 三层；core memory 常驻 prompt。
**不偷**：完整 function-calling 循环；1.5B 扛不住。

### Mem0（2024–）

- `add(messages)` 抽取候选事实；与已有记忆比较；决定 **ADD / UPDATE / DELETE / NOOP**。
- `search(query)` 检索；支持 user / session / agent 作用域；可选 graph memory。
- 链接：https://github.com/mem0ai/mem0

**偷**：显式更新操作、去重/合并、来源。
**不偷**：无校验的 LLM 抽取；1.5B 会编。

### Zep / Graphiti（2024–）

- 时序知识图谱：episode → entity → fact/edge；边带 `valid_at` / `invalid_at`。
- 新事实与旧事实冲突时，**旧边失效**，不是删掉；混合检索（向量 + BM25 + graph）。
- 链接：https://github.com/getzep/graphiti

**偷**：`valid_from / valid_to`、supersede、时间感知检索。
**不偷**：图数据库；SQLite 加两列就够。

### Reflexion（2023）

- Actor → Evaluator → Self-Reflection；把“这次为什么失败”写进 episodic memory，下次重试。
- 不改权重，只改上下文。
- 链接：https://github.com/noahshinn/reflexion
- 论文：https://arxiv.org/abs/2303.11366

**偷**：反思必须**可执行**、**可召回**。
**不偷**：让 1.5B 当 evaluator；它评不准。

### 附：MemoryBank（2023）

- 用 Ebbinghaus 遗忘曲线做记忆强度；用户画像 + 摘要。
- 链接：https://github.com/zhongwanjun/MemoryBank-SiliconFriend

**偷**：事实也会衰减；重要的要 pin。
**不偷**：把“猪猪/凉茶”这种高情绪事实忘掉。

## 2. 岸现在的位置

已有：

- `facts`：来源、冲突、count、FTS5、topic、引用
- `recall`：关键词 + 半衰期 + 情绪一致
- `reflections` / `motifs` / `promises` / `impressions`
- 硬路由 + 记忆卡 + 25 条探针

比多数 demo 强的地方：

- **来源**（user/agent）
- **冲突**（记得/没听说过）
- **计数**（×8）
- **可核对**（引用 [M]）

缺的：

- importance 写时评分
- valid time / supersede
- core memory（常驻）
- 反思层级 / derived_from
- 事实衰减 / pin
- 时间性评测

## 3. 最值得偷的三件

### ① importance + valid time

给 facts 加：

```
importance REAL
valid_from REAL
valid_to   REAL
derived_from TEXT
```

importance 用规则，不交给 1.5B：

- 明确“记住/别忘” +0.4
- 情绪事件 +0.2
- 重复出现 +0.2
- 实体（猪猪/凉茶/小岛）+0.1
- 用户来源 +0.1

检索分数改成：

```
0.35*FTS + 0.20*recency + 0.20*importance + 0.15*topic + 0.10*source
```

### ② core memory（常驻）

从 facts 里 pin 5–8 条，永远进 prompt：

```
用户叫它“大肥鱼”
用户说“猪猪”是逗它
用户说“不用继续努力”
它怕自己忘了
```

总长控制在 300 字以内。MemGPT 的 core memory，但用规则 pin，不用 function call。

### ③ 反思层级

现在 `_maybe_reflect` 是单层摘要。改成：

- 每 10 轮，或累计 importance > 3.0，生成 2–3 条 insight
- 每条带 `derived_from`（哪几轮）
- 存成 `topic=reflection` 的 fact
- 检索时和普通 facts 一起召回

这样“我为什么怕忘”不是一句空话，而是能追到第 108 轮。

## 4. 最小实验

1. 加 `importance / valid_from / valid_to / derived_from`
2. 规则 importance + 时间失效
3. core memory 5 条常驻
4. 每 10 轮生成 2 条带出处的 reflection
5. 评测：
   - 原 25 条
   - 加 5 条时间题（“以前喜欢红茶，现在呢？”）
   - 加 5 条 core 题（“我叫什么/什么不能叫”）
   - 看 answer accuracy、wrong-source、temporal accuracy、prompt chars

## 5. 一句话

> **Generative Agents 让记忆“像人”；MemGPT 让记忆“有层级”；Mem0 让记忆“可更新”；Zep 让记忆“有时间”；Reflexion 让记忆“能改错”。**
>
> 岸现在最缺的不是容量，是**时间、重要性和常驻核心**。
