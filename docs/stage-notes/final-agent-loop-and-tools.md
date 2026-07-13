# 最终版 Agent Loop 与工具系统

当前最终版在 Workspace 和 Conversation 边界内运行一个最小、可解释、可测试的
Agent 闭环：

```text
用户消息
-> Agent 判断下一步
-> 可选调用工具
-> 读取工具结果
-> 继续判断或生成最终回答
-> 保存最终回答和中间步骤
```

核心学习点是：普通 Workspace Chat 是系统固定编排的流程；Agent 是模型在受控工具集合中
决定是否调用工具、调用哪个工具，以及拿到 observation 后是否继续行动。

## Main Flow

```text
POST /workspaces/{workspace_id}/conversations/{conversation_id}/agent
  -> backend/app/api/agents.py
  -> AgentService.run_in_conversation()
  -> WorkspaceService.prepare_workspace_conversation_context()
  -> AgentExecutor.run()
  -> ReactTextAgentExecutor.run() 或 DeepSeekNativeToolCallingExecutor.run()
  -> ReAct parser 或 DeepSeek tool_calls
  -> ToolRegistry.run()
  -> calculator 或 workspace_document_search
  -> 保存 user message 和 assistant message
```

主要文件：

```text
backend/app/core/agent_loop.py          -> Agent loop、ReAct parser、agent prompt builder
backend/app/tools/registry.py           -> ToolContext、ToolResult、ToolRegistry
backend/app/tools/calculator.py         -> 安全计算器工具
backend/app/tools/document_tools.py     -> Workspace 文档搜索工具
backend/app/services/agent_service.py   -> Workspace 边界、历史、执行和持久化编排
backend/app/api/agents.py               -> Workspace Conversation Agent API
backend/app/api/schemas/agents.py       -> Agent request/response schema
backend/app/core/agent_executor.py      -> AgentExecutor protocol、共享结果类型和 step 上限
backend/app/core/agent_modes.py         -> 当前可运行 agent mode 常量
backend/app/core/native_tool_calling.py -> DeepSeek provider-native tool calling executor
backend/app/core/llm.py                 -> DeepSeek text chat 和 native tool_calls adapter
```

## ReAct Text Protocol

当前实现使用文本版 ReAct 协议，不依赖 provider-native tool calling。

模型每一轮只能输出两种格式之一：

```text
Action: calculator
Action Input: {"expression": "1 + 2"}
```

或：

```text
Final Answer: 结果是 3
```

解析规则：

- `Final Answer:` 表示结束，后续文本作为最终答案。
- `Action:` 必须配套 `Action Input:`。
- `Action Input` 必须是 JSON object。
- 同时出现 `Final Answer:` 和 `Action:` 视为格式错误。
- 格式错误会记录成 failed step，作为 observation 给下一轮 LLM 修正。
- 超过 `max_steps` 后固定返回 `Agent stopped after reaching the maximum number of steps.`。

这个协议比原生 function calling 更脆弱，但适合学习 Agent Loop 的核心结构：模型输出、
解析、工具调用、observation、下一轮模型输出。

## DeepSeek Native Tool Calling

`native_tool_calling` 模式使用 DeepSeek/OpenAI-compatible 工具调用格式：

- 请求向 DeepSeek 传入 `tools` 和 `tool_choice="auto"`。
- 模型需要工具时返回 `message.tool_calls`。
- 本地按顺序执行 tool calls，并把 `role="tool"`、`tool_call_id` 和工具结果追加回下一轮
  messages。
- 模型返回普通 content 时作为最终答案。

这个模式不使用 `Action:` / `Action Input:` 文本 parser。工具输入仍由本地 Pydantic
schema 和 `ToolRegistry.run()` 校验。DeepSeek beta strict mode 暂不启用，后续可以作为
独立学习点处理。

## Tool System

最终版定义了统一工具接口：

```text
ToolContext
- workspace_id
- conversation_id

ToolResult
- ok
- content
- artifacts
- error
- error_details

ToolArtifacts
- sources
- outputs

BaseTool
- name
- description
- input_model
- run(...)
```

`ToolRegistry` 负责：

- 注册工具。
- 防止重复工具名静默覆盖。
- 列出工具描述和 Pydantic JSON schema，供 agent prompt 使用。
- 统一处理未知工具、输入校验失败和工具运行异常。

`content` 是唯一进入下一轮 LLM 推理的 observation。`artifacts` 是给 service、API、
invocation persistence 和调试页消费的结构化产物；`error_details` 只保存 validation 和
tool policy 等失败诊断信息。

第一版 artifact contract 支持：

- `sources`：类型化文档引用，不允许内部文件路径。
- `outputs`：最多 20 个 JSON-safe 命名结果，总长度最多 8,000 字符。
- 不支持二进制 attachment，也不允许本地路径或 path 字段进入 outputs。

默认工具集合：

- `calculator`
- `workspace_document_search`

## Calculator Tool

`calculator` 用于学习确定性工具调用。

支持：

- 数字。
- 括号。
- `+ - * / // % **`。
- 一元 `+ -`。

实现上使用 Python `ast` 白名单解释表达式，不使用 `eval`。变量名、函数调用、属性访问、
列表、字典、比较表达式等都会失败；除零和过大的计算结果也会返回失败结果。

返回示例：

```json
{
  "ok": true,
  "content": "7",
  "artifacts": {
    "sources": [],
    "outputs": {
      "result": 7
    }
  },
  "error": null,
  "error_details": {}
}
```

## Workspace Document Search Tool

`workspace_document_search` 把 workspace-scoped RAG 检索包装成工具。

关键边界：

- `workspace_id` 只能来自 `ToolContext.workspace_id`。
- LLM 不能通过工具输入覆盖 workspace 范围。
- 工具只做检索，不调用 `build_context_prompt()`，不拼 system prompt，不调用 LLM。
- 通过 `artifacts.sources` 返回类型化 sources，但不返回 `upload_path` 或 `parsed_path`。

调用路径：

```text
AgentService
-> ToolContext(workspace_id=context.workspace.id, conversation_id=context.conversation.id)
-> ReactTextAgentExecutor 或 DeepSeekNativeToolCallingExecutor
-> ToolRegistry.run("workspace_document_search", ...)
-> WorkspaceDocumentSearchTool
-> RAGService.retrieve(..., workspace_id=context.workspace_id)
```

这让 Agentic RAG 和固定 RAG Chat 有了清楚区别：

- Workspace Chat：系统固定先检索，再决定是否调用 LLM。
- Workspace Agent：模型可以先回答，也可以主动调用文档搜索工具，再根据 observation 回答。

## AgentService

`AgentService.run_in_conversation()` 是 Agent 的 Workspace 边界。

它负责：

1. 复用 `WorkspaceService.prepare_workspace_conversation_context()` 加载 Workspace、
   Conversation、规范化后的 message 和历史。
2. 构造 `ToolContext`，把当前 `workspace_id` 和 `conversation_id` 注入 Agent executor。
3. 根据请求的 `agent_mode` 选择 executor，并传入 Workspace 的 `system_prompt`、
   `chat_mode`、`temperature` 和历史。
4. 从工具步骤的 `artifacts.sources` 中提取文档引用。
5. 统计 agent metrics。
6. 保存最终 user message 和 assistant message。

`query` 模式下，Agent system prompt 会额外提示模型：如果问题可能依赖 Workspace
文档，应先使用 `workspace_document_search`。

## Persistence

Agent 调用最终仍只保存两条 ConversationMessage：

```text
user message
assistant message
```

中间工具调用不保存成单独 message，避免污染普通聊天历史。工具步骤保存到
`agent_invocations` / `agent_steps` 表；assistant message 的 `metrics` 只保留
`agent_invocation_id` 和汇总指标：

```json
{
  "agent_mode": "react_text",
  "agent_invocation_id": "invocation-id",
  "max_steps": 5,
  "tool_call_count": 1,
  "failed_step_count": 0,
  "max_steps_reached": false
}
```

完整步骤通过 invocation 查询：

```text
GET /workspaces/{workspace_id}/conversations/{conversation_id}/agent-invocations/{invocation_id}
```

Assistant message 同时保存：

- `content`：最终答案。
- `sources`：工具检索出的 source 快照。
- `provider` 和 `model`。
- `metrics`：`agent_invocation_id`、LLM 调用次数、step 数、工具调用数、
  失败 step 数、source 数、`max_steps_reached` 和总耗时。

如果保存 user/assistant message 失败，服务会 rollback，并抛
`WorkspacePersistenceError("failed to save agent conversation messages")`。

## API

接口：

```http
POST /workspaces/{workspace_id}/conversations/{conversation_id}/agent
```

请求体：

```json
{
  "message": "请计算 1 + 2 * 3",
  "max_steps": 5,
  "agent_mode": "native_tool_calling"
}
```

`agent_mode` 可选，默认是 `react_text`；可选值为 `react_text` 和
`native_tool_calling`。响应体不直接增加 `agent_mode`，实际执行模式通过 invocation
detail 读取。

响应体：

```json
{
  "conversation_id": "...",
  "message": "请计算 1 + 2 * 3",
  "answer": "结果是 7",
  "steps": [],
  "sources": [],
  "provider": "deepseek",
  "model": "deepseek-v4-flash",
  "metrics": {
    "llm_call_count": 2,
    "step_count": 1,
    "tool_call_count": 1,
    "failed_step_count": 0,
    "source_count": 0,
    "max_steps_reached": false,
    "total_latency_ms": 123
  }
}
```

调用完成后，普通 messages endpoint 可以读到新增的 user/assistant 两条消息：

```http
GET /workspaces/{workspace_id}/conversations/{conversation_id}/messages
```

## Error Boundaries

Agent 的失败边界分层处理：

- message 为空：请求校验或 `ReactTextAgentExecutor` 校验失败。
- `max_steps` 不在 `1..10`：请求校验或 executor 校验失败。
- ReAct 文本输出格式错误：记录 failed step，并允许下一轮修正。
- native tool call arguments 不是合法 JSON object：记录 failed step，并把错误作为 tool
  message 回传给模型修正。
- 未知工具：`ToolRegistry.run()` 返回 `ToolResult(ok=False, error="unknown_tool")`。
- 工具输入非法：`ToolRegistry.run()` 返回
  `ToolResult(ok=False, error="invalid_tool_input")`。
- 工具内部异常：`ToolRegistry.run()` 返回
  `ToolResult(ok=False, error="tool_execution_failed")`。
- DB 保存失败：rollback，并返回 `WorkspacePersistenceError`。

工具失败属于 Agent 过程的一部分：只要 Agent 后续给出 `Final Answer`，HTTP 仍可以返回
200，并把失败 step 保存到独立 `agent_steps` 表中。

## Tests

测试覆盖：

- `backend/tests/test_tools_registry.py`：工具注册、查找、列表、未知工具、输入校验和异常包装。
- `backend/tests/test_tool_artifacts.py`：artifact 默认值、JSON/数量/长度约束和路径安全边界。
- `backend/tests/test_calculator_tool.py`：安全计算器的支持表达式、拒绝危险表达式、除零和大数边界。
- `backend/tests/test_document_tools.py`：Workspace 文档搜索工具的 workspace 约束、参数透传、
  sources 返回和无结果行为。
- `backend/tests/test_agent_loop.py`：ReAct parser、`ReactTextAgentExecutor`、parse error、
  unknown tool、invalid input、`max_steps`、`agent_mode` 和 prompt builder。
- `backend/tests/test_native_tool_calling.py`：DeepSeek native tool calling executor、tool_calls、
  tool message 回传、失败 step、多 tool call 和 `max_steps`。
- `backend/tests/test_agent_service.py`：Workspace context 注入、calculator、document search、
  sources 提取、executor mode 选择、消息持久化、失败 step 持久化、标题更新和 rollback。
- `backend/tests/test_agents_api.py`：Agent endpoint、messages endpoint 读回、失败工具步骤持久化、
  `agent_mode` 请求校验、native mode 和普通 workspace chat 回归。

常用验证命令：

```bash
cd backend
conda run -n anythingllm-mini pytest -q
conda run -n anythingllm-mini ruff check app tests alembic
conda run -n anythingllm-mini black --check app tests alembic
git diff --check
```

## Boundary

当前实现最小、可测试的 Agent 闭环。普通 Agent endpoint 仍同步返回完整结果；
`/agent/stream` 额外支持 SSE-format 运行事件 timeline，用于实时展示 agent/tool 状态。

暂不实现：

- WebSocket 或双向实时 Agent。
- Token-by-token answer streaming。
- DeepSeek beta strict mode。
- 后台任务、定时任务和长任务恢复。
- 多用户权限、审计和工具授权。
- 外部浏览器、CLI、文件系统、邮件、日历等高风险工具。
- WebSocket/SSE 事件流中的实时 step 推送。

当前版本已经把新产生的 Agent 运行步骤迁到 `agent_invocations` / `agent_steps` 表。
旧 assistant message metrics 中如果已经存在历史 `agent_steps`，本阶段不做清洗或回填。

当前 executor 边界已经拆出：`AgentService` 依赖 `AgentExecutor` protocol，默认运行
`ReactTextAgentExecutor`，也可以通过 `agent_mode="native_tool_calling"` 运行
`DeepSeekNativeToolCallingExecutor`。两种模式共享 `ToolRegistry`、`ToolContext`、
`AgentStep`、invocation/step 持久化和 metrics 汇总。
