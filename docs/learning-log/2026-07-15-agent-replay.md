# 2026-07-15 Agent Replay 学习记录

## 1. 本次开发模块

- 模块名称：Agent replay 和评估样例第一版
- 开发目标：把已持久化的 Agent invocation 转为本地 JSON fixture，并在不请求 LLM、
  不执行工具的前提下重放确定性工程边界。
- 当前阶段：V4 后的 Observability and evaluation、Developer experience and documentation。
- 相关分支：`feat/monorepo-backend`

## 2. 本次代码改动概览

- 新增 `AgentReplayService`：导出 invocation、匿名化 source document ID、加载严格 fixture，
  并检查 parser、registry 输入校验、ToolResult、source 快照、status 和可推导 metrics。
- 新增 export/replay 两个本地 CLI，以及三个不含真实用户数据的合成 fixture。
- 更新路线图和 Agent stage note，将 replay 标记为第一版完成；补充独立技术说明。
- 补充 Chroma/RAG 技术笔记，并删除已不再维护的 monorepo 布局说明。

## 3. 本次关键问题与回答

### Q1：Agent replay 是什么，为什么不是“LLM 答案稳定性测试”？

**回答要点：**

- fixture 是固定 JSON 样例，来源可以是一次真实 invocation 的本地导出，也可以是提交到仓库的
  人工脱敏合成样例。
- replay 只检查确定性边界：`react_text` parser、当前工具是否存在、Pydantic 输入校验、artifact
  sources 与 assistant sources 快照、以及从 steps 重新计算的指标。
- 不重新调用 LLM、不运行 `ToolRegistry.run()`，所以不执行工具，也不会重新计算 policy approval。
- `llm_call_count`、`total_latency_ms` 和最终自然语言答案保留为历史证据，而不是硬断言。

**复习版理解：**

Agent replay 是把一次运行的“可确定部分”变成回归资产。模型的自然语言生成可能变化，但 parser
能否读懂协议、工具输入能否通过 schema、sources 是否在持久化链路中保持一致，这些都应在代码改动
后继续成立。

### Q2：fixture 的 `sources` 和 `tool_result.artifacts.sources` 为什么要同时存在？

**回答要点：**

- `tool_result.artifacts.sources` 是单个工具步骤产生的来源证据。
- fixture 顶层 `sources` 是最终 assistant message 保存的 sources 快照，面向聊天历史和展示。
- `AgentService` 会按照 steps 的顺序汇总 artifact sources 后保存 assistant message；replay 再比对
  两者，验证这个跨层传递没有漂移。

### Q3：`action`、parser 和 `model_validate()` 分别检查什么？

**回答要点：**

- `action` 是模型请求调用的工具名，例如 `calculator`；`react_text` 的最终回答不保存为 step。
- parser 将 `Action: ...` / `Action Input: ...` 文本协议解析为 action 和字典输入；没有 action 的
  已持久化 React step 必须是失败的 parser 结果。
- `tool.input_model.model_validate(step.action_input)` 只验证工具输入合同。例如 calculator 的
  `CalculatorInput.expression` 会执行 `Field(min_length=1)`；它不会执行工具，也不会触发 policy。

### Q4：CLI 的 `argparse` 和退出码为什么值得单独设计？

**回答要点：**

- `parser.add_argument()` 声明位置参数 `invocation_id`、必填 `--output`，以及布尔开关
  `--overwrite`；`type=Path` 会将路径字符串提前转换为 `Path`。
- export 成功返回 `0`，fixture 与当前边界不匹配的 replay 返回 `1`，文件、合同或运行错误返回 `2`。
- `main(argv=None)` 让真实终端调用和单元测试共享同一参数解析逻辑；`SystemExit(main())` 将返回值
  交给 shell。

## 4. 本次代码设计决策

### 决策 1：严格 fixture 合同放在独立 service

使用 Pydantic fixture model 且 `extra="forbid"`，使未知字段、未知版本或不合法 step 在加载时失败。
服务层复用 export/load/replay helper，CLI 只做参数、输出和退出码适配；因此未来测试或维护命令不会
各自复制匿名化与校验规则。

### 决策 2：来源 ID 仅在 fixture 内匿名化

导出时移除数据库关联 ID 与时间戳，并把 source `document_id` 映射为 `document_1` 等稳定别名；
同一文档在 step artifacts 与 assistant snapshot 中使用同一别名。原始 user message、LLM 文本和
source 文本为 replay 保留，但只允许输出到显式本地路径，不能直接提交。

### 决策 3：export CLI 不因缺失 SQLite 而创建空数据库

SQLite 默认会在连接缺失路径时创建文件。导出前先确认本地 SQLite 文件存在，避免一个应当只读的
维护命令留下空数据库，再以清晰的 JSON error 和 exit code `2` 失败。非 SQLite 数据库不走该
文件检查。

## 5. 踩坑点与注意事项

- 不要把 `native_tool_calling` 的 parser 标记为失败；它没有 React 文本协议，正确状态是
  `not_applicable`。
- 不要把 policy 当作 replay 的当前断言。第一版仅保留 policy ToolResult 作为历史证据，避免在
  缺少 workspace/conversation/approval 上下文时假装能安全重算。
- fixture 中的顶层 sources 不是冗余数据，而是用于验证最终消息快照是否忠实汇总 tool artifacts。
- 正常打开一个不存在的 SQLite URL 可能创建文件；“不写 SQL”不等于“没有副作用”。
- 导出的真实 fixture 仍可能包含业务文本，提交前必须人工提炼为合成测试样例。

## 6. 面试可讲版本

> 我在一个 FastAPI Agent 学习项目里，把已持久化的 invocation 和 steps 做成了本地 replay 能力。
> 导出时会移除数据库关联 ID 和时间戳，并对文档 ID 做 fixture 内匿名化；回放时不请求 LLM、也不
> 执行真实工具，只检查文本协议 parser、工具输入 schema、sources 快照和可由 steps 推导的指标。
> 这样把一次线上或本地失败运行转成可提交的合成回归样例，同时避免把模型自然语言输出的一致性误当
> 成确定性测试。实现中我还处理了 SQLite 缺库时会意外创建空文件的只读边界。

## 7. 相关文件与后续复习

- `backend/app/services/agent_replay_service.py`
- `backend/app/maintenance/export_agent_replay.py`
- `backend/app/maintenance/replay_agent_fixture.py`
- `backend/tests/test_agent_replay_service.py`
- `backend/tests/test_agent_replay_cli.py`
- `docs/tech-notes/agent-replay.md`

后续优先级：澄清问题工具和长文档总结工具；新增状态时应同时增加合成 replay fixture。
