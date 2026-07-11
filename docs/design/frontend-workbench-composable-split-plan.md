# Frontend Workbench Composable 拆分计划

## 1. 状态与定位

- 状态：TODO / Deferred。
- 基线：commit `7a28ec5`，tag `checkpoint/frontend-phase3`。
- 能力线：Developer experience and documentation、API and UX contracts。
- 历史阶段影响：V3 workspace/conversation history、V4 chat/agent run。

本文档记录 `frontend/src/composables/useWorkspaceWorkbench.js` 的待做拆分方案。
当前优先级低于 Agent 能力开发，不立即执行。下一次继续扩展前端运行流程，特别是加入
SSE、停止生成、Agent timeline 或更多 conversation run 状态前，再启动本计划。

这次拆分是行为保持型重构，不新增产品能力，也不以减少文件行数作为唯一目标。

## 2. 当前问题

在 Phase 3 checkpoint 中，`useWorkspaceWorkbench.js` 已达到约 508 行，并同时管理：

- workspace 列表、创建、选择和错误状态。
- conversation 列表、创建、选择和刷新。
- message history 加载、重试和 assistant message 选择。
- chat / agent 请求、pending、错误和最近一次运行 metrics。
- workspace、conversation、message 和 send request 的过期响应保护。
- workspace -> conversation -> message -> run 的级联清理。

当前文件仍然是一个可理解的页面级 workbench facade，而且只有 `AppShell.vue` 直接使用它。
因此问题不是“文件超过某个行数就必须拆”，而是 message history 与 conversation run 已形成
相对独立、未来还会继续增长的职责边界。

不应采用只搬动函数的机械拆分。拆分后的模块必须各自拥有清晰状态和 request id，同时把
跨模块编排保留在 `useWorkspaceWorkbench()` 中。

## 3. 目标

- 降低单文件阅读负担，让 workspace navigation、message history 和 run state 容易分别理解。
- 让 `messageRequestId` 与 `sendMessageRequestId` 分别归属实际管理该请求的模块。
- 保持 `useWorkspaceWorkbench()` 作为页面级 facade 和跨模块编排入口。
- 第一轮保持 `AppShell.vue` 使用的公开返回字段和函数名称不变。
- 为异步竞态、级联清理和 assistant 选择行为增加 composable 测试。
- 为未来 SSE、abort 和 agent timeline 提供明确的 run-state 扩展位置。

## 4. 非目标

- 不修改后端 API、数据库 schema、Agent loop 或持久化行为。
- 不引入 Pinia、Vue Router、TypeScript 或新的状态管理依赖。
- 不调整页面布局、文案、Markdown、Sources 或 PromptComposer 交互。
- 不改变 chat / agent 请求体、响应解析或 FastAPI error 展示。
- 不在同一切片中加入 SSE、abort、timeline 或 invocation detail。
- 不为了追求更小文件而提取一次性 helper 或通用框架式抽象。

## 5. 必须保持的行为

### 5.1 初始化与选择

- 页面挂载后加载 workspace 列表，并默认进入第一项。
- workspace 加载完成后加载 conversation，并默认进入第一项。
- conversation 加载完成后读取 messages，并默认选择最后一条 assistant message。
- 手动刷新 messages 时，原 assistant 选择仍存在则继续保留。
- 新建 workspace 后选择新 workspace，并清空旧 conversation/message/run 状态。
- 新建 conversation 后选择新 conversation，并加载其空消息列表。

### 5.2 级联清理

- 切换 workspace 必须使旧 conversation、message 和 send response 失效。
- 切换 conversation 必须使旧 message 和 send response 失效。
- 清空 messages 时必须同步清空 selected assistant 和 message error/loading。
- 清空当前 run 时必须复位 pending、send error、run type 和 run metrics。

### 5.3 过期响应保护

- 较慢的旧 conversation response 不能覆盖新 workspace 的 conversations。
- 较慢的旧 message response 不能覆盖新 conversation 的 messages。
- 发送期间切换 workspace/conversation 后，旧 send response 不能刷新当前页面。
- finally 只能复位属于当前 request id 的 loading/pending 状态。

### 5.4 发送成功与失败

- chat 和 agent 继续调用当前 API client 方法。
- 发送成功后重新读取后端持久化 messages，不在前端手工拼接消息。
- 重新读取后选择最新 assistant message。
- 发送成功后刷新 conversation list，以显示自动标题。
- 请求失败时保留 composer 输入，并正确复位 pending。
- agent run 继续记录 `agent_invocation_id` 和 `agent_mode`。

## 6. 目标结构

第一轮只提取职责最清晰、增长最快的两个模块：

```text
frontend/src/composables/
  useWorkspaceWorkbench.js
  useConversationMessages.js
  useConversationRun.js
```

职责分配：

| 模块 | 拥有的状态 | 主要行为 |
| --- | --- | --- |
| `useWorkspaceWorkbench` | workspaces、conversations、selected workspace/conversation | 初始化、创建、导航、conversation refresh、跨模块级联清理、公开 facade |
| `useConversationMessages` | messages、selected assistant、message loading/error、`messageRequestId` | 加载/重试 messages、选择/reconcile assistant、清空 message state |
| `useConversationRun` | send pending/error、last run type/metrics、success count、`sendMessageRequestId` | chat/agent 发送、过期响应检查、成功后的刷新编排 |

暂不强制提取 `useWorkspaceNavigation.js`。完成前两个模块后，如果
`useWorkspaceWorkbench.js` 仍然难以阅读，或者 workspace/conversation 状态开始被其他页面复用，
再将 navigation 作为后续独立切片。

## 7. 建议接口

### 7.1 `useConversationMessages()`

建议返回：

```text
messages
selectedAssistantMessageId
selectedAssistantMessage
isLoadingMessages
messagesError
loadMessages(workspaceId, conversationId, options)
retryMessages(workspaceId, conversationId)
selectAssistantMessage(messageId)
clearMessages()
```

约束：

- 直接复用当前 API client，不增加通用 repository/service 包装层。
- `messageRequestId` 保持模块内部私有。
- `clearMessages()` 只清理 message domain，不直接清理 run domain。
- 是否同时清理 run 由 facade 的 workspace/conversation 切换流程决定。

### 7.2 `useConversationRun(options)`

建议输入：

```text
selectedWorkspaceId
selectedConversationId
reloadMessages(workspaceId, conversationId, options)
refreshConversationList(workspaceId)
```

建议返回：

```text
isSendingMessage
sendMessageError
lastRunType
lastRunMetrics
sendMessageSuccessCount
sendMessage(payload)
clearRun()
```

约束：

- `sendMessageRequestId`、run type 常量和默认 agent mode 归该模块所有。
- 依赖通过少量明确 callback 传入，不直接反向 import workbench。
- 不创建全局 event bus，不使用 provide/inject 隐藏调用关系。
- `sendMessage()` 的外部返回值继续是 `Promise<boolean>`。

### 7.3 `useWorkspaceWorkbench()`

拆分后继续承担：

- 创建 messages/run composable 实例。
- 管理 workspace 和 conversation 的 API 流程。
- 在 workspace/conversation 切换时显式调用 `clearMessages()` 和 `clearRun()`。
- 将子 composable 的字段以当前平铺接口继续返回给 `AppShell.vue`。
- 只在这里注册首次 `onMounted(loadWorkspaces)`，避免重复初始化请求。

## 8. 执行切片

### Slice 0：补齐行为刻画测试

新增 workbench/composable 测试，在重构前锁定当前行为。

重点场景：

- 初始化选择第一 workspace、第一 conversation 和最后一条 assistant message。
- 手动刷新 messages 后保留仍存在的 assistant 选择。
- 快速切换 conversation 时忽略旧 message response。
- 发送期间切换 conversation 时忽略旧 send response。
- 发送成功后 reload messages、选择最新 assistant，并刷新 conversation title。
- 发送失败时复位 pending 并保留可理解的 error。

测试可通过 `vi.mock("@/api/client")` 和可控 deferred promise 构造竞态，不引入新的依赖。

建议提交：

```text
test(frontend): characterize workspace workbench state flows
```

### Slice 1：提取 Conversation Run

- 新建 `useConversationRun.js`。
- 移动 run refs、run constants、`sendMessageRequestId`、`clearCurrentRunState()` 和
  `sendMessage()`。
- 通过 callback 保留 reload messages 和 refresh conversations 行为。
- `useWorkspaceWorkbench()` 继续返回原有 run 字段和函数。
- `AppShell.vue` 原则上不改；若格式化造成微小 diff，应单独确认无契约变化。

建议提交：

```text
refactor(frontend): extract conversation run state
```

### Slice 2：提取 Conversation Messages

- 新建 `useConversationMessages.js`。
- 移动 message refs、assistant selection、`messageRequestId`、load/retry/clear 逻辑。
- 在 workbench facade 中保留 workspace/conversation 切换时的级联清理顺序。
- 保持发送成功后的 `selectLatestAssistant` 语义。

建议提交：

```text
refactor(frontend): extract conversation message state
```

### Slice 3：评估是否继续提取 Navigation

完成前两步后重新阅读 `useWorkspaceWorkbench.js`。

仅当出现以下情况之一时才增加 `useWorkspaceNavigation.js`：

- workspace/conversation 状态被第二个 view 或 composable 复用。
- workbench facade 仍同时包含大量列表 CRUD 和跨模块编排细节。
- 文档管理阶段需要复用 workspace selection 生命周期。

如果剩余 facade 已能清楚表达页面初始化和级联关系，则停止拆分。

## 9. 风险与约束

### 请求失效边界漂移

最大风险是 request id 移动后，workspace/conversation 切换未正确使旧请求失效。

约束：每个 request id 只能由对应 domain 修改；跨 domain 失效通过公开 `clear*()` 完成，
不能由多个模块直接修改同一个计数器。

### 清理顺序变化

拆分后容易遗漏 run state 或 assistant selection 清理。

约束：workspace/conversation 的级联清理由 facade 显式编排，并由测试验证最终状态。

### 循环依赖

run 需要刷新 messages/conversations，但 messages 和 navigation 不应反向依赖 run。

约束：依赖方向固定为 facade -> domain composables；run 通过 callback 请求刷新，不 import
workbench。

### 只减少行数而增加跳转

过度拆分会让一个简单流程跨越过多文件。

约束：不提取一次性 helper；不为了对称而强制增加 navigation composable；每个新文件必须
拥有独立状态或 request 生命周期。

## 10. 验证计划

每个 slice 完成后运行：

```bash
cd frontend
npm run test
npm run check
npm audit --audit-level=moderate
cd ..
git diff --check
```

手动验证：

- 从空库创建 workspace 和 conversation。
- 在多个 conversation 间快速切换，确认消息不会串位。
- 发送 chat 和 agent 后确认 messages、metrics、sources 与 conversation title 正常刷新。
- 发送期间切换 conversation，确认旧响应不覆盖新页面。
- 刷新当前 messages，确认手动选择的历史 assistant 仍被保留。
- 关闭后端后重试，确认 loading/pending 不会卡死。

本重构不修改后端契约，因此默认不要求新增后端测试。若实现期间发现依赖字段与 API schema
不一致，应停止重构并将其作为独立契约问题处理。

## 11. 验收标准

- `AppShell.vue` 使用的 workbench 字段和函数名称保持不变。
- workspace/conversation/message/run 的行为不变量全部有测试覆盖。
- 三个 request id 各自只有一个明确 owner。
- `useWorkspaceWorkbench.js` 主要表达页面级初始化、导航和级联编排。
- `sendMessage()` 与 message selection 不再与 workspace CRUD 混在同一个实现文件中。
- 不新增 Pinia、Router、TypeScript 或运行时依赖。
- 自动化检查和手动关键路径全部通过。

## 12. Suggested Tests To Read

- `frontend/src/composables/useWorkspaceWorkbench.test.js`：重构前后最重要的状态流和竞态保护契约；该文件将在 Slice 0 新增。
- `backend/tests/test_workspaces_api.py::test_workspace_conversation_chat_http_flow`：确认 conversation、messages 和 chat 持久化契约。
- `backend/tests/test_agents_api.py::test_agent_endpoint_runs_agent_loop`：确认 agent response、metrics 与 persisted assistant message 的关系。

