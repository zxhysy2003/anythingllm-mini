# Agent 文档术语（Terminology）

本文档统一 `anythingllm-mini` V4 Agent 相关文档中的专业术语。中文用于解释概念；讨论
代码边界、字段、状态或测试时，优先使用下表的英文 **technical term**。

| English technical term | 中文说明 | 在本项目中的含义 |
| --- | --- | --- |
| `contract` | 合同 / 契约 | 模块间对输入、输出、字段语义、失败行为和兼容边界的明确约定。 |
| `clarification` | 澄清 | Agent 通过 `request_user_input` 请求用户补充信息的结构化交互。 |
| `interaction` | 交互结果 | 不属于 `artifact`、但会改变 Agent 控制流的 `ToolResult` 内容。 |
| `lifecycle` | 生命周期 | invocation 从开始、`needs_input`、continue 到完成的状态转换。 |
| `invocation` | 一次调用记录 | 持久化的完整 Agent run，由 `agent_invocations` 表表示。 |
| `step` | 步骤 | invocation 内一次 parser 或 tool 执行记录，由 `agent_steps` 表表示。 |
| `executor` | 执行器 | 执行 `react_text` 或 `native_tool_calling` Agent loop 的组件。 |
| `observation` | 观察结果 | 工具 `content` 或 parser 失败信息；它是下一轮 LLM 可见的文本。 |
| `artifact` | 结构化产物 | 不自动传给 LLM 的安全结构化结果，例如 `sources` 和 `outputs`。 |
| `replay` | 确定性回放 | 不调用 LLM 或真实工具，重新验证 parser、registry、artifact 与 metrics 边界。 |
| `fixture` | 测试样例文件 | 可提交、脱敏的本地 JSON replay 输入。 |
| `snapshot` | 快照 | 某一时刻保存的 sources、状态或 transcript，用于读回和一致性验证。 |
| `persistence` | 持久化 | 将 invocation、step、message 或 resume state 写入 SQLite。 |
| `claim` | 认领 | Agent 初始 run 或 continue 请求对同一 conversation 取得的持久化排他执行权。 |
| `lease` | 租约 | claim 的有限有效期；异常中断后允许其他请求接管。 |
| `fencing` | 围栏校验 | 最终写入时再次校验 claim ID，阻止过期请求覆盖新持有者。 |
| `metrics` | 指标 | 可计数、可汇总的运行证据，例如 step 数、工具调用数和 latency。 |

## 写法约定

- 第一次引入概念时使用“中文（`English term`）”，例如“工具结果合同（`ToolResult contract`）”。
- 后续同一段落或同一文档优先使用英文 technical term，字段名和状态名始终保持代码中的原样。
- 不把普通中文叙述机械替换为英语：例如“调用接口”不是必然的 `invocation`；只有对应
  `agent_invocations` 中一次 Agent run 时才使用 `invocation`。
