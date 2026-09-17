# 2026-07-19 模块学习记录：AgentService 职责拆分

## 1. 本次开发模块

- 模块名称：AgentService lifecycle 与 invocation persistence 职责拆分
- 开发目标：降低 `AgentService` 阅读负担，同时保持 V4 Agent lifecycle、数据库事务和 API 行为不变
- 当前阶段：V4 之后的 Agent capabilities + persistence 小步重构
- 相关分支：`feat/monorepo-backend`
- 相关文件：`agent_service.py`、`agent_invocation_store.py`、对应 service/store 测试和当前 stage note

## 2. 本次代码改动概览

根据 git diff，本次主要改动包括：

- 新增 `AgentInvocationStore`，集中 execution claim、lease、fencing、invocation/message/step persistence。
- `AgentService` 保留 run/continue/get 编排、heartbeat、clarification、metrics 和结果组装。
- 将直接测试 persistence 私有方法的用例迁移到独立 store 测试文件。
- 更新当前 V4 stage note，使文档中的职责边界和代码一致。

| 文件 | 作用 | 本次变化 |
|---|---|---|
| `backend/app/services/agent_service.py` | Agent lifecycle 编排 | 从 1238 行降到约 739 行，通过注入的 store 完成 persistence |
| `backend/app/services/agent_invocation_store.py` | invocation persistence | 新增 claim、fencing、save/finalize 和 step 映射边界 |
| `backend/tests/test_agent_invocation_store.py` | persistence 边界测试 | 新增 pending claim、stale pause、并发 title 和 finalize fencing 测试 |

## 3. 本次关键问题与回答

### Q1：`agent_service.py` 很长后，是否应该拆分？

**问题背景：**

clarification pause/continue 和 execution claim 完成后，`agent_service.py` 已达到 1238 行，后续阅读和修改需要在 lifecycle、SQL 和序列化代码之间频繁跳转。

**用户困惑：**

文件行数变长是否足以说明需要拆分，以及拆到什么程度才不会过度设计。

**回答要点：**

- 行数只是信号，真正的拆分依据是是否存在稳定、可单独描述和测试的职责。
- Agent lifecycle orchestration 与 invocation persistence 已经形成明确边界。
- claim、fencing 和 finalize 必须留在同一个 persistence collaborator 中，避免破坏原子性。
- 第一版只增加一个 store，不拆 contracts、clarification service 或通用状态机。

**复习版理解：**

拆文件不是把代码平均分配，而是让一次修改只需要理解一个概念。`AgentService` 回答“Agent 何时开始、暂停、继续和结束”，`AgentInvocationStore` 回答“这些状态如何安全写入数据库”。这种边界既减少阅读负担，又没有隐藏当前最小 Agent loop 的控制流。

**相关代码：**

- `backend/app/services/agent_service.py`
- `backend/app/services/agent_invocation_store.py`
- `backend/tests/test_agent_invocation_store.py`

**易错点：**

- 只按函数数量拆分，可能得到很多互相调用的小文件，反而更难理解。
- 将 fenced release 与 save/finalize 分开，可能让 claim 校验和最终写入不再处于同一 transaction。

## 4. 本次涉及的技术点

| 技术点 | 在项目中的位置 | 为什么需要 | 类似方案 | 复习重点 |
|---|---|---|---|---|
| Service orchestration | `AgentService` | 保持 Agent lifecycle 显式可读 | 通用状态机 | 当前阶段优先直接控制流 |
| Persistence collaborator | `AgentInvocationStore` | 集中 SQL、commit/rollback 和 record 映射 | Repository / Unit of Work | collaborator 应对应真实职责，不为抽象而抽象 |
| Optimistic fencing | claim ID 条件更新 | 阻止 lease 过期的旧请求覆盖新请求 | 数据库锁、版本号 | 最终写入和 claim 校验必须原子完成 |
| Dependency injection | `AgentService(invocation_store=...)` | 让编排依赖清晰并可独立测试 | 模块级函数 | 默认单例保持现有调用兼容 |

## 5. 本次代码设计决策

### 决策 1：只新增一个 `AgentInvocationStore`

**选择：** 把 claim、fencing、message/invocation/step persistence 放在同一个类中。

**原因：** 这些操作共享 SQLModel session 和 transaction 边界，放在一起更容易检查原子性。

**替代方案：** 分成 claim manager、repository、step store，或者引入 Unit of Work。

**取舍：** 新 store 仍有约 550 行，但职责单一；避免为了缩短文件继续制造跨模块跳转。

### 决策 2：heartbeat 继续由 `AgentService` 管理

**选择：** store 只执行 claim renewal SQL，async task 的启动和停止仍在 service。

**原因：** heartbeat 生命周期和一次 Agent run/continue 一致，属于 orchestration。

**替代方案：** 让 store 创建后台 task 或用 async context manager 隐藏清理流程。

**取舍：** service 中保留少量重复清理代码，但异常与 cancellation 行为仍然直观。

### 决策 3：store 接收已序列化的 metrics/resume payload

**选择：** service 使用 Pydantic model 校验和计算，再把 JSON-ready dict 交给 store。

**原因：** 防止 store 反向导入 service DTO 导致循环依赖，同时明确 persistence 边界。

**替代方案：** 把所有内部 DTO 再拆到独立 contracts 文件。

**取舍：** store 的内部参数类型更接近数据库 payload，但避免了本次范围外的第三个生产模块。

## 6. 踩坑点与注意事项

- `git diff --stat` 默认不统计 untracked 新文件，审查时必须结合 `git status --short`。
- 行为保持重构仍要检查序列化模式；metrics、pending input 和 resume state 继续使用 JSON-ready payload。
- 移动私有方法后，测试不能继续从 `AgentService` 调用旧 persistence helper，应直接测试新边界。
- persistence failure、heartbeat cancellation 和 API contract 仍需要由原有集成测试保护，不能只保留 store 单元测试。

## 7. 面试可讲版本

> 在 Agent clarification lifecycle 完成后，我发现 `AgentService` 同时承担运行编排和数据库状态管理，文件已经比较难阅读。
> 我把 execution claim、fencing 和 invocation persistence 提取到一个 `AgentInvocationStore`，但保留 heartbeat 和 pause/continue 控制流在 service 中。
> 拆分时最重要的是没有破坏 claim 校验与最终写入的事务原子性，并通过 stale execution 和 claim takeover 测试验证。
> 这次重构让我理解到，拆分依据应当是职责和变化方向，而不是单纯追求更少的代码行数。

## 8. 后续 TODO

- [ ] 后续新增 Agent persistence 状态时，优先在 store 边界增加独立测试。
- [ ] 只有 clarification 出现第二种 interaction 或多轮需求时，再评估是否提取独立 lifecycle 模块。
- [ ] 避免仅为减少行数继续拆分当前 cohesive store。
