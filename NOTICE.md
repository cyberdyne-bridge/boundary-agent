# NOTICE

## 生成方式

本项目的代码、实验脚本与文档，由作者在架构与判断上主导，**借助 AI 辅助编写**
（DeepSeek V4 / Qwen 系列）。所有实验数字来自作者本机实测，不是模型生成的估计值。
AI 辅助部分如与事实不符，以代码与实测数据为准。

## 模型与第三方

本项目**不包含**任何模型权重，只通过 OpenAI 兼容接口调用使用者自行部署的模型。使用时请同时遵守：

| 组件 | 许可 / 说明 |
|---|---|
| Qwen2.5 / Qwen3 系列 | 阿里云通义千问，Apache-2.0（以官方为准） |
| DeepSeek 蒸馏模型 | DeepSeek 模型许可（以官方为准） |
| llama.cpp / llama-server | MIT |
| 本地运行时 | Python ≥ 3.10，**仅标准库**，无第三方依赖 |

本项目与上述任何一方**无关联、未获背书**。

## 研究脉络来源

设计时参考过的公开工作（仅思想来源，未复制代码）：

- 记忆与长程一致性：Generative Agents、MemGPT、Mem0、Zep、Reflexion
- 自我与意识模型：Global Workspace Theory、Damasio 的情感/内稳态框架、Predictive Processing
- 安全与对齐：agentic misalignment、corrigibility、situational awareness
- 评测：ARC-AGI、GAIA、OSWorld、LongMemEval

详见 `docs/research-A-memory.md` / `research-B-self.md` / `research-C-safety.md` / `research-D-eval.md`。

## 发布时剥离的内容

为保护隐私，公开版**不包含**：全部会话原文（`docs/session-*`）、52 轮长对话实录、
个人日记生成器、实验现场数据库（`boundary-lab-*`、`boundary-live`）。
评测报告里保留了少量匿名化片段（如「小岛」「热茶」）作为实验证据，不含身份信息。
`docs/` 只保留方法报告（`.md`），原始评测转储（`.json`）未发布。
