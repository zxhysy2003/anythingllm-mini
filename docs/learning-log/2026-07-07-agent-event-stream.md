# 2026-07-07 模块学习记录：Agent Event Stream

## 1. 本次开发模块

- 模块名称：Agent 事件流与实时 introspection
- 开发目标：为 V4 Agent loop 增加 SSE-format 运行事件流，让 UI 能实时展示 Agent 执行 timeline。
- 当前阶段：Post-V4 能力线迭代，影响 V4 Agent loop 和跨阶段 API/UI 合同。
- 相关分支：`cleanup/v4-final-version`
- 相关文件：
  - `app/api/agents.py`
  - `app/core/agent_events.py`
  - `app/core/agent_executor.py`
  - `app/core/agent_loop.py`
  - `app/core/native_tool_calling.py`
  - `app/services/agent_service.py`
  - `app/web/agent_ui.html`
  - `tests/test_agents_api.py`
  - `tests/test_agent_loop.py`
  - `tests/test_agent_service.py`
  - `tests/test_native_tool_calling.py`
  - `tests/test_agent_ui.py`

## 2. 本次代码改动概览

根据 git diff，本次主要改动包括：

- 新增 `AgentEvent`、`AgentEventType`、`AgentEventEmitter` 和 `emit_agent_event()`，作为 service/executor 到 API stream 的轻量事件合同。
- 新增 `POST /workspaces/{workspace_id}/conversations/{conversation_id}/agent/stream`，使用 `StreamingResponse` 输出 `text/event-stream`。
- 在 `AgentService.run_in_conversation()` 和两个 executor 中增加可选 `event_emitter`，发出 `agent_started`、`llm_started`、`tool_started`、`tool_finished`、`agent_finished` 等事件。
- 在 Agent UI 中改为读取 `/agent/stream`，解析 SSE 文本块，并追加 live timeline。
- 增加 executor、service、API、UI 相关测试，锁住事件顺序、stream payload 和请求校验。

涉及文件：

| 文件 | 作用 | 本次变化 |
|---|---|---|
| `app/core/agent_events.py` | Agent 事件模型 | 新增固定事件类型、Pydantic 事件对象和 emitter 协议 |
| `app/api/agents.py` | Agent API 路由 | 新增 SSE stream route、`asyncio.Queue` 和 producer/consumer 协程 |
| `app/services/agent_service.py` | Agent 业务编排 | 在 workspace/conversation context、保存成功和异常分支发事件 |
| `app/core/agent_loop.py` | ReAct 文本 executor | 在 LLM 调用、工具调用、解析错误、max steps 分支发事件 |
| `app/core/native_tool_calling.py` | Native tool calling executor | 在 LLM/tool/max steps 分支发事件 |
| `app/web/agent_ui.html` | 本地 Agent UI | 通过 fetch stream 解析 SSE，并渲染 live timeline |
| `tests/test_agents_api.py` | API 合同测试 | 覆盖 stream endpoint、SSE 格式和最终 payload |

## 3. 本次关键问题与回答

### Q1：Agent 事件流是不是 ChatGPT 式 token streaming？

**问题背景：**

看到 roadmap 里写“Agent 事件流和实时 introspection”后，容易把它理解成 ChatGPT 网页那种一段一段显示 answer 文本的流式输出。

**用户困惑：**

这个功能是不是指模型 token-by-token 或 paragraph-by-paragraph 输出，而不是等请求完成后一次性返回完整答案？

**回答要点：**

- 本次实现的是 Agent 运行事件 timeline，不是 token streaming。
- SSE 中实时出现的是 `agent_started`、`llm_started`、`tool_started`、`tool_finished` 等状态事件。
- 最终 answer 仍由当前 Agent executor 完整生成、保存后，放在最后一个 `agent_finished.payload.answer` 中。
- 这样可以先学习事件模型和 introspection，而不用同时引入 token 流、取消运行、后台任务恢复等复杂能力。

**复习版理解：**

当前 `/agent/stream` 解决的是“用户能不能在 Agent 运行过程中看到它进行到哪一步”，不是“模型回答能不能边生成边显示”。普通 `/agent` endpoint 仍然一次性返回完整结果；新增 `/agent/stream` 只是把中间状态用 SSE 推给前端。最后的 `agent_finished` 事件才携带完整的 `WorkspaceAgentResponse` 形状，包括 `answer`、`steps`、`metrics` 和 `agent_invocation_id`。

**相关代码：**

- `app/api/agents.py`
- `app/core/agent_events.py`
- `app/services/agent_service.py`
- `app/web/agent_ui.html`

**易错点：**

- 不要把 SSE 等同于 token streaming；SSE 只是传输方式，传什么事件由业务合同决定。
- 当前实现不支持 token-by-token answer streaming。
- 当前实现也不持久化运行中事件；刷新后仍看最终 invocation detail。

---

### Q2：`agents.py` 中新增的队列是怎么使用的？

**问题背景：**

`app/api/agents.py` 的 stream route 中新增了：

```python
queue: asyncio.Queue[AgentEvent | None] = asyncio.Queue()
emitter = QueueAgentEventEmitter(queue)
```

**用户困惑：**

这个队列到底存什么？谁往里面放？谁从里面取？为什么 route 里需要它？

**回答要点：**

- 队列是当前一次 SSE 请求内部的事件中转站，不是数据库，也不是持久化历史。
- `produce_events()` 运行 Agent，service/executor 通过 `event_emitter.emit(...)` 产生事件。
- `QueueAgentEventEmitter.emit()` 给事件加 `sequence`，再 `queue.put(AgentEvent(...))`。
- `event_stream()` 通过 `queue.get()` 取事件，编码成 SSE 文本块并 `yield` 给浏览器。
- `produce_events()` 结束时放入 `None` 作为 sentinel，告诉消费者 stream 可以结束。

**复习版理解：**

可以把 `asyncio.Queue` 理解成生产者和消费者之间的传送带。生产者是 `produce_events()`：它调用 `agent_service.run_in_conversation(...)`，Agent 运行到关键节点时就 emit 事件。消费者是 `event_stream()`：它只关心队列里有没有事件，一旦拿到事件就发成：

```text
event: tool_finished
data: {"sequence":4,"type":"tool_finished","payload":{...}}
```

这个设计把业务层和 HTTP streaming 解耦。service/executor 不需要知道 SSE wire format；API route 也不需要知道 Agent 内部什么时候调用 LLM、什么时候调用工具。

**相关代码：**

- `app/api/agents.py`
- `app/core/agent_events.py`
- `app/services/agent_service.py`
- `app/core/agent_loop.py`
- `app/core/native_tool_calling.py`

**易错点：**

- `queue.get()` 队列为空时不会忙等，也不会占 CPU 转圈；它会挂起当前协程。
- `None` 不是事件，它是结束信号。
- `has_failed` 用来避免 service 已经发过 `agent_failed` 后 route 又重复发一次失败事件。

---

### Q3：`asyncio.create_task()` 是不是另外开了一个线程？

**问题背景：**

`event_stream()` 中有一行：

```python
producer = asyncio.create_task(produce_events())
```

**用户困惑：**

这个函数看起来像启动后台任务，它是不是创建了新线程？

**回答要点：**

- `asyncio.create_task()` 不是开新线程。
- 它是在当前 Python 进程的同一个 asyncio event loop 里注册一个协程任务。
- 这里的作用是让 `produce_events()` 先跑起来，同时 `event_stream()` 自己可以继续 `await queue.get()` 等事件。
- 如果直接 `await produce_events()`，会先把 Agent 全部跑完，再开始消费队列，实时 stream 就失效了。

**复习版理解：**

`create_task()` 的关键作用是让两个协程交替推进：

```text
event_stream()
  create_task(produce_events)
  await queue.get()

produce_events()
  run agent
  queue.put(agent_started)

event_stream()
  get agent_started
  yield SSE
```

它的并发是协作式并发，不是多线程并行。真正创建线程通常会看到 `threading`、`asyncio.to_thread()` 或 executor 相关 API。

**相关代码：**

- `app/api/agents.py`

**易错点：**

- `task` 不等于 `thread`。
- `create_task()` 只是把协程交给事件循环调度。
- 如果协程内部执行 CPU 密集或同步阻塞代码，仍然会卡住同一个 event loop。

---

### Q4：`event_stream()` 被 `queue.get()` 挂起了，循环体现在哪里？

**问题背景：**

`event_stream()` 里有：

```python
while True:
    event = await queue.get()
    if event is None:
        break
    yield ...
```

**用户困惑：**

既然 `queue.get()` 在队列为空时会等住，那么循环到底在哪里？它是不是阻塞了？

**回答要点：**

- `await queue.get()` 只挂起 `event_stream()` 这个协程，不阻塞整个线程。
- 循环体现在每次拿到一个事件、`yield` 给浏览器后，又回到 `while True` 顶部等待下一个事件。
- 队列为空时不是一直检查，而是事件循环把这个协程暂停，去调度 `produce_events()`。
- 当 producer `queue.put(event)` 后，等待 `queue.get()` 的消费者会被唤醒。
- 当 producer 放入 `None` 后，消费者读到 `None`，执行 `break`，结束 SSE 响应。

**复习版理解：**

这个循环不是同步代码里的“不断轮询”，而是异步代码里的“每次被唤醒处理一个事件，然后继续等待”。它的节奏大概是：

```text
event_stream: await queue.get()，队列为空，暂停
produce_events: 运行 Agent，put agent_started
event_stream: 被唤醒，yield agent_started，进入下一轮
event_stream: 再次 await queue.get()，继续等
produce_events: put llm_started
event_stream: 被唤醒，yield llm_started
```

**相关代码：**

- `app/api/agents.py`

**易错点：**

- 不要把 `await queue.get()` 理解成阻塞整个应用。
- 不要把异步循环理解成 busy loop；这里没有事件时协程是挂起状态。
- sentinel `None` 是跳出循环的关键。

---

### Q5：什么是 asyncio 事件循环？

**问题背景：**

理解 `create_task()`、`queue.get()`、`queue.put()` 后，需要进一步理解是谁在调度这些协程。

**用户困惑：**

`asyncio` 事件循环到底是什么？它和普通函数调用、线程调度有什么关系？

**回答要点：**

- asyncio 事件循环可以理解成 Python 的异步任务调度器。
- 它维护一批未完成的协程任务，运行当前能继续执行的任务。
- 当某个协程遇到 `await` 并需要等待 I/O、队列或网络响应时，它会让出控制权。
- 事件循环会去运行其他可以继续执行的协程。
- 等等待条件满足后，事件循环再把原协程从暂停位置恢复。

**复习版理解：**

在本模块里，事件循环做的是：

```text
event_stream 在等 queue -> 暂停它
produce_events 还能跑 -> 调度它
produce_events put 事件 -> 唤醒 event_stream
event_stream 发送 SSE -> 再次等待 queue
```

它通常在单线程内完成调度。只要代码在等待网络、队列、sleep 这类 I/O 型操作时使用 `await`，事件循环就能让多个协程交替推进。

**相关代码：**

- `app/api/agents.py`
- `app/services/agent_service.py`
- `app/core/agent_loop.py`

**易错点：**

- `await` 是让出控制权的位置。
- `time.sleep()` 这种同步阻塞调用会卡住事件循环；异步代码里应使用可 await 的 I/O。
- 事件循环擅长 I/O 并发，不等于自动获得 CPU 并行。

---

### Q6：协程和线程有什么区别？

**问题背景：**

用户在操作系统课程中学过：一个进程中可以有多个线程并发调度执行。当前又看到“单线程中有多个协程”，两者看起来很相似。

**用户困惑：**

协程和线程都像多个执行流，它们到底差在哪里？

**回答要点：**

- 线程由操作系统调度；协程由用户态事件循环调度。
- 线程切换通常是抢占式的，OS 可以在很多位置切走；协程通常是协作式的，在 `await` 等位置主动让出。
- 多个线程可以在多个 CPU 核上并行；单个 asyncio event loop 通常只在一个线程里运行。
- 线程开销更大，有独立栈和内核调度成本；协程更轻量，适合大量 I/O 等待任务。
- 线程更适合 CPU 并行或调用阻塞库；协程更适合网络、队列、SSE、异步数据库等 I/O 并发。

**复习版理解：**

可以把线程理解成“OS 调度的执行流”，把协程理解成“应用自己调度的轻量执行流”。两者都能表达并发任务，但线程的切换权在操作系统，协程的切换点主要由程序中的 `await` 决定。

在本模块中，`event_stream()` 和 `produce_events()` 就是两个协程执行流。它们不是两个 OS 线程，而是在同一个 event loop 中交替运行：一个等待队列，另一个运行 Agent 并产生事件。

**相关代码：**

- `app/api/agents.py`

**易错点：**

- 协程有自己的执行状态和局部变量，但不是操作系统线程。
- 协程并发不适合长时间 CPU 计算；CPU 密集任务会占住 event loop。
- 看到“后台 task”不应该马上理解成“新线程”。

## 4. 本次涉及的技术点

| 技术点 | 在项目中的位置 | 为什么需要 | 类似方案 | 复习重点 |
|---|---|---|---|---|
| SSE | `app/api/agents.py`、`app/web/agent_ui.html` | 用 HTTP response 持续发送 Agent 运行事件 | WebSocket、polling | SSE wire format 是 `event:` + `data:`，本次传事件不是 token |
| `asyncio.Queue` | `stream_agent_in_conversation()` | 解耦 producer 和 consumer，让 Agent 内部事件实时转成 HTTP 输出 | 回调直写 response、内存 list、后台消息总线 | `queue.get()` 挂起消费者，`queue.put()` 唤醒消费者 |
| `asyncio.create_task()` | `event_stream()` | 让 `produce_events()` 和 `event_stream()` 在同一 event loop 中并发推进 | 直接 `await`、线程、后台 worker | task 不是 thread；直接 await 会失去实时性 |
| `StreamingResponse` | `app/api/agents.py` | FastAPI 原生支持流式响应，不引入额外依赖 | `sse-starlette`、WebSocket | generator `yield` 的文本块会逐步发给客户端 |
| Emitter 协议 | `app/core/agent_events.py` | service/executor 不依赖 HTTP，实现运行事件的抽象输出 | 直接传 queue、直接写 SSE | 业务层只 emit 事件，API 层负责编码 |
| 协程与事件循环 | `produce_events()`、`event_stream()` | 在等待 LLM、工具、队列时让多个任务交替运行 | 线程池、多进程 | `await` 是让出控制权的位置 |

## 5. 本次代码设计决策

### 决策 1：用 SSE-format over POST，而不是浏览器 `EventSource`

**选择：**

新增 `POST /workspaces/{workspace_id}/conversations/{conversation_id}/agent/stream`，前端用 `fetch(...).body.getReader()` 读取 stream。

**原因：**

当前 Agent 请求需要携带 JSON body，包括 `message`、`max_steps`、`agent_mode`。浏览器原生 `EventSource` 更适合 GET，不适合当前请求形态。

**替代方案：**

可以使用 GET + query 参数、WebSocket、或者引入专门 SSE 库。

**取舍：**

POST + fetch stream 保持了现有 request schema，也不新增依赖；限制是前端要自己解析 SSE 文本块。

### 决策 2：用 `AgentEventEmitter` 抽象事件输出

**选择：**

service/executor 接收可选 `event_emitter`，默认 `None`，现有 JSON endpoint 行为不变。

**原因：**

事件发射点属于 Agent 运行逻辑，但 SSE 编码属于 API 层。用 emitter 可以隔离两者。

**替代方案：**

可以把 queue 直接传进 service/executor，或者让 executor 变成 async generator。

**取舍：**

emitter 方式更轻量，也更容易在测试里用 `CollectingEventEmitter` 收集事件；限制是事件顺序和 payload 需要通过测试维护。

### 决策 3：最终答案仍在 `agent_finished` 中一次性返回

**选择：**

本次只做运行事件 timeline，不做 token streaming。

**原因：**

当前学习重点是 Agent introspection：知道 Agent 什么时候开始、什么时候调用 LLM、什么时候调用工具、什么时候失败。token streaming 会引入 provider 流式协议、文本合并、取消运行和 UI 局部渲染等额外复杂度。

**替代方案：**

可以让 LLM provider 支持 token stream，并把 assistant answer 增量推给 UI。

**取舍：**

当前方案更容易解释和测试；限制是用户仍要等 `agent_finished` 才能看到完整答案。

### 决策 4：用 `None` 作为队列结束信号

**选择：**

`produce_events()` 在 `finally` 中执行 `await queue.put(None)`。

**原因：**

消费者 `event_stream()` 需要知道 producer 已经结束，否则会一直等下一条事件。

**替代方案：**

可以使用单独的 `asyncio.Event`、关闭通道语义，或者把结束事件也建模成普通业务事件。

**取舍：**

`None` sentinel 很简单；需要类型上写成 `asyncio.Queue[AgentEvent | None]`，并在消费端显式判断。

## 6. 踩坑点与注意事项

- `asyncio.create_task()` 不是开线程，只是在同一个 event loop 里注册协程任务。
- `await queue.get()` 不是阻塞整个线程，而是挂起当前协程，让事件循环可以跑 producer。
- `while True` 不是忙等；每次消费一个事件后回到下一轮等待。
- SSE 是传输格式，不代表一定是 token streaming。
- 如果 service/executor 中出现同步阻塞代码，可能导致 event stream 不能及时推送。
- 文档中的事件名必须和 `AgentEventType` 保持一致，否则 UI/API 合同容易漂移。

## 7. 面试可讲版本

可以这样表述：

> 本次我在 mini Agent loop 上加了一版 SSE 事件流，用来展示 Agent 运行中的状态变化，比如开始运行、LLM 调用、工具调用、解析失败和最终完成。
> 设计上我没有让 service 或 executor 直接依赖 HTTP stream，而是抽了一个 `AgentEventEmitter`，API 层用 `asyncio.Queue` 把事件转成 SSE 输出。
> 这里的并发不是多线程，而是 `asyncio.create_task()` 在同一个事件循环里调度 producer 和 consumer：producer 跑 Agent 并放事件，consumer 等队列并把事件发给浏览器。
> 开发过程中我重点理解了 `asyncio.Queue`、事件循环、协程和线程的区别，也明确了当前实现只是事件 timeline，不是 token-by-token 文本流。
> 这个模块让我更清楚地理解了 Agent observability 的边界：先把运行过程透明化，再考虑更复杂的 WebSocket、取消运行或 token streaming。

## 8. 后续 TODO

- [ ] 如果后续做 token streaming，单独设计 token event，不要混淆当前 Agent timeline 事件。
- [ ] 如果后续支持取消运行，需要处理浏览器断开连接时 producer task 的取消语义。
- [ ] 如果工具数量继续增加，可以在事件 payload 中补充 selected tools / available tools 等观测字段。
- [ ] 如果事件需要页面刷新后仍可回放，再考虑事件持久化或从 invocation steps 重建 timeline。

## 9. 建议复习的测试

- `tests/test_agents_api.py::test_agent_stream_endpoint_streams_agent_events`：从 API 层验证 SSE 格式、事件顺序和 `agent_finished` payload。
- `tests/test_agent_loop.py::test_agent_emits_llm_and_tool_events`：验证 ReAct executor 的 LLM/tool 事件顺序。
- `tests/test_agent_service.py::test_agent_service_emits_started_and_finished_events`：验证 service 层负责 `agent_started` 和 `agent_finished`。
- `tests/test_native_tool_calling.py::test_native_executor_emits_tool_events`：验证 native tool calling executor 也走同一套事件合同。
- `tests/test_agent_ui.py::test_agent_ui_route_serves_static_page`：确认 UI 页面包含 stream 读取、SSE parser 和 live timeline 入口。
