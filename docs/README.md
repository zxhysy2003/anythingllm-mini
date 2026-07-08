# 文档目录说明

`anythingllm-mini/docs` 现在只保留最终版相关文档，不再维护 V0-V3 阶段性学习入口说明。
当前后端代码位于 `backend/`，文档中的当前代码路径以 `backend/app`、`backend/tests`
为准；历史 `learning-log/` 可能保留迁移前路径。

## 目录职责

| 目录 | 作用 | 适合写什么 |
|---|---|---|
| `stage-notes/` | 最终版主流程笔记 | Agent loop、workspace-scoped RAG、工具系统等已稳定流程 |
| `design/` | 设计与计划记录 | Agent 设计判断、方案取舍、后续可演进方向 |
| `tech-notes/` | 技术专题笔记 | Alembic、Chroma、目录结构、Pydantic、pytest 等可复用技术解释 |
| `learning-log/` | 个人学习日志 | 提交前复盘、某次问答、踩坑点、面试表达 |

## 写入规则

- 最终版运行流程写入 `stage-notes/`。
- 仍在讨论或带有明显取舍的设计判断写入 `design/`。
- 单个技术点的长期复习材料写入 `tech-notes/`。
- 某次对话、踩坑或提交复盘写入 `learning-log/`。

## 和外层 `comparison-notes/` 的边界

工作区外层的 `comparison-notes/` 用于记录 `anythingllm-mini` 与原版 AnythingLLM 的架构对比、差距分析和借鉴点。

如果主题是“mini 当前代码怎么工作”，放在本目录下。

如果主题是“AnythingLLM 原版怎么做，mini 为什么选择简化”，放在外层 `comparison-notes/`。
