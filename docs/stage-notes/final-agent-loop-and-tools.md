# 最终版 Agent Loop 与工具系统

当前代码在 Workspace 和 Conversation 边界内运行一个最小、可解释、可测试的 Agent
闭环。V4 建立了 executor、tool registry 和 invocation/step 持久化基线；此后又沿能力线补上
SSE 事件 timeline、backend-first tool policy、统一 tool artifacts 和确定性 replay，但没有把
它扩张成通用 Agent 平台：

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
  -> 保存 user/assistant messages、agent invocation 和 ordered steps
```

`POST .../agent/stream` 复用同一条业务链路，只在 executor/service 的显式边界发出有序事件，
再由 API 层编码为 SSE；它不是另一套 Agent 实现。

主要文件：

```text
backend/app/core/agent_loop.py          -> Agent loop、ReAct parser、agent prompt builder
backend/app/tools/registry.py           -> ToolContext、ToolResult、ToolRegistry
backend/app/tools/artifacts.py          -> ToolArtifacts、source/output 合同和路径安全限制
backend/app/tools/calculator.py         -> 安全计算器工具
backend/app/tools/document_tools.py     -> Workspace 文档搜索工具
backend/app/services/agent_service.py   -> Workspace 边界、历史、执行和持久化编排
backend/app/api/agents.py               -> Workspace Conversation Agent API
backend/app/api/schemas/agents.py       -> Agent request/response schema
backend/app/core/agent_events.py        -> AgentEvent、事件类型和 emitter protocol
backend/app/core/agent_executor.py      -> AgentExecutor protocol、共享结果类型和 step 上限
backend/app/core/agent_modes.py         -> 当前可运行 agent mode 常量
backend/app/core/native_tool_calling.py -> DeepSeek provider-native tool calling executor
backend/app/core/llm.py                 -> DeepSeek text chat 和 native tool_calls adapter
backend/app/models/agent.py             -> agent_invocations / agent_steps 持久化模型
backend/app/services/agent_replay_service.py -> invocation export 和确定性 replay 检查
backend/app/maintenance/export_agent_replay.py / replay_agent_fixture.py -> replay CLI 入口
```

## ReAct Text Protocol

默认 `react_text` 模式使用文本版 ReAct 协议，不依赖 provider-native tool calling。

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
- agent_mode
- approved_tool_call_ids

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
- risk_level
- side_effects
- requires_confirmation
- allowed_in_agent_modes
- run(...)
```

`ToolRegistry` 负责：

- 注册工具。
- 防止重复工具名静默覆盖。
- 列出工具描述、policy metadata 和 Pydantic JSON schema。
- 为 ReAct prompt 提供工具描述，也为 native mode 生成 OpenAI-compatible tools schema。
- 统一处理未知工具、输入校验失败和工具运行异常。
- 在真实 `tool.run()` 前统一执行 agent mode 和 confirmation policy gate。

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

## Tool Policy

当前第一版 tool policy 是 backend-first、无持久化 pending state 的执行前检查。

`ToolRegistry.run()` 的顺序固定为：

```text
查找工具
-> Pydantic 输入校验
-> agent mode / confirmation policy
-> tool.run()
-> ToolResult
```

工具通过静态 metadata 声明：

- `risk_level`：`low`、`medium` 或 `high`。
- `side_effects`：是否可能产生外部副作用。
- `requires_confirmation`：是否必须携带匹配的批准 ID。
- `allowed_in_agent_modes`：是否限制只能由某些 executor mode 调用。

需要确认的工具在未批准时不会执行，而是返回
`ToolResult(error="tool_confirmation_required")`。其中 `error_details.approval_id` 由工具名、
规范化输入、workspace、conversation 和 agent mode 确定性生成。调用方可以在新的 Agent 请求中
通过 `approved_tool_call_ids` 回传这个 ID，registry 再次校验相同动作后才放行。

这个边界目前是 stateless retry，不是完整的暂停/恢复工作流：没有 pending approval 表、过期
时间、用户身份绑定或前端确认按钮。默认 `calculator` 和 `workspace_document_search` 都是低风险、
无副作用工具，因此真实确认路径主要由测试工具刻画。

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
   `chat_mode`、`temperature`、历史和 `approved_tool_call_ids`。
4. 从工具步骤的 `artifacts.sources` 中提取文档引用。
5. 统计 agent metrics。
6. 在非流式和流式调用中发出相同的业务事件。
7. 用一次数据库 transaction 保存最终 user/assistant messages、invocation 和 ordered steps。

`query` 模式下，Agent system prompt 会额外提示模型：如果问题可能依赖 Workspace
文档，应先使用 `workspace_document_search`。

Agent 不会像 Workspace chat 一样在运行前自动调用 RAG。是否搜索文档仍由模型通过工具调用
决定；`query` mode 当前是 prompt 指令，不是硬编码的 doc-only executor。

## Agent Event Stream

当前流式接口传的是 Agent 运行事件，不是 assistant token：

```text
POST /workspaces/{workspace_id}/conversations/{conversation_id}/agent/stream
```

已实现的事件类型：

```text
agent_started
llm_started
llm_finished
tool_started
tool_finished
parse_error
max_steps_reached
agent_finished
agent_failed
```

executor 和 service 只依赖可选的 `AgentEventEmitter` protocol，不直接依赖 FastAPI 或 SSE。
API 层用 `asyncio.Queue[AgentEvent | None]` 解耦 producer 和 HTTP consumer：producer 运行
Agent 并 `put()` 事件，consumer 按 sequence 取出事件并编码成：

```text
event: tool_finished
data: {"sequence": 5, "type": "tool_finished", "payload": {...}}
```

`None` 是内部结束 sentinel，不会作为业务事件发送。成功运行最后一个事件是
`agent_finished`，其 payload 与非流式 endpoint 的完整结果合同一致；运行异常通过
`agent_failed` 表达。当前客户端断开后的主动取消、断点恢复和多客户端订阅仍未实现。

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
  "llm_call_count": 2,
  "step_count": 1,
  "tool_call_count": 1,
  "failed_step_count": 0,
  "source_count": 0,
  "max_steps_reached": false,
  "total_latency_ms": 123
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

## Agent Replay

当前 replay 把已保存的 invocation/steps 导出为严格 JSON fixture，再用确定性检查验证：

- `react_text` parser 是否仍产生相同 action、input 或 parser error。
- 当前 registry 是否仍能找到工具并维持相同输入校验边界；policy 结果仅作历史证据。
- step、artifacts、sources、status 和 metrics 是否仍能相互推导。

Replay 不调用真实 LLM，也不执行工具，因此它是工程回归，不是回答质量评测或线上运行重放。
真实导出可能包含 user message、LLM 文本和 source 内容，不能未经检查直接提交。具体命令和 fixture
边界见 `docs/tech-notes/agent-replay.md`。

## API

非流式执行接口：

```http
POST /workspaces/{workspace_id}/conversations/{conversation_id}/agent
```

请求体：

```json
{
  "message": "请计算 1 + 2 * 3",
  "max_steps": 5,
  "agent_mode": "native_tool_calling",
  "approved_tool_call_ids": []
}
```

`agent_mode` 可选，默认是 `react_text`；可选值为 `react_text` 和
`native_tool_calling`。响应体不直接增加 `agent_mode`，实际执行模式通过 invocation
detail 读取。

`approved_tool_call_ids` 可选，默认空列表，只用于批准 registry 已经返回过的确定性 tool call ID；
它不会绕过工具输入校验，也不会批准输入或上下文不同的另一个动作。

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

SSE 事件接口使用同一个请求 schema：

```http
POST /workspaces/{workspace_id}/conversations/{conversation_id}/agent/stream
```

响应类型是 `text/event-stream`。它返回有序运行事件，并在 `agent_finished` 中给出完整最终结果，
而不是在 HTTP response body 中直接返回 `WorkspaceAgentResponse` JSON。

Invocation 查询接口：

```http
GET /workspaces/{workspace_id}/conversations/{conversation_id}/agent-invocations/{invocation_id}
```

该接口同时用 workspace、conversation 和 invocation ID 限定读取范围，并按 `step_index` 返回
完整 ordered steps。

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
- 工具不允许在当前 agent mode 使用：返回
  `ToolResult(ok=False, error="tool_blocked_by_policy")`，不执行工具。
- 工具需要确认但没有匹配 approval ID：返回
  `ToolResult(ok=False, error="tool_confirmation_required")`，并在 `error_details` 给出 approval ID。
- DB 保存失败：rollback，并返回 `WorkspacePersistenceError`。
- SSE 运行中异常：发送 `agent_failed` 后结束 stream；它不是普通 JSON error response。

工具失败属于 Agent 过程的一部分：只要 Agent 后续给出 `Final Answer`，HTTP 仍可以返回
200，并把失败 step 保存到独立 `agent_steps` 表中。

## Tests

测试覆盖：

- `backend/tests/test_tools_registry.py`：工具注册、schema、未知工具、输入校验、异常包装、
  agent mode gate 和 confirmation approval。
- `backend/tests/test_tool_artifacts.py`：artifact 默认值、JSON/数量/长度约束和路径安全边界。
- `backend/tests/test_calculator_tool.py`：安全计算器的支持表达式、拒绝危险表达式、除零和大数边界。
- `backend/tests/test_document_tools.py`：Workspace 文档搜索工具的 workspace 约束、参数透传、
  sources 返回和无结果行为。
- `backend/tests/test_agent_loop.py`：ReAct parser、`ReactTextAgentExecutor`、parse error、
  unknown tool、invalid input、`max_steps`、`agent_mode` 和 prompt builder。
- `backend/tests/test_native_tool_calling.py`：DeepSeek native tool calling executor、tool_calls、
  tool message 回传、失败 step、多 tool call 和 `max_steps`。
- `backend/tests/test_agent_service.py`：Workspace context 注入、calculator、document search、
  sources 提取、executor mode 选择、事件、approval、消息/invocation/step 持久化和 rollback。
- `backend/tests/test_agents_api.py`：Agent endpoint、messages endpoint 读回、失败工具步骤持久化、
  SSE 事件、approval、invocation scope、`agent_mode` 请求校验、native mode 和普通 chat 回归。
- `backend/tests/test_agent_replay_service.py`：fixture parser/registry/artifact/metrics 回归、导出脱敏和
  不执行真实工具的边界。
- `backend/tests/test_agent_replay_cli.py`：export/replay 命令参数、overwrite 规则和 exit code。

### Suggested Tests To Read

- `backend/tests/test_agent_service.py::test_agent_service_returns_final_answer_and_saves_exchange`
  - 主 happy path，串起最终消息、invocation、ordered steps 和 metrics。
- `backend/tests/test_tools_registry.py::test_registry_blocks_confirmation_required_tool_without_approval`
  - policy gate 的关键安全边界：未批准时真实工具不能执行。
- `backend/tests/test_agents_api.py::test_agent_stream_endpoint_streams_agent_events`
  - SSE 格式、事件顺序和 `agent_finished` 最终 payload。
- `backend/tests/test_agent_replay_service.py::test_synthetic_agent_replay_fixtures_pass`
  - 理解已保存运行如何变成不调用 LLM、不执行工具的确定性回归样例。

常用验证命令：

```bash
cd backend
conda run -n anythingllm-mini pytest -q
conda run -n anythingllm-mini ruff check app tests alembic
conda run -n anythingllm-mini black --check app tests alembic
git diff --check
```

## Boundary

当前实现保持最小、可测试的 Agent 闭环。普通 Agent endpoint 同步返回完整结果；
`/agent/stream` 额外支持 SSE-format 运行事件 timeline，实时推送显式 agent、LLM、tool、
parse error、max steps 和最终状态。两条 API 共享 executor、tool policy、持久化和结果合同。

暂不实现：

- WebSocket 或双向实时 Agent。
- Token-by-token answer streaming。
- 客户端断开后的主动取消、断点恢复和多客户端订阅。
- DeepSeek beta strict mode。
- 后台任务、定时任务和长任务恢复。
- 多用户认证、RBAC、审计日志和持久化 approval/resume 工作流。
- Agent 可调用的外部浏览器、shell、文件系统、邮件、日历等高风险工具。
- 二进制 attachments 和不受限的任意 tool outputs。
- 隐藏 chain-of-thought 暴露；事件只记录显式状态、tool result 和可持久化结果。

当前版本已经把新产生的 Agent 运行步骤迁到 `agent_invocations` / `agent_steps` 表。
旧 assistant message metrics 中如果已经存在历史 `agent_steps`，本阶段不做清洗或回填。

当前 executor 边界已经拆出：`AgentService` 依赖 `AgentExecutor` protocol，默认运行
`ReactTextAgentExecutor`，也可以通过 `agent_mode="native_tool_calling"` 运行
`DeepSeekNativeToolCallingExecutor`。两种模式共享 `ToolRegistry`、`ToolContext`、
`AgentStep`、tool policy、artifact contract、事件、invocation/step 持久化和 metrics 汇总。
