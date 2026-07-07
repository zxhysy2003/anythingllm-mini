# Post-V4 Agent 学习路线

## 定位

本文档记录在 `anythingllm-mini` 已完成最小 V4 Agent Loop 后，继续对照原版
AnythingLLM 时仍然值得学习的 Agent 开发内容。

当前 mini 已实现：

- Workspace Conversation 下的 Agent API。
- `react_text` ReAct 文本执行器。
- DeepSeek/OpenAI-compatible `native_tool_calling` 执行器。
- 本地 `ToolRegistry`、`ToolContext`、`ToolResult`。
- `calculator` 和 `workspace_document_search` 两个低风险工具。
- `agent_invocations` / `agent_steps` 独立持久化。
- assistant message metrics 中的轻量 `agent_invocation_id` 和汇总指标。
- 一个用于替代重复 `curl` 的薄 Agent UI。

所以下面的内容不是“V4 还没完成”，而是 post-V4 能力线迭代。每一项都应该保持小切片、
可测试、可解释，不应直接移植 AnythingLLM 的完整 Agent 平台。

## 对照来源

本次扫描参考了原版 AnythingLLM 的这些 Agent 相关结构：

- `server/models/workspaceAgentInvocation.js`：Agent invocation 的独立实体。
- `server/utils/chats/agents.js`：普通 chat 检测 `@agent` 或 automatic mode 后切到
  Agent invocation。
- `server/endpoints/agentWebsocket.js`：通过 websocket 连接一次 invocation。
- `server/utils/agents/index.js`：`AgentHandler` 负责 provider、workspace、history、
  plugins、attachments 和启动 AIbitat。
- `server/utils/agents/aibitat/index.js`：事件驱动的 agent runtime、tool call 链、
  streaming / non-streaming 执行、tool call limit 和 tool result event。
- `server/utils/agents/defaults.js`：默认 skills、可配置 skills、imported plugins、
  agent flows、MCP tools 和 clarifying question tool 的装配。
- `server/utils/agents/aibitat/plugins/websocket.js`：introspection、tool approval、
  clarifying question、bail command 和用户反馈。
- `server/utils/agents/aibitat/utils/toolReranker.js`：工具数量变多后的智能 skill selection。
- `server/utils/agents/aibitat/plugins/summarize.js` 与
  `server/utils/agents/aibitat/utils/summarize.js`：长文档分块总结、进度提示、用户继续确认
  和 abort。
- `server/utils/agentFlows/executor.js`：可配置 flow 的顺序执行、变量替换和 direct output。

## 值得学习的能力切片

### 1. Agent 事件流和实时 introspection

状态：已按 SSE-format over POST 实现第一版事件 timeline；仍不做 token-by-token 文本流。

能力线：Observability and evaluation、API and UX contracts。

影响阶段：V4 Agent loop，外加跨阶段 UI/API 合同。

学习价值：

- 当前 mini 的 Agent API 是同步 HTTP，只有完成后才能看到完整 `steps`。
- AnythingLLM 会通过 websocket 把 status、tool approval、clarification、usage metrics
  等事件推给前端。
- 对 mini 来说，先学“事件模型”比先学完整 websocket runtime 更有价值。

建议边界：

- 已落地的只读事件流固定为：`agent_started`、`llm_started`、`llm_finished`、
  `tool_started`、`tool_finished`、`parse_error`、`max_steps_reached`、
  `agent_finished`、`agent_failed`。
- 事件内容来自当前 executor 的显式步骤，不暴露隐藏 chain-of-thought。
- 可以先选 SSE 或简单 polling，不必立刻做双向 websocket。
- 不引入后台 worker、断点恢复或多客户端订阅。

最小验收：

- 同一次 agent run 既能返回最终 HTTP 响应，也能产生有序事件。
- UI 可以显示运行中状态和工具 timeline。
- 失败 tool step 和 `max_steps_reached` 有清晰事件。

### 2. Tool policy、风险等级和用户批准

能力线：Safety and boundaries、Agent capabilities。

影响阶段：V4 tool registry，跨阶段安全边界。

学习价值：

- mini 现在只有 calculator 和 workspace 文档搜索，风险低。
- AnythingLLM 在文件、邮箱、日历、创建文件等工具上使用 approval / whitelist / auto-approve
  机制。
- 这能学习“工具不是只注册 schema，还要注册执行权限和失败行为”。

建议边界：

- 给工具增加静态元数据：`risk_level`、`requires_confirmation`、`side_effects`、
  `allowed_in_agent_modes`。
- 当前两个默认工具保持低风险、无需确认。
- 只有在新增有副作用工具前才启用确认流。
- 先做服务层 policy 判断，不做多用户权限系统。

最小验收：

- policy 禁用某工具时，Agent step 记录为可解释失败。
- 需要确认但没有确认 token 时，工具不执行。
- 工具失败不污染普通 conversation history。

### 3. Clarifying question 工具

能力线：Agent capabilities、API and UX contracts。

影响阶段：V4 agent loop 和 UI。

学习价值：

- 当前 mini 的 Agent 无法在执行中向用户补充提问，只能一次性回答或失败。
- AnythingLLM 把澄清问题建成 `request-user-input` 工具，并设置 per-turn 上限和超时。
- 这适合学习 Agent 和用户交互的边界：模型不能随便用自然语言“等用户回复”，而要通过结构化工具发起请求。

建议边界：

- 先支持单个结构化问题：`text` 或 `choice`。
- HTTP 同步 API 不适合长时间阻塞；可以返回 `needs_input` 状态，UI 再带 `invocation_id`
  继续。
- 每次 invocation 限制最多 1-3 个澄清问题。
- 超时或跳过时，Agent 必须继续或给出清晰失败，而不是无限等待。

最小验收：

- Agent 调用澄清工具后不会执行其他工具。
- 用户回答后能继续同一个 invocation。
- 跳过/超时会写入 step，并返回可解释 observation。

### 4. Tool selection 和工具 prompt 预算

能力线：Agent capabilities、Observability and evaluation。

影响阶段：V4 tool registry，后续多工具扩展。

学习价值：

- mini 当前工具很少，全部塞进 prompt 没问题。
- AnythingLLM 工具很多，因此提供 intelligent skill selection / reranker，避免工具 schema
  过多挤占上下文窗口。
- 这个能力的学习重点不是追求复杂 reranker，而是理解工具数量、schema 长度、模型选择质量之间的关系。

建议边界：

- 只有当默认工具超过 5 个时再引入。
- 第一版使用确定性 selector：按工具名、描述、examples 做简单匹配，最多注入 top N。
- 记录每次 run 注入了哪些工具，以及未注入工具数量。
- 暂不接 embedding reranker 或外部 rerank provider。

最小验收：

- selector 不影响当前两个默认工具。
- 当工具集变大时，prompt 中只出现被选中的工具。
- invocation metrics 能看到 `available_tool_count` 和 `selected_tool_count`。

### 5. 长文档总结工具

能力线：Document lifecycle、RAG quality、Agent capabilities。

影响阶段：V1/V2 文档能力和 V4 Agent 工具。

学习价值：

- 当前 `workspace_document_search` 是检索工具，只返回相关 chunk 和 sources。
- AnythingLLM 的 document summarizer 能列文档、读取完整内容，并在超出上下文时分块总结。
- 这是学习“长任务工具”的好切片：token 预算、分块、进度、abort、source citation 都会出现。

建议边界：

- 第一版只允许总结当前 workspace 内已经解析成功的文档。
- 工具输入使用 `document_id` 或精确 filename，不做跨 workspace 查找。
- 大文档分块时限制最大 chunk 数，并记录每个 chunk 的 summary step。
- 暂不做文件生成、附件注入或后台任务恢复。

最小验收：

- 列出可总结文档。
- 小文档直接返回摘要。
- 大文档按 chunk 总结，并在超限时返回清晰失败或部分结果。
- sources 保留到 assistant message 和 invocation detail。

### 6. Tool artifacts：sources、outputs、attachments 的统一返回

能力线：API and UX contracts、Observability and evaluation。

影响阶段：V4 tool result contract，跨阶段 UI。

学习价值：

- mini 当前 `ToolResult.data["sources"]` 主要服务文档检索。
- AnythingLLM 会缓冲 citations、outputs、attachments，并由 chat-history 插件一起持久化。
- mini 不必先支持真实二进制附件，但值得统一“工具除了文本 observation 还能产出结构化 artifact”的合同。

建议边界：

- 扩展 `ToolResult` 时优先支持 `sources` 和轻量 `outputs`。
- `outputs` 只保存 JSON metadata 或短文本，不保存任意本地路径。
- UI 读取 artifact 时通过 invocation detail，而不是把 artifact 塞进普通 chat history。

最小验收：

- 文档搜索工具仍能返回 sources。
- 新 artifact 字段不会破坏现有 API response。
- 不可信路径、隐藏文件路径、内部解析路径不出现在前端响应中。

### 7. Agent replay 和评估样例

能力线：Observability and evaluation、Developer experience and documentation。

影响阶段：V4 invocation persistence，跨阶段测试。

学习价值：

- mini 已经保存 `agent_invocations` / `agent_steps`，但还没有把它们用于 replay 或评估。
- Agent 最难稳定的是“同一个问题为什么这次调用了不同工具”。
- 把真实 invocation 导出成可读样例，可以学习回归测试、prompt 调整和工具 schema 调整。

建议边界：

- 先做只读导出：把 invocation、steps、sources、metrics 写成 JSON fixture。
- 支持一个 CLI 或测试 helper，不做在线自动评测平台。
- 对真实 LLM 输出只做 smoke/evidence 记录；确定性断言仍使用 fake LLM。

最小验收：

- 能从某个 `agent_invocation_id` 导出完整可脱敏记录。
- 能用 fixture 回放 parser / tool registry / metrics 汇总逻辑。
- 文档记录如何从一次失败 invocation 变成一个回归测试。

### 8. 轻量 Agent flow

能力线：Agent capabilities、Developer experience and documentation。

影响阶段：V4 Agent，与 post-V4 工作流学习。

学习价值：

- AnythingLLM 的 Agent Flows 是显式 workflow，不完全依赖模型自由决定每一步。
- 这能帮助区分两类系统：Agent 是模型决定行动，Flow 是系统按配置编排步骤。
- 对学习后端架构很有价值，因为它把“可预测流程”和“模型自由工具调用”分开。

建议边界：

- 第一版只做代码内配置或 JSON fixture，不做可视化 builder。
- 支持 `start`、`tool_call`、`llm_instruction` 三类节点即可。
- flow 可以作为一个工具被 Agent 调用，也可以作为独立 endpoint 执行，但不要混进当前最小 agent loop 主干。

最小验收：

- 一个固定 flow 能调用 calculator 或 document search。
- flow 结果能以 direct output 返回。
- flow 执行失败时记录失败节点和错误。

## 暂不建议学习或移植的内容

这些内容在 AnythingLLM 中有价值，但对当前 mini 的学习投入产出比不高：

- 完整 AIbitat 多 agent / multi-channel runtime。
- MCP server 兼容层和 imported plugin marketplace。
- 邮箱、日历、浏览器、真实文件系统写入等高风险外部工具生态。
- model router、多 provider fallback 和每轮动态路由。
- 可视化 Agent Builder / Flow Builder。
- 后台任务、定时任务、长任务恢复和多用户审计系统。
- 自动把 workspace 切到 agent automatic mode。

原因不是这些能力不重要，而是它们会把学习重点从当前 mini 的可解释 Agent Loop 转移到
产品平台工程。mini 更适合先把 tool policy、事件流、artifact contract、replay 这些
边界打稳。

## 推荐优先级

### P1：先巩固当前 Agent 的可观测性和安全边界

1. Agent 事件流和 UI timeline。
2. Tool policy、风险等级和用户批准。
3. Tool artifacts 统一合同。
4. Agent replay / fixture 导出。

这些任务都建立在当前已实现的 invocation、steps、ToolRegistry 和 UI 之上，学习价值高，
实现边界也清楚。

### P2：再扩展 Agent 的交互能力

1. Clarifying question 工具。
2. 长文档总结工具。
3. Tool selection。

这些任务会引入更多状态和 UI/API 合同，应在 P1 稳定后再做。

### P3：最后学习工作流和平台化能力

1. 轻量 Agent flow。
2. 更复杂的 streaming / websocket。
3. 更丰富的工具生态。

这类能力可以作为后续专题，不应该倒逼当前最小 agent loop 变复杂。

## 下一步建议

如果只选一个最适合马上做的切片，建议从 Agent 事件流和 UI timeline 开始：

- 它不需要增加新工具。
- 它直接复用当前 `agent_invocations` / `agent_steps`。
- 它能让已有薄 UI 从“curl 替代品”升级成“Agent 调试界面”。
- 它会自然暴露后续 tool approval、clarifying question、artifact 展示需要怎样的 API
  合同。
