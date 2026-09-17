# AnythingLLM Mini Frontend 设计指南

## 1. 定位

本文档记录 `anythingllm-mini` 后续前端的设计方向。它属于 post-V4 的
API and UX contracts、Developer experience and documentation 能力线，影响 V0-V4
已有能力的前端表达方式，但不改变后端 API、数据库 schema 或 Agent loop 行为。

目标不是一次性复刻 ChatGPT 或完整移植 AnythingLLM，而是用一个轻量 Vue 前端把当前
mini 已经实现的能力逐步可视化：

- workspace 和 conversation 边界。
- 普通 workspace chat。
- workspace-scoped RAG sources。
- Agent run、tool steps、metrics 和 SSE event timeline。
- 文档上传、索引、删除和状态反馈。

第一版前端应服务学习和调试：让重复 `curl` 变成可观察的页面操作，同时帮助复习 Vue、
组件拆分、状态管理、接口契约和页面设计流程。

## 2. 技术选择

推荐基础栈：

```text
frontend/
  Volta-pinned latest Node/npm
  Vue 3 + Vite
  Tailwind CSS
  shadcn-vue
  markdown-it
  Shiki
  Vue Router
  Pinia
```

选择原则：

- Vue 3 适合以 Single-File Component 方式复习 template、reactivity、props、emits、
  slots、composables 和 lifecycle。
- 前端目录使用 Volta 在 `frontend/package.json` 中固定 Node/npm，避免系统默认 Node 版本和
  Vite 最新版的 engine 要求不一致。
- Vite 用作本地开发服务器，并通过 dev proxy 转发后端 API 请求；开发阶段不引入 Nginx。
- Tailwind CSS 和 shadcn-vue 用于基础 UI 原语，例如 Button、Dialog、Sheet、Tabs、
  Tooltip、ScrollArea、Textarea、Skeleton、Toast。
- 聊天业务组件自己写，不直接依赖大而全的 chat template。ChatGPT-like 体验的核心在
  message rendering、composer、sources、tool timeline 和 stream state。
- Pinia 不要在第一个页面就强行引入；当 workspace/conversation/message/agent 状态开始跨
  组件共享时再使用。

暂不选择：

- 暂不引入 Nginx。等有 `frontend/dist`、统一域名、HTTPS、静态资源缓存或部署需求时再学习。
- 暂不做 SSR、Nuxt、复杂权限、多用户团队空间或完整 AnythingLLM 设置中心。
- 暂不把 `backend/app/web/agent_ui.html` 直接迁成正式前端；它仍是后端调试页。

参考资料：

- Vue 官方文档：Vue 提供 declarative rendering、reactivity 和 component-based model。
- Vite 官方文档：`server.proxy` 可以在开发阶段把前端请求代理到后端。
- shadcn-vue 官方文档：它不是传统组件库，而是一套可复制、可改造的组件代码。
- Pinia 官方文档：用于跨组件/页面共享 state，并提供 devtools、testing utilities 和 HMR。
- markdown-it / Shiki：分别负责 Markdown 解析和代码块高亮。

## 3. 信息架构

推荐第一版主界面：

```text
┌─────────────────────────────────────────────────────────────┐
│ Top Bar: app name / current workspace / health indicator     │
├───────────────┬───────────────────────────────┬─────────────┤
│ Sidebar       │ Main Chat                     │ Inspector   │
│               │                               │             │
│ Workspaces    │ MessageList                   │ Sources     │
│ Conversations │ PromptComposer                │ Agent steps │
│ Documents     │ Run status                    │ Metrics     │
└───────────────┴───────────────────────────────┴─────────────┘
```

主界面职责：

- Sidebar：选择 workspace、conversation，创建 conversation，查看 document 入口。
- Main Chat：展示 user/assistant messages，发送 workspace chat 或 agent run。
- Inspector：展示当前 assistant message 的 sources、agent invocation detail、tool steps、
  metrics 和 event timeline。

响应式规则：

- 桌面端使用三栏布局。
- 窄屏时隐藏 Inspector 到 Sheet/Drawer，Sidebar 可折叠。
- PromptComposer 固定在主聊天区域底部；MessageList 独立滚动。

视觉风格：

- 偏工程工作台，不做营销页。
- 信息密度适中，强调可读性、可扫描、状态清楚。
- 使用中性色为主体，sources、tool status、error state 用少量语义色区分。
- 不用大面积渐变、装饰性 hero、复杂动画。

## 4. 组件分层

建议目录：

```text
frontend/src/
  app/
  router/
  stores/
  api/
  composables/
  components/
    layout/
    chat/
    agent/
    documents/
    workspace/
    ui/
  views/
```

组件边界：

```text
layout/
  AppShell
  Sidebar
  InspectorPanel

chat/
  MessageList
  MessageItem
  MessageMarkdown
  CodeBlock
  PromptComposer
  SourceList

agent/
  AgentModeSelect
  AgentRunStatus
  AgentTimeline
  AgentStepItem
  ToolResultCard
  MetricsSummary

documents/
  DocumentUpload
  DocumentList
  DocumentRow

workspace/
  WorkspaceList
  WorkspaceSettings
  ConversationList
```

设计规则：

- `views/` 负责页面组合，不直接写复杂请求逻辑。
- `api/` 封装 fetch、SSE 读取和错误格式化。
- `stores/` 只保存跨组件共享状态；局部输入框、弹窗开关、hover 状态留在组件内。
- `composables/` 放可复用流程，例如 `useAgentStream()`、`useAutoScroll()`、
  `useWorkspaceSelection()`。
- `components/ui/` 放 shadcn-vue 导入或本地改造后的基础 UI 组件，不写业务 API。

## 5. API 契约

前端第一版围绕当前后端已有接口，不要求后端新增 API。

Workspace：

- `GET /workspaces`
- `POST /workspaces`
- `GET /workspaces/{workspace_id}`
- `PATCH /workspaces/{workspace_id}`
- `DELETE /workspaces/{workspace_id}`

Document：

- `POST /workspaces/{workspace_id}/documents/upload`
- `GET /workspaces/{workspace_id}/documents`
- `DELETE /workspaces/{workspace_id}/documents/{document_id}`

Conversation：

- `POST /workspaces/{workspace_id}/conversations`
- `GET /workspaces/{workspace_id}/conversations`
- `GET /workspaces/{workspace_id}/conversations/{conversation_id}/messages`

Chat / Agent：

- `POST /workspaces/{workspace_id}/conversations/{conversation_id}/chat`
- `POST /workspaces/{workspace_id}/conversations/{conversation_id}/agent`
- `POST /workspaces/{workspace_id}/conversations/{conversation_id}/agent/stream`
- `GET /workspaces/{workspace_id}/conversations/{conversation_id}/agent-invocations/{invocation_id}`

约定：

- 开发阶段前端只调用相对路径，例如 `/workspaces`，由 Vite proxy 转发到 FastAPI。
- 不在前端拼接本地 `storage/` 路径；后端本来就不暴露 `upload_path` 和 `parsed_path`。
- `agent/stream` 是 SSE-format event stream，不是 token-by-token answer stream。
- 完成后的 assistant message，或 pause 时已保存的 user message，其 `metrics.agent_invocation_id`
  都是打开 invocation detail、恢复 pending clarification 的入口；字段语义遵循 Agent lifecycle contract。
- sources 展示文件名、chunk、score 和文本摘要，不展示本地文件系统路径。

## 6. 分阶段实现计划

### Phase 0：前端骨架和开发代理

能力线：Developer experience and documentation、API and UX contracts。

历史阶段影响：cross-stage infrastructure。

目标：

- 创建 `frontend/` Vite Vue 项目。
- 使用 Volta 固定前端 Node/npm 版本。
- 配置 Tailwind CSS、基础 layout、Vite proxy。
- 保留 `backend/` 独立运行方式。

Vue 学习点：

- Vite 项目结构。
- Vue SFC。
- `<script setup>`。
- 基础 props / emits。
- 开发代理和相对 API 路径。

验收：

- `frontend/` 能独立启动。
- 页面能请求 `/health` 或 `/workspaces`。
- 不需要 CORS，不需要 Nginx。

### Phase 1：Workspace / Conversation 工作台

能力线：API and UX contracts。

历史阶段影响：V3 workspace and conversation history。

目标：

- 展示 workspace 列表。
- 创建 workspace。
- 展示和创建 conversation。
- 进入一个 conversation 后读取历史 messages。

Vue 学习点：

- `v-for`、`v-if`、empty state。
- 表单输入和提交。
- loading / error state。
- 简单 composable：`useApi()`。

验收：

- 用户可以从空库创建 workspace 和 conversation。
- 刷新页面后能重新读取列表。
- API 错误显示成可理解的 toast 或 inline error。

### Phase 2：基础 Chat / Agent 调用

能力线：API and UX contracts、Agent capabilities。

历史阶段影响：V3 chat、V4 agent loop。

目标：

- `PromptComposer` 支持输入 message、选择 run type：`chat` 或 `agent`。
- agent 支持 `react_text` / `native_tool_calling` mode 选择。
- 发送后将 user message 和 assistant response 刷新到 message list。
- 展示 basic metrics：latency、step count、tool count、source count。

Vue 学习点：

- 组件拆分：composer、message list、message item。
- 父子组件事件。
- 局部 pending state。
- 简单 store：当前 workspace、conversation、selected message。

验收：

- 能从页面完成普通 chat。
- 能从页面完成一次 agent run。
- 失败请求不会重复提交，不会让 composer 永久 loading。

### Phase 3：Markdown、Sources 和代码块

能力线：RAG quality、API and UX contracts。

历史阶段影响：V2 RAG、V3 workspace chat、V4 agent sources。

目标：

- assistant message 支持 Markdown 渲染。
- code block 支持语法高亮和 copy。
- sources 在 Inspector 中展示，点击 source 能定位到对应 assistant message。
- 对空 sources、低分 sources、无 context answer 有清晰 UI。

Vue 学习点：

- slots 和 scoped slots。
- computed view model。
- 第三方库封装。
- DOM 安全边界：Markdown 禁用原始 HTML，避免 XSS。

验收：

- 普通文本、列表、表格、代码块可读。
- source 不暴露本地路径。
- 代码复制有明确成功/失败反馈。

### Phase 4：Agent Trace / SSE Timeline

能力线：Observability and evaluation、Agent capabilities。

历史阶段影响：V4 agent loop。

目标：

- 使用 `agent/stream` 展示实时 event timeline。
- Timeline 至少区分：agent started、LLM started/finished、tool started/finished、
  parse error、max steps、agent finished、agent failed。
- agent finished 后刷新完整 messages 和 invocation detail。

Vue 学习点：

- ReadableStream / SSE 文本解析。
- composable：`useAgentStream()`。
- timeline 状态机。
- abort / cleanup。

验收：

- 运行中能看到 tool timeline。
- agent failed 时 timeline 结束并显示错误。
- 页面切换 conversation 时不会继续写入旧 conversation 状态。

### Phase 5：Document Lifecycle 页面

能力线：Document lifecycle、RAG quality。

历史阶段影响：V1 upload/parsing、V2 RAG、V3 workspace scope。

目标：

- 上传 TXT/PDF/DOCX。
- 展示 document list：filename、extension、size、character count、chunk count。
- 删除 document，并刷新 sources/RAG 状态。

Vue 学习点：

- file input / drag area。
- multipart upload。
- progress / disabled state。
- confirmation dialog。

验收：

- 支持成功上传并进入 RAG。
- unsupported file / empty file / parse failure 有明确错误。
- 删除文档后列表刷新，后续 query 不再依赖已删除文档。

### Phase 6：Workspace Settings 和复习整理

能力线：Developer experience and documentation、API and UX contracts。

历史阶段影响：V3 workspace settings、V4 agent mode。

目标：

- 编辑 workspace name、system prompt、temperature、history limit、chat mode、top_k、
  similarity threshold。
- 整理前端 docs 和组件图。
- 为每个阶段补充“学到了哪些 Vue 知识”的 learning note。

Vue 学习点：

- form validation。
- controlled inputs。
- optimistic update 和 rollback 的取舍。
- Pinia store 分层复盘。

验收：

- settings 保存后刷新仍保持。
- 输入非法值时前端先提示，后端错误仍兜底展示。
- 文档记录组件边界、API 边界和后续待办。

## 7. 状态管理建议

第一阶段先少用全局状态，避免一开始把 Pinia 当数据库。

推荐演进：

```text
Phase 0-1:
  component state + composables

Phase 2:
  workspaceStore
  conversationStore

Phase 3-4:
  messageStore
  agentRunStore

Phase 5:
  documentStore
```

Store 边界：

- `workspaceStore`：workspace list、current workspace、workspace settings save state。
- `conversationStore`：conversation list、current conversation。
- `messageStore`：messages、selected assistant message、refresh status。
- `agentRunStore`：current run state、stream events、selected invocation detail。
- `documentStore`：document list、upload state、delete state。

避免：

- 不把每个输入框都放进 Pinia。
- 不在 store 中直接写复杂 DOM 逻辑。
- 不把后端 response 原样到处传；在组件边界整理成 view model。

## 8. 设计和交互规则

消息展示：

- user message 靠右或使用轻背景。
- assistant message 靠左或使用无边框正文区域。
- system/debug 内容不要混入普通 messages，放到 Inspector。
- running 状态使用 skeleton 或 subtle spinner，不使用大面积遮罩。

Agent Trace：

- Timeline 是解释工具，不是聊天内容。
- tool call 成功、失败、等待确认要一眼区分。
- 不展示 hidden reasoning 或 chain-of-thought。
- parse error 是可学习信息，应显示为可解释事件。

Sources：

- sources 默认折叠在 Inspector。
- source item 显示 filename、chunk index、score、text preview。
- 点击 source 可展开完整 text，但仍不展示本地路径。

Composer：

- Enter 发送，Shift+Enter 换行。
- 发送中禁用重复提交。
- agent mode、max steps 属于高级控制，可先放在 popover 或 compact toolbar。
- 后续需要 stop/abort 时作为独立阶段处理。

错误：

- API 失败要显示后端 error message。
- validation error 显示在表单附近。
- Agent failed 显示在 timeline 和 composer 附近，不伪装成 assistant answer。

## 9. 测试和验收

每个阶段至少保留三类验证：

- 手动路径：从空库开始，创建 workspace、conversation，再完成当前阶段主流程。
- API contract：检查前端使用的 request/response 字段和后端 schema 一致。
- 状态边界：loading、empty、error、retry、切换 workspace/conversation。

后续可以考虑的前端测试：

- 组件级测试：MessageMarkdown、PromptComposer、AgentTimeline。
- E2E smoke：create workspace -> create conversation -> send agent run -> show answer。
- SSE parser 单元测试：解析 event/data block、agent_failed、agent_finished。

## 10. 阶段提交建议

每个阶段单独提交，避免 UI scaffold、业务 API、视觉重构混在一起。

推荐提交切片：

1. `feat(frontend): scaffold vue workspace`
2. `feat(frontend): add workspace conversation shell`
3. `feat(frontend): send chat and agent messages`
4. `feat(frontend): render markdown sources and code blocks`
5. `feat(frontend): show agent stream timeline`
6. `feat(frontend): manage workspace documents`

每个阶段 final summary 仍按工作区规则提供 1-4 个高学习价值测试或组件文件，不列全部文件。
