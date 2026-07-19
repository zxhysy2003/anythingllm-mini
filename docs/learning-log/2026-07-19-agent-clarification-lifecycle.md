# 2026-07-19 Agent Clarification Lifecycle 学习记录

## 1. 本次开发模块

- 模块名称：Agent clarification 与 conversation execution claim
- 开发目标：让 V4 Agent 能通过 `request_user_input` 暂停一次 invocation、等待用户回答后在同一条
  invocation 上继续；同时防止同一 conversation 的并发 Agent execution 互相覆盖状态。
- 当前阶段：V4 后的 Agent capabilities、Persistence and migrations、API and UX contracts，以及
  Observability and evaluation。
- 相关文件：`backend/app/tools/clarifying_question.py`、
  `backend/app/services/agent_service.py`、`backend/app/core/native_tool_calling.py`、
  `backend/alembic/versions/0004_add_agent_pending_input.py`、
  `backend/alembic/versions/0005_add_agent_invocation_claims.py`。

## 2. 本次代码改动概览

- 新增 `request_user_input` tool：输入为 text 或 choice clarification，成功时返回
  `ToolResult.interaction`，而不是 artifact。
- 将 invocation lifecycle 扩展为开始、`needs_input` 暂停、continue、完成；暂停时只保存 user message、
  step、pending input 与 resume state，不创建 assistant message。
- 为 React text 与 native tool calling executor 增加 continuation 支持。React 从已保存 steps 重建
  observation；native 保存原始 tool-call transcript 与 `pending_tool_call_id`。
- 新增 `0004`、`0005` migration：允许 pending invocation 没有 assistant message/结束时间，保存
  `pending_input`、`resume_state`，并在 conversation 保存 execution claim。
- 扩展 replay fixture 与测试样例，覆盖 answered、skipped、timed_out 及 native clarification。

## 3. 本次关键问题与回答

### Q1：`clarifying_question.py` 怎样被 LLM 使用？

**回答要点：**

- LLM 不会直接执行 Python 类；tool registry 将 `request_user_input` 的名称、说明和 Pydantic input
  schema 提供给 LLM。
- React text 模式要求 LLM 输出 `Action` / `Action Input`；native 模式将它作为 function tool schema
  传给 provider。
- executor 解析调用后交给 `ToolRegistry.run()` 校验，再得到带 unresolved `interaction` 的
  `ToolResult`；此时 executor 立即暂停，而不是把问题当成普通 observation 后继续循环。

**复习版理解：**

clarification 是由 LLM 决定“需要补充信息”的 control-flow tool。Python tool 只负责把这个决定转换为
严格的结构化请求；真正的用户答案由 continue API 在之后提供。

**相关代码：**

- `backend/app/tools/clarifying_question.py`
- `backend/app/tools/interactions.py`
- `backend/app/tools/registry.py`

### Q2：`AgentResumeState.executor_state` 保存什么？

**回答要点：**

- `history` 是普通 conversation context；`executor_state` 是 executor-specific continuation state。
- React text 可以从持久化 steps 的 observation 继续，因此当前不需要额外 executor state。
- Native mode 必须保留 messages transcript 和 `pending_tool_call_id`，恢复时才能补回与原 tool call
  ID 对应的 `role="tool"` response。

**易错点：**

- 不要只保存自然语言 history 就认为 native tool calling 能恢复；tool response 与 tool call ID 的对应
  关系属于协议状态。

**相关代码：**

- `backend/app/services/agent_service.py`
- `backend/app/core/native_tool_calling.py`

### Q3：`_claim_agent_execution()` 如何处理并发？

**回答要点：**

- 它使用带条件的原子 `UPDATE conversations` 写入随机 claim ID 与时间戳，不依赖“先查询、再写入”的
  内存判断。
- 初始 run 的 `pending_condition` 要求不存在 `needs_input` invocation；continue 则要求目标
  invocation 仍是 `needs_input`。
- update 的 `rowcount == 1` 才算取得 claim；`0` 代表已被其他执行占用或状态变化，映射为 HTTP 409。
- 15 分钟 lease 与 5 分钟 heartbeat 防止慢执行被误接管；最终写入用 claim ID fencing，旧执行失去
  claim 后必须 rollback。

**复习版理解：**

claim 是 conversation 级的持久化排他执行权，不是 Python 内存锁。它同时保护 initial run 和 continue，
从而让并发请求在调用 LLM/tool 之前就被拒绝。

**相关代码：**

- `backend/app/services/agent_service.py`
- `backend/app/models/conversation.py`
- `backend/alembic/versions/0005_add_agent_invocation_claims.py`

### Q4：两个 release helper 为什么不能合并？

**回答要点：**

- `_release_agent_execution()` 用于异常 cleanup，会独立 commit，只释放自己的 claim。
- `_release_agent_execution_in_transaction()` 不 commit，供暂停、完成与 finalization 调用；它必须和
  message、invocation、step 的写入处于同一 transaction。
- 事务版本会检查 rowcount，作为 fencing 的最后一道校验：claim 被接管时不能提交旧结果。

**易错点：**

- 如果完成路径提前 commit claim release，后续写入失败可能留下“claim 已释放但运行结果只保存一半”的
  数据不一致。

## 4. 本次代码设计决策

### 决策 1：将 clarification 放入 `ToolResult.interaction`

选择把 clarification 设计为不属于 artifact 的 `interaction`。artifact 用于安全结构化产物，
interaction 则表示会改变 Agent lifecycle 的控制信息；两者不会混淆，也不会自动传给 LLM。

### 决策 2：每个 invocation 只允许一次 clarification

第一版限制一次请求，并要求提出问题后仍至少保留一次 LLM call。这样能清晰展示暂停/恢复边界，避免
立即演变为多轮状态机或后台超时任务。

### 决策 3：conversation 级 execution claim

claim 不放在 invocation 上，因为初始 run 尚未有 invocation。放在 conversation 可以让 initial run 和
continue 共享同一互斥边界，同时保留 `needs_input` partial unique index 作为持久化不变量。

## 5. 踩坑点与注意事项

- 新增 `needs_input` 状态时，`assistant_message_id` 和 `ended_at` 必须允许为 `NULL`；migration 与
  SQLModel model 要同步修改。
- pending invocation 落库前先做 claim fencing，避免 partial unique index 的 autoflush 将“旧执行”
  错误地变成 500，而不是预期的 409 conflict。
- finalization 时 assistant message 必须先 flush，再更新引用它的 invocation，才能在启用 foreign key
  的 SQLite 环境中保持正确顺序。
- invocation detail 应显式提供 `answer`；完成后客户端不应必须再扫描 messages 才能重建 lifecycle
  result。
- conversation title 更新应由 SQL `CASE` 基于数据库当前值决定，不能用旧 ORM 对象覆盖普通 chat 并发
  更新的标题。

## 6. 面试可讲版本

> 我给一个 FastAPI Agent 学习项目增加了 clarification lifecycle。模型可以通过结构化 tool call 请求
> 用户补充信息，服务端把运行持久化为 `needs_input`，收到答案后在同一 invocation 上继续，并恢复
> 剩余 LLM 调用预算。为了处理并发，我在 conversation 上设计了带 lease、heartbeat 和 fencing 的
> execution claim：竞争请求会在调用模型前收到 409，旧执行即使晚到也不能覆盖新状态。这个过程让我
> 更清楚地理解了 Agent control flow、数据库 transaction 和 API lifecycle contract 需要一起设计。

## 7. 后续 TODO

- [ ] 继续 P2：澄清工具之后的长文档总结工具。
- [ ] 如未来需要多轮 clarification，再将单次限制扩展为显式状态机，并补充对应 replay fixture。
