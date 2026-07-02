# V4 Agent Loop 与工具系统设计笔记

## 阶段定位

V4 的目标不是完整移植 AnythingLLM Agent，而是在 V0-V3 的基础上补一个最小、
可解释、可测试的 Agent 闭环。

前面几个阶段已经完成了 Agent 需要的底座：

- V0：可以调用 LLM。
- V1：可以上传和解析文档。
- V2：可以把文档分块、向量化并检索。
- V3：有 Workspace、Conversation、聊天历史和 workspace-scoped RAG。

因此 V4 应该增加的是：

```text
用户消息
-> Agent 判断下一步
-> 可选调用工具
-> 读取工具结果
-> 继续判断或生成最终回答
-> 保存最终回答和中间步骤
```

这一步的学习重点是理解 Agent 和普通 Chat/RAG 的差别：普通 RAG 是系统固定
先检索再回答；Agent 是模型可以基于任务决定是否使用工具、何时使用工具，以及如何
根据工具结果继续推理。

## 为什么不是直接做完整 Agent 平台

AnythingLLM 的 Agent 能力包含更多产品化部分，例如 WebSocket 流式执行、后台任务、
权限、调度、复杂工具生态和 UI 交互。mini 当前的学习目标更窄：只实现一个清晰的
执行闭环。

V4 暂不实现：

- 多用户权限和审计。
- 后台 worker、定时任务和长任务调度。
- WebSocket 或 SSE agent streaming。
- 可视化 workflow builder。
- 外部工具市场、浏览器自动化、系统命令执行。
- 多模型 provider 自动路由。

这些能力有价值，但会把学习重点从 Agent 核心执行模型分散到产品工程复杂度上。

## 要增加的能力

### 1. Agent Loop

新增一个核心执行循环：

```text
for step in range(max_steps):
    ask LLM what to do next
    if final answer:
        stop
    if tool call:
        validate input
        run tool
        append observation
```

为什么增加：

- 这是 Agent 区别于单轮 Chat 的核心。
- 可以学习 ReAct 风格的 thought/action/observation 流程。
- 可以把 RAG、计算器等能力变成模型可选择的动作。

好处：

- 用户问题不再只能走固定路径。
- 系统可以处理需要多步操作的问题。
- 每一步都可以测试和记录，便于 debug。

### 2. Tool 抽象

定义统一工具接口，建议最小字段为：

```text
name
description
input_schema
run(...)
```

为什么增加：

- Agent 不应该直接调用任意 Python 函数。
- 所有工具都需要统一描述、统一输入校验、统一错误处理。

好处：

- 新增工具时不需要改 Agent Loop 主流程。
- 工具能力和 Agent 执行逻辑解耦。
- 后续可以学习 provider-native tool calling 或 MCP 风格接口。

### 3. Tool Registry

新增一个本地工具注册表：

```text
ToolRegistry
-> register(tool)
-> get(name)
-> list_tools()
```

第一批工具建议只保留两个：

- `calculator`：用于学习确定性工具调用。
- `workspace_document_search`：包装已有 workspace-scoped RAG 检索。

为什么增加：

- Agent 需要知道当前允许使用哪些工具。
- 工具权限应该显式受控，而不是靠 prompt 暗示。

好处：

- 可以为不同 workspace 或不同 endpoint 配置不同工具集合。
- 测试时可以注入 fake registry。
- 后续加工具不破坏现有 agent loop。

### 4. Workspace 文档搜索工具

把 V2/V3 已有 RAG 检索包装成工具：

```text
workspace_document_search(question, workspace_id, top_k, similarity_threshold)
```

为什么增加：

- V3 chat 是固定先检索。
- V4 agent 应该能主动决定是否需要查文档。

好处：

- 能清楚看到 RAG Chat 和 Agentic RAG 的区别。
- Agent 可以先普通推理，再决定查文档；也可以查完后继续调用其他工具。
- 复用已有 Chroma、embedding、workspace 隔离和 source 结构。

注意：

- 工具必须始终使用当前 `workspace_id`，不能访问全局 `__global__` 文档。
- 工具结果不应该直接把全部文档正文塞进长期历史。
- 返回内容应包含必要 source 信息，方便最终答案引用。

### 5. Agent Step 记录

V3 已保存 user/assistant message。V4 需要记录中间步骤，例如：

```text
step_index
action
tool_name
tool_input
tool_output
status
latency_ms
error
```

设计选择有两种：

- 学习版：先保存在 assistant message 的 `metrics["agent_steps"]` 中。
- 更清晰版：新增 `agent_steps` 表，通过 `conversation_message_id` 关联最终 assistant
  message。

建议 V4 最小实现先用 `metrics["agent_steps"]`，避免一开始就引入迁移和额外表结构；
如果步骤查询、失败恢复或 UI 展示需求变多，再升级为独立表。

长期判断：

类比 AnythingLLM，Agent 调用更适合成为独立数据实体。AnythingLLM 中有
`workspace_agent_invocations` 表，记录一次 Agent invocation 的 `uuid`、`prompt`、
`workspace_id`、`user_id`、`thread_id`、`closed` 等信息；Agent 触发后会先创建
invocation，再由 WebSocket 挂接执行过程。

因此 mini 的长期方向不应只依赖 assistant message 的 `metrics` JSON。更合理的演进是：

```text
agent_invocations
- id
- workspace_id
- conversation_id
- user_message_id
- assistant_message_id
- input_message
- status
- provider
- model
- max_steps
- max_steps_reached
- started_at
- ended_at
- error

agent_steps
- id
- invocation_id
- step_index
- llm_output
- action
- action_input
- observation
- ok
- error
- latency_ms
- created_at
```

取舍：

- `metrics["agent_steps"]` 适合作为学习版和 summary 快照，开发快、无迁移成本。
- 独立表适合长期产品化，便于查询、调试、重放、统计工具调用次数和失败率，也避免
  `ConversationMessage.metrics` 越来越大。
- V4 当前仍先使用 metrics；当进入 UI 展示、失败排查、统计分析或长任务恢复时，再升级
  为 `agent_invocations` + `agent_steps`。

为什么增加：

- Agent 的价值在过程，不能只保存最终答案。
- 中间工具调用是排查错误和理解推理路径的关键。

好处：

- 可以回答“模型为什么调用了这个工具”。
- 可以测试工具调用顺序和失败边界。
- 可以避免把工具 observation 混进普通 conversation history。

### 6. Workspace Agent API

新增 endpoint 应挂在 Workspace Conversation 下：

```text
POST /workspaces/{workspace_id}/conversations/{conversation_id}/agent
```

请求体建议：

```json
{
  "message": "请根据文档计算总数",
  "max_steps": 5
}
```

响应体建议：

```json
{
  "conversation_id": "...",
  "message": "...",
  "answer": "...",
  "steps": [],
  "sources": [],
  "provider": "deepseek",
  "model": "deepseek-v4-flash",
  "metrics": {}
}
```

为什么增加：

- V4 应继承 V3 的 Workspace 和 Conversation 边界。
- 旧 `/chat`、`/documents/upload`、`/rag/query` 是学习用 legacy endpoint，不应继续承载
  新 Agent 能力。

好处：

- Agent 自动复用 workspace 配置、文档范围和会话历史。
- 普通 chat 和 agent chat 可以并存，便于对比学习。
- API 边界清晰，不会混淆全局 RAG 和 workspace RAG。

## 推荐实现顺序

### Step 1：写设计和测试骨架

状态：当前文档。

先明确 V4 只做最小闭环，不动业务代码。

### Step 2：抽出 Workspace Chat 上下文准备逻辑

状态：已完成。

从 `WorkspaceService.chat_in_conversation()` 中抽出可复用 helper：

```text
load workspace
load conversation
load limited history
retrieve workspace chunks
build context prompt
decide query refusal
```

普通 chat 和 agent 都可以复用这部分，避免 V4 复制 V3 主流程。

### Step 3：实现 Tool 与 ToolRegistry

状态：已完成。

先实现纯 Python 抽象，不接 LLM：

```text
BaseTool
ToolResult
ToolRegistry
calculator
workspace_document_search
```

### Step 4：实现最小 Agent Loop

状态：已完成。

先做 ReAct 文本协议，不急着接 provider-native tool calling。

模型输出可以约定为：

```text
Action: calculator
Action Input: {"expression": "1 + 2"}
```

或：

```text
Final Answer: ...
```

### Step 5：接入 AgentService 和 API

新增：

```text
app/core/agent_loop.py
app/services/agent_service.py
app/api/agents.py
app/tools/registry.py
app/tools/calculator.py
app/tools/document_tools.py
```

并在 `app/main.py` 注册 agent router。

### Step 6：保存最终消息和中间步骤

最终仍保存一条 user message 和一条 assistant message。中间步骤先放入 assistant
message 的 metrics：

```json
{
  "agent_steps": [],
  "tool_call_count": 1,
  "max_steps_reached": false
}
```

## 验收标准

V4 最小闭环完成时，应满足：

- 可以通过 workspace conversation agent endpoint 发送消息。
- Agent 能在不需要工具时直接回答。
- Agent 能调用 calculator 并用结果回答。
- Agent 能调用 workspace document search，并只检索当前 workspace 的文档。
- `max_steps` 能阻止无限循环。
- 工具输入非法时返回可解释错误，并保存失败步骤。
- 普通 `/workspaces/{workspace_id}/conversations/{conversation_id}/chat` 行为不变。
- 旧 `/chat`、`/documents/upload`、`/rag/query` 仍保持 legacy 学习入口。
- 测试覆盖 agent loop、tool registry、calculator、document search 工具和 API 路由。

## 主要风险

### 1. Agent 输出解析不稳定

如果使用 ReAct 文本协议，模型可能输出格式不规范。

缓解：

- 写严格 parser。
- 格式错误作为一步 failed step 保存。
- 允许模型在下一步修正，超过 `max_steps` 后停止。

### 2. 工具 observation 污染聊天历史

工具结果如果直接保存为 assistant 自然语言消息，普通 chat 后续可能误把工具过程当成用户
对话内容。

缓解：

- `_load_history()` 继续只加载 `user` 和 `assistant`。
- 中间工具步骤放入 metrics 或独立表，不作为普通历史喂回 LLM。

### 3. Agent 绕过 workspace 边界

文档搜索工具如果没有强制传入当前 workspace，可能误读全局或其他 workspace 文档。

缓解：

- `workspace_id` 由 `AgentService` 注入，不允许 LLM 自己提供。
- 工具内部只调用 workspace-scoped `RAGService.retrieve()`。

### 4. V4 过早扩大范围

如果一开始就做 streaming、多 provider、复杂工具权限和后台任务，会拖慢核心学习。

缓解：

- V4 只做同步最小闭环。
- 先保证 agent loop、tool call、step persistence 可测试。

## 当前结论

V4 应增加的是一个 workspace-aware 的最小 Agent Loop，而不是完整 Agent 平台。

最小功能集合：

- Agent Loop。
- Tool 抽象。
- Tool Registry。
- Calculator 工具。
- Workspace 文档搜索工具。
- Agent step 记录。
- Workspace Conversation 下的 Agent API。

这样可以把 V0 的 LLM、V2 的 RAG、V3 的 Workspace/Conversation 串成一个真正的
Agent 执行闭环，同时保持学习项目的范围足够小、边界足够清楚。
