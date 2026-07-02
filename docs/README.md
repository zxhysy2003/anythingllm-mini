# 文档目录说明

本文档说明 `anythingllm-mini/docs` 下各类笔记的职责边界，避免同一内容在多个目录重复维护。

## 目录职责

| 目录 | 作用 | 适合写什么 | 不适合写什么 |
|---|---|---|---|
| `stage-notes/` | 阶段主流程笔记 | V0-V4 每个阶段的最终流程、核心模块、代码导读 | 临时 TODO、未定设计方案、个人问答日记 |
| `design/` | 设计与计划记录 | V4 设计笔记、实现步骤、待处理问题、方案取舍、状态跟踪 | 已稳定的阶段教程、单个技术概念百科 |
| `tech-notes/` | 技术专题笔记 | Alembic、Chroma、Pydantic、pytest 等可复用技术解释 | 某一次提交的个人复盘 |
| `learning-log/` | 个人学习日志 | 提交前复盘、某次问答、踩坑点、面试表达 | 项目设计权威文档、长期维护的阶段主流程 |

## 写入规则

### `stage-notes/`

用于记录一个阶段已经相对稳定的主流程。比如：

- `01-v0-chat-flow.md`
- `02-document-upload-and-parse.md`
- `03-rag-main-flow.md`
- `04-workspace-and-conversation.md`
- `05-agent-loop-and-tools.md`

这里的笔记应该回答：

- 这个阶段的目标是什么？
- 请求或数据从哪里进入、经过哪些模块、最后到哪里？
- 核心文件分别负责什么？
- 当前实现和阶段边界是什么？

### `design/`

用于记录实现前和实现中的设计判断。比如：

- 某阶段计划做什么。
- 为什么做这个功能。
- 哪些能力暂时不做。
- 已发现的问题和处理状态。
- Step 1 / Step 2 / Step 3 这类实现计划。

当功能稳定后，可以把最终流程整理进 `stage-notes/`，但不要把整份设计笔记复制过去。

### `tech-notes/`

用于记录单个技术点，方便以后复习。比如：

- Alembic 是什么，在本项目哪里用。
- Chroma 如何保存 chunk 和 metadata。
- Pydantic schema 和 SQLModel model 的边界。

这类笔记不要求跟某一次提交绑定，而是偏长期复用。

### `learning-log/`

用于记录学习过程本身。比如：

- 用户问过的一个具体问题。
- 当时的困惑是什么。
- 回答要点是什么。
- 以后复习或面试时可以怎么表达。

这里可以引用代码和设计文档，但不要把它当成项目设计的唯一来源。

## 和外层 `comparison-notes/` 的边界

工作区外层的 `comparison-notes/` 用于记录 `anythingllm-mini` 与原版 AnythingLLM 的架构对比、差距分析和借鉴点。

如果主题是“mini 当前代码怎么工作”，放在 `docs/stage-notes/`、`docs/design/` 或 `docs/tech-notes/`。

如果主题是“AnythingLLM 原版怎么做，mini 为什么选择简化”，放在外层 `comparison-notes/`。

## 已废弃的旧目录

旧目录已经合并进 `docs/`：

- `notes/` -> `docs/stage-notes/`
- `issues/` -> `docs/design/`
- `tech-notes/` -> `docs/tech-notes/`

后续不要再往旧目录新增文档。
