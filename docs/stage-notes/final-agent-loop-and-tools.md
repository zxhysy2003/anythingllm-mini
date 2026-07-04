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
  -> app/api/agents.py
  -> AgentService.run_in_conversation()
  -> WorkspaceService.prepare_workspace_conversation_context()
  -> AgentLoop.run()
  -> ReAct parser
  -> ToolRegistry.run()
  -> calculator 或 workspace_document_search
  -> 保存 user message 和 assistant message
```

主要文件：

```text
app/core/agent_loop.py          -> Agent loop、ReAct parser、agent prompt builder
app/tools/registry.py           -> ToolContext、ToolResult、ToolRegistry
app/tools/calculator.py         -> 安全计算器工具
app/tools/document_tools.py     -> Workspace 文档搜索工具
app/services/agent_service.py   -> Workspace 边界、历史、执行和持久化编排
app/api/agents.py               -> Workspace Conversation Agent API
app/api/schemas/agents.py       -> Agent request/response schema
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

## Tool System

最终版定义了统一工具接口：

```text
ToolContext
- workspace_id
- conversation_id

ToolResult
- ok
- content
- data
- error

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
  "data": {
    "result": 7
  },
  "error": null
}
```

## Workspace Document Search Tool

`workspace_document_search` 把 workspace-scoped RAG 检索包装成工具。

关键边界：

- `workspace_id` 只能来自 `ToolContext.workspace_id`。
- LLM 不能通过工具输入覆盖 workspace 范围。
- 工具只做检索，不调用 `build_context_prompt()`，不拼 system prompt，不调用 LLM。
- 返回 sources 数据，但不返回 `upload_path` 或 `parsed_path`。

调用路径：

```text
AgentService
-> ToolContext(workspace_id=context.workspace.id, conversation_id=context.conversation.id)
-> AgentLoop
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
2. 构造 `ToolContext`，把当前 `workspace_id` 和 `conversation_id` 注入 Agent Loop。
3. 根据 Workspace 的 `system_prompt`、`chat_mode`、`temperature` 和历史运行
   `AgentLoop`。
4. 从工具步骤中提取 `workspace_document_search` 返回的 sources。
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

中间工具调用不保存成单独 message，避免污染普通聊天历史。工具步骤先保存在 assistant
message 的 `metrics["agent_steps"]` 中：

```json
{
  "agent_mode": "react_text",
  "agent_steps": [
    {
      "step_index": 1,
      "llm_output": "Action: calculator\nAction Input: {\"expression\": \"1 + 2\"}",
      "action": "calculator",
      "action_input": {
        "expression": "1 + 2"
      },
      "observation": "3",
      "ok": true,
      "error": null,
      "tool_result": {
        "ok": true,
        "content": "3",
        "data": {
          "result": 3
        },
        "error": null
      }
    }
  ],
  "tool_call_count": 1,
  "failed_step_count": 0,
  "max_steps_reached": false
}
```

Assistant message 同时保存：

- `content`：最终答案。
- `sources`：工具检索出的 source 快照。
- `provider` 和 `model`。
- `metrics`：LLM 调用次数、step 数、工具调用数、失败 step 数、source 数、
  `max_steps_reached` 和总耗时。

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
  "max_steps": 5
}
```

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

- message 为空：请求校验或 AgentLoop 校验失败。
- `max_steps` 不在 `1..10`：请求校验或 AgentLoop 校验失败。
- LLM 输出格式错误：记录 failed step，并允许下一轮修正。
- 未知工具：`ToolRegistry.run()` 返回 `ToolResult(ok=False, error="unknown_tool")`。
- 工具输入非法：`ToolRegistry.run()` 返回
  `ToolResult(ok=False, error="invalid_tool_input")`。
- 工具内部异常：`ToolRegistry.run()` 返回
  `ToolResult(ok=False, error="tool_execution_failed")`。
- DB 保存失败：rollback，并返回 `WorkspacePersistenceError`。

工具失败属于 Agent 过程的一部分：只要 Agent 后续给出 `Final Answer`，HTTP 仍可以返回
200，并把失败 step 保存到 assistant message 的 metrics 中。

## Tests

测试覆盖：

- `tests/test_tools_registry.py`：工具注册、查找、列表、未知工具、输入校验和异常包装。
- `tests/test_calculator_tool.py`：安全计算器的支持表达式、拒绝危险表达式、除零和大数边界。
- `tests/test_document_tools.py`：Workspace 文档搜索工具的 workspace 约束、参数透传、
  sources 返回和无结果行为。
- `tests/test_agent_loop.py`：ReAct parser、AgentLoop、parse error、unknown tool、
  invalid input、`max_steps` 和 prompt builder。
- `tests/test_agent_service.py`：Workspace context 注入、calculator、document search、
  sources 提取、消息持久化、失败 step 持久化、标题更新和 rollback。
- `tests/test_agents_api.py`：Agent endpoint、messages endpoint 读回、失败工具步骤持久化、
  max_steps 请求校验和普通 workspace chat 回归。

常用验证命令：

```bash
conda run -n anythingllm-mini pytest -q
conda run -n anythingllm-mini ruff check app tests
conda run -n anythingllm-mini black --check app tests
git diff --check
```

## Boundary

当前只实现同步、最小、可测试的 Agent 闭环。

暂不实现：

- WebSocket、SSE 或 streaming Agent。
- provider-native tool calling。
- Agent UI。
- 后台任务、定时任务和长任务恢复。
- 多用户权限、审计和工具授权。
- 外部浏览器、CLI、文件系统、邮件、日历等高风险工具。
- 独立 `agent_invocations` / `agent_steps` 表。

当前 `metrics["agent_steps"]` 适合作为学习版调试快照。后续如果要做 UI 展示、失败排查、
统计分析、重放或长任务恢复，再升级为独立表会更合适。
