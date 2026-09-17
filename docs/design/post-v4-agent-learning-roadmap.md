# Post-V4 Agent 学习路线

术语遵循 [Agent 文档术语（Terminology）](../tech-notes/agent-terminology.md)。本文中
“合同”统一写作 `contract`，“澄清”统一写作 `clarification`；代码字段、状态和 endpoint 保持原样。

## 定位

本文档记录在 `anythingllm-mini` 已完成最小 V4 Agent Loop 后，继续对照原版
AnythingLLM 时仍然值得学习的 Agent 开发内容。

当前 mini 已实现：

- Workspace Conversation 下的 Agent API。
- `react_text` ReAct 文本执行器。
- DeepSeek/OpenAI-compatible `native_tool_calling` 执行器。
- 本地 `ToolRegistry`、`ToolContext`、`ToolResult`。
- `calculator`、`workspace_document_search`、`workspace_document_summary` 和
  `request_user_input` 四个低风险工具。
- `agent_invocations` / `agent_steps` 独立 persistence。
- Agent conversation message metrics 中的轻量 `agent_invocation_id` 和汇总指标；完成时写在
  assistant message，pause 时写在已保存的 user message，供调试页恢复同一 invocation。
- SSE-format over POST 的 Agent 事件流。
- backend-first tool policy、静态风险元数据和 stateless approval id。
- 单次、结构化的 `request_user_input` clarification tool，以及同一 invocation 的 pause/continue。
- 同步、有界的 `workspace_document_summary`，包括 parsed text 安全读取、Map + Reduce、
  `tool_progress`、direct-document sources 和 partial result。
- 本地 replay fixture 导出和确定性 replay。
- 一个用于替代重复 `curl`、展示事件 timeline 和 invocation detail 的薄 Agent 调试页：
  `backend/app/web/agent_ui.html`。

所以下面的内容不是“V4 还没完成”，而是 post-V4 能力线迭代。每一项都应该保持小切片、
可测试、可解释，不应直接移植 AnythingLLM 的完整 Agent 平台。

## 本路线图的范围

本文档只规划 `backend/` 下的 Agent capabilities，以及配套的 backend API、persistence、tests 和 docs。

- Agent 运行过程的显示和人工调试继续使用 `backend/app/web/agent_ui.html`。
- `agent_ui.html` 是后端 Agent 的调试工具，不按产品前端标准扩展。
- 本路线图不包含 Vue 前端接入、页面组件拆分、交互样式或正式前端状态管理。
- 如果某个 backend capability 需要最小人工交互，例如 tool approval 或 answer clarification，只在
  `agent_ui.html` 中增加足以验证 backend contract 的薄交互。

## 当前进度总览

| 能力切片 | 当前状态 | 下一步判断 |
| --- | --- | --- |
| Agent 事件流和实时 introspection | 已实现第一版 | 保持 event contract 稳定，不继续扩展 WebSocket 或 token 流 |
| Tool policy、风险等级和用户批准 | 已实现第一版 | 等真实有副作用工具出现后，再补完整批准交互 |
| Clarifying question 工具 | 已实现第一版 | 保持单次 clarification 和同一 invocation continue 的 lifecycle contract |
| Tool selection 和工具 prompt 预算 | 延后触发 | 默认工具超过 5 个后再评估 |
| 长文档总结工具 | 已实现第一版 | 保持同步、有界的 Map + Reduce 和 partial contract |
| Tool artifacts 统一返回 | 已实现第一版 | 保持 artifact contract 稳定，后续工具复用 `sources` / `outputs` |
| Agent replay 和评估样例 | 已实现第一版 | 保持 fixture contract 稳定，以 synthetic fixtures 覆盖 regression 边界 |
| 轻量 Agent flow | 未实现 | P3，不进入当前最小 Agent loop 主干 |

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

影响阶段：V4 Agent loop，外加跨阶段 UI/API contract。

学习价值：

- 当前 mini 同时保留同步 Agent API 和只读事件流 API，便于对比最终 result contract 与运行中
  event contract。
- AnythingLLM 会通过 websocket 把 status、tool approval、clarification、usage metrics
  等事件推给调用端。
- mini 已经完成“事件模型”这一层，后续重点是保持事件顺序、失败语义和最终结果一致。

已实现边界：

- 已落地的只读事件流固定为：`agent_started`、`llm_started`、`llm_finished`、
  `tool_started`、`tool_progress`、`tool_finished`、`parse_error`、`max_steps_reached`、
  `agent_needs_input`、`agent_finished`、`agent_failed`。
- 事件内容来自当前 executor 的显式步骤，不暴露隐藏 chain-of-thought。
- `agent_ui.html` 可以显示运行中状态、工具 timeline、失败 step 和最终 invocation 结果。

剩余边界：

- 不做双向 websocket、token-by-token answer streaming、后台 worker、断点恢复或多客户端订阅。
- 新增后端能力时可以增加必要事件类型，但必须继续只暴露显式状态和结构化结果。

最小验收：

- [x] 同一次 agent run 能产生有序事件，并在 `agent_finished` 中返回完整最终结果。
- [x] `agent_ui.html` 可以显示运行中状态和工具 timeline。
- [x] 失败 tool step 和 `max_steps_reached` 有清晰事件。

### 2. Tool policy、风险等级和用户批准

状态：已实现第一版 backend-first tool policy。当前支持工具静态风险元数据、执行前
policy gate、stateless approval id 和可解释 failed step；仍不做真实高危工具、
pending approval persistence、多用户权限系统或完整 approval workflow。

能力线：Safety and boundaries、Agent capabilities。

影响阶段：V4 tool registry，跨阶段安全边界。

学习价值：

- mini 现在只有 calculator 和 workspace 文档搜索，风险低。
- AnythingLLM 在文件、邮箱、日历、创建文件等工具上使用 approval / whitelist / auto-approve
  机制。
- 这能学习“工具不是只注册 schema，还要注册执行权限和失败行为”。

已实现边界：

- 工具已经具有静态元数据：`risk_level`、`requires_confirmation`、`side_effects`、
  `allowed_in_agent_modes`。
- 当前四个默认工具保持低风险、无需确认。
- `ToolRegistry.run()` 在执行前完成 agent mode 和 confirmation policy 判断。
- 缺少批准时返回可解释 `ToolResult`，并由当前 Agent step/invocation `persistence` 记录。

剩余边界：

- 当前没有真实高风险或有副作用工具，confirmation-required 工具只在测试中验证。
- 只有新增有副作用工具时，才在 `agent_ui.html` 增加最小批准和重新提交交互。
- 不做 pending approval persistence、多用户权限系统、whitelist 管理或完整审批平台。

最小验收：

- [x] policy 禁用某工具时，Agent step 记录为可解释失败。
- [x] 需要确认但没有 approval id 时，工具不执行。
- [x] 工具 step 独立保存在 invocation detail，不把完整 step 塞进普通 conversation history。

### 3. Clarifying question 工具

状态：已实现第一版。`request_user_input` 是一个低风险、无副作用的 default tool；两个 executor
都会在收到 unresolved interaction 时 pause 同一 invocation，而不是让 HTTP 请求等待用户。

能力线：Agent capabilities、API and UX contracts。

影响阶段：V4 agent loop、invocation persistence 和 `agent_ui.html` 调试交互。

学习价值：

- 当前 mini 的 Agent 无法在执行中向用户补充提问，只能一次性回答或失败。
- AnythingLLM 把 clarification 建成 `request-user-input` tool，并设置 per-turn 上限和 timeout。
- 这适合学习 Agent 和用户交互的边界：模型不能随便用自然语言“等用户回复”，而要通过结构化工具发起请求。

第一版边界：

- 每个 invocation 最多一次结构化 `text` 或 `choice` clarification；choice 有 2–8 个唯一选项。
- 初始和 continue API 都返回 lifecycle response：`needs_input`、`completed` 或
  `max_steps_reached`。`needs_input` 只有 `pending_input`，没有 assistant answer。
- `POST .../agent-invocations/{invocation_id}/continue` 和对应 stream endpoint 使用同一个
  invocation 的累计 LLM/step 预算；choice answer 必须精确匹配，支持 skip。
- 十分钟没有回答时，下一次 continue 自动把 `timed_out` observation 写回原 step 后继续；
  没有后台超时任务。
- native mode 只有在本轮唯一 tool call 是 clarification tool 时才 pause；和其他 tool call 混合时，
  所有该轮调用都记录为未执行的明确失败。
- pause record 立即保存原 user message、steps、metrics、pending input 和最小 resume state，
  但不创建 assistant message；完成后才在同一 invocation 创建唯一 assistant message。
- continue 通过带 15 分钟 lease 的原子 claim 防止两个请求同时恢复同一 invocation；数据库 partial
  unique index 保证同一 conversation 最多一个 `needs_input` 记录，竞争写入回滚并返回 conflict；最终
  保存再以 claim ID fencing，过期的旧请求不能覆盖新请求。

最小验收：

- [x] 两个 executor 在 clarification tool 后 pause，且不把 interaction 视为 artifact。
- [x] 用户回答、skip 或 timeout 后能继续同一个 invocation，并保持累计预算。
- [x] pause 状态没有 assistant message；重复 continue 和同一 conversation 的新 run 被拒绝。
- [x] lifecycle API/SSE、invocation detail、`agent_ui.html` 和 replay 都能表达 pending 状态。

### 4. Tool selection 和工具 prompt 预算

状态：延后触发。当前默认工具有 `calculator`、`workspace_document_search`、
`workspace_document_summary` 和 `request_user_input`，两个 executor 都注入全部工具，暂时没有
selector 或工具数量 metrics。

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

- selector 不影响当前四个默认工具。
- 当工具集变大时，prompt 中只出现被选中的工具。
- invocation metrics 能看到 `available_tool_count` 和 `selected_tool_count`。

### 5. 长文档总结工具

状态：已实现并完成文件名信任边界重构。`workspace_document_summary` 支持列出当前 workspace
文档、只按稳定 `document_id` 读取安全 parsed text、小文档单次摘要和长文档有界 Map + Reduce。

能力线：Document lifecycle、RAG quality、Agent capabilities。

影响阶段：V1/V2 文档能力和 V4 Agent 工具。

学习价值：

- 当前 `workspace_document_search` 是检索工具，只返回相关 chunk 和 sources。
- AnythingLLM 的 document summarizer 能列文档、读取完整内容，并在超出上下文时分块总结。
- 这是学习“长任务工具”的好切片：token 预算、分块、进度、abort、source citation 都会出现。

已实现边界：

- 只允许总结当前 workspace 内已有数据库记录且 parsed text 校验成功的单个文档。
- 一个低风险、只读工具支持 `list` / `summarize`；展示名只用于 list/source/UI，总结只使用
  `document_id`，相同展示名不会产生 selector 歧义。
- 默认按 4000 字符、无 overlap 分块，最多处理前 8 个 chunk；长文档顺序执行 Map + Reduce。
- 超限、部分模型失败或 reduce 失败会返回带覆盖范围和 `stop_reasons` 的可用 partial；没有任何
  可用 chunk summary 时才返回失败；executor 会把 partial 覆盖声明保留到最终 Agent answer。
- `tool_progress` 只暴露 phase 和 N/M；一次工具调用仍只形成一个 Agent step，内部 chunk summaries
  保存在 `artifacts.outputs`。
- 直接文档 source 使用 `score=null`，只保留实际处理的 sections，并继续汇总到 assistant message
  和 invocation detail。
- 暂不做文件生成、附件注入或后台任务恢复。

最小验收：

- [x] 列出可总结文档。
- [x] 小文档直接返回摘要。
- [x] 大文档按 chunk 总结，并在超限时返回明确 partial。
- [x] sources 保留到 assistant message 和 invocation detail。

### 6. Tool artifacts：sources、outputs、attachments 的统一返回

状态：已实现第一版。`ToolResult` 已使用显式 `artifacts` 和 `error_details` 取代宽泛的
`data`；calculator 和 document search 已分别迁移到 `outputs` 和 `sources`，AgentService、
API、invocation step、SSE 和 `agent_ui.html` 共用同一 artifact contract。

能力线：API and UX contracts、Observability and evaluation。

影响阶段：V4 tool result contract，跨阶段 API 和调试能力。

学习价值：

- mini 已把给 LLM 的 `content` observation 与给后端基础设施的结构化 artifacts 分开。
- AnythingLLM 会缓冲 citations、outputs、attachments，并由 chat-history 插件一起 `persist`。
- mini 不必先支持真实二进制附件，但值得统一“工具除了文本 observation 还能产出结构化 artifact”的 contract。

已实现边界：

- `ToolArtifacts` 只支持类型化 `sources` 和轻量 JSON-safe `outputs`。
- `outputs` 限制命名、数量和总长度，并拒绝本地路径、path 字段和内部解析路径。
- validation details、approval id 和 policy reason 独立保存在 `error_details`。
- `agent_ui.html` 通过 invocation detail 读取 artifact，不把完整 artifact 塞进普通
  conversation history。
- 第一版不支持二进制 attachment，只为未来扩展保留清楚边界。
- 本次直接替换旧 `data` API，并通过完整重置本地运行数据结束旧 invocation contract；数据库
  表结构和 Alembic revision 不变。

最小验收：

- [x] 文档搜索工具通过 `artifacts.sources` 返回 sources，top-level assistant sources 不变。
- [x] calculator 通过 `artifacts.outputs["result"]` 返回机器可读结果。
- [x] 同步 API、invocation detail 和 SSE step 使用同一 artifact/error contract。
- [x] 不可信路径、隐藏文件路径、内部解析路径不出现在 Agent API 或调试页中。

### 7. Agent replay 和评估样例

状态：已实现第一版。已支持从本地 SQLite 按 `agent_invocation_id` 导出 JSON fixture，
并使用 fixture replay parser、tool registry 输入校验、artifact source snapshot 和可推导 metrics。

能力线：Observability and evaluation、Developer experience and documentation。

影响阶段：V4 invocation persistence，跨阶段测试。

学习价值：

- Agent 最难稳定的是“同一个问题为什么这次调用了不同工具”。
- 把真实 invocation 导出成可读样例，可以学习回归测试、prompt 调整和工具 schema 调整。

第一版边界：

- `export_agent_replay` 只读导出 invocation、ordered steps、assistant sources snapshot 和
  metrics；移除关系型 ID 与时间戳，并将 source document ID 匿名化。
- `replay_agent_fixture` 不请求 LLM、不调用 `ToolRegistry.run()`；只验证 parser、工具存在与
  输入校验、ToolResult/clarification contract、invocation status/source snapshot 和从 steps 推导出的计数 metrics。
- `react_text` 重放文本 parser；`native_tool_calling` 的 parser check 标记为不适用。
- 真实 LLM 输出与 latency 仅是 evidence，不作为“答案必须完全一致”的 hard assertion。
- 不做在线自动评测平台、HTTP 下载 API、Vue 页面或 `agent_ui.html` 入口。

最小验收：

- [x] 能从某个 `agent_invocation_id` 导出完整的本地 fixture，且不包含 persistence ID 或时间戳。
- [x] 能用 fixture replay parser / tool registry 输入校验 / artifact source / metrics 汇总逻辑。
- [x] 有 synthetic react/native success、parser failure，以及 answered/skip/timeout clarification evaluation fixtures。
- [x] 文档记录如何从一次失败 invocation 变成一个回归测试。

### 8. 轻量 Agent flow

状态：未实现。当前没有 flow schema、节点执行器、独立 endpoint 或作为工具注册的固定
flow。

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

### 已完成基础：事件流、tool policy、artifact contract、clarification 和 replay

- Agent 事件流和 `agent_ui.html` timeline 已实现第一版。
- Tool policy、静态风险元数据、policy gate 和 stateless approval id 已实现第一版。
- Tool artifacts、JSON-safe outputs、类型化 sources 和最小 artifact inspector 已实现第一版。
- 单次 clarification、同一 invocation pause/continue、`agent_needs_input` 和 `agent_ui.html` 调试交互已实现第一版。
- Agent replay、只读 fixture 导出和 clarification 覆盖在内的 synthetic evaluation fixtures 已实现第一版。

后续任务只维护和复用这些基础，不再把它们列为待启动工作。只有出现真实有副作用工具
时，才补 `agent_ui.html` 的最小批准交互。

### 已完成 P2：长任务工具

1. [x] 长文档总结工具第一版。

该切片已增加 `tool_progress`、有界 partial 和 direct-document sources；完整、超限 partial、
模型失败 partial 均有 synthetic replay fixture。人工调试展示仍只在 `agent_ui.html`。

### 条件触发：Tool selection

- 默认注册工具超过 5 个，或实际观察到工具 schema 明显挤占 prompt 时再启动。
- 启动后先做确定性 top N selector 和工具数量 metrics，不做 embedding reranker。
- 在触发条件出现前，不为当前四个默认工具增加选择抽象。

### P3：最后学习工作流和平台化能力

1. 轻量 Agent flow。
2. 更复杂的 streaming / websocket。
3. 更丰富的工具生态。

这类能力可以作为后续专题，不应该倒逼当前最小 agent loop 变复杂。

## 本切片完成后的判断

- 默认工具现为 4 个，仍低于 Tool selection 的触发条件，不增加 selector。
- 真实 abort、中途批准、后台恢复和摘要文件生成继续留在第一版边界外，不为它们预留隐藏状态机。
- 后续若继续这一能力线，应先用真实长文档观察摘要质量、latency 和 source payload，再决定是否学习
  token 级预算、可恢复任务或更复杂的 summary strategy。
