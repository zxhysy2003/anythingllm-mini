# Agent clarification：可持久化的 pause/continue lifecycle

术语遵循 [Agent 文档术语（Terminology）](agent-terminology.md)。

## 目的

`request_user_input` 解决的是 Agent 在执行中缺少必要信息时的边界问题。模型不能只在自然语言里
说“请告诉我范围”，因为服务端无法知道它是否应 pause、何时 continue，以及这条回答属于哪一次
`invocation`。

第一版把该行为固定为一个低风险、无副作用的 tool call：它返回 `ToolResult.interaction`，而不是
`artifacts`。`content` 只是一条给后续 LLM 使用的 `observation`；真正控制 pause/continue 的是结构化
`interaction contract`。

## Lifecycle 状态

```text
initial run
  -> request_user_input(text | choice)
  -> AgentRunResult.pending_interaction
  -> AgentInvocation(status=needs_input)
  -> POST continue(answer | skip)
  -> resolved interaction + observation
  -> 同一 invocation continue executor
  -> completed 或 max_steps_reached
```

pause 时立刻写入 `persistence`：原始 user message、已执行 steps、累计 metrics、`pending_input` 和最小
`resume_state`。没有 assistant message、answer 或 `ended_at`。continue 成功后才更新原 invocation、
创建唯一 assistant message，并清除 pending state。

初始 Agent run 和 continue 都会用条件更新取得 `Conversation.agent_execution_claim_id`，并记录一个 15 分钟
lease：同一 conversation 同时只能有一个 active Agent execution，冲突请求会得到 conflict，且不会调用 LLM 或
执行 tool。请求生命周期内每五分钟会有一次 heartbeat 续约；这不是独立后台任务。失败或 coroutine 被取消时，
claim 会被释放；进程中断后，lease 到期可由后续请求接管。pause 后 execution claim 被释放，但数据库仍以
`status='needs_input'` 的 partial unique index 保证同一 conversation 最终至多保存一个待回答 invocation，阻止
用户在等待 clarification 时发起新的 Agent run。

lease 是可恢复性边界，不代表旧请求可以在过期后覆盖新请求：pause、completed 和 continue finalization 的
persistence 会在同一 transaction 内按 conversation claim ID 执行 `fencing`。如果旧请求在 lease 过期后才返回，
条件更新会失败并 rollback，不会创建额外 message、覆盖 steps 或修改 lifecycle 状态。

`react_text` 可以根据已有 steps 重建 observation；`native_tool_calling` 在暂停时还会保存当时的完整 messages，以及正在等待用户回答的 `request_user_input` 所对应的 `tool_call_id`。continue 后，系统使用该 ID 追加一条 `role="tool"` 的响应，使用户回答能够关联到原 assistant 消息中的工具调用。

## Input contract 和限制

- `text`：只接受非空问题，回答是去首尾空白后的非空文本。
- `choice`：必须有 2–8 个唯一选项，answer 必须精确匹配其中一项。
- 每个 invocation 最多一次 clarification；第二次调用会得到可解释失败 observation。
- 请求 clarification 时必须还剩至少一次 LLM call，否则不 pause，避免回答永远无法被模型使用。
- native mode 中 clarification 必须是该轮唯一 tool call。若和其他调用混合，整轮调用都不执行并记录失败。

`skip` 生成 `skipped` resolution。过期时间固定为 pause 后十分钟；没有后台定时器，只有下一次
continue 发现过期后才写入 `timed_out` resolution 并继续。因此等待时间不计入
`total_latency_ms`。

## API 和调试边界

初始 Agent API 与 continue API 都返回 lifecycle response：

- `needs_input`：`answer` 为 `null`，`pending_input` 包含 question、input type、choices 和 expiry。
- `completed` / `max_steps_reached`：`answer` 有最终值，`pending_input` 为 `null`。

Invocation detail 也使用同样的 answer 表达：pending invocation 的 `answer` 为 `null`；完成后由关联的
assistant message 返回最终 answer，客户端不需要额外读取 conversation messages 才能重建 lifecycle state。

SSE 用 `agent_needs_input` 作为 pause 的正常终点；只有真正完成时才发 `agent_finished`。
`backend/app/web/agent_ui.html` 只提供 text、choice、Skip 和过期提示，以验证这套 backend contract；
Vue 前端不在该切片中。

## 没有实现的内容

这不是通用的人机协作平台：第一版没有多轮 clarification、后台 timeout 任务、WebSocket、用户身份绑定、
多客户端订阅、approval persistence 或通用 checkpoint/resume。它只演示“一个结构化问题 pause，再由同一
invocation continue”的最小、可测试边界。
