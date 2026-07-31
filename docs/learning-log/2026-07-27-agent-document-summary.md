# 2026-07-27 模块学习记录：Agent 长文档总结工具

## 1. 本次开发模块

- 模块名称：Agent 长文档总结工具
- 开发目标：给 Workspace Agent 增加一个只读、低风险的文档发现与全文总结工具
- 当前阶段：Post-V4 capability-line iteration
- 能力线：Document lifecycle、RAG quality、Agent capabilities、Safety and boundaries
- 影响阶段：V1 文档解析、V2 source/citation、V4 Agent loop，以及跨阶段 tests/docs
- 相关分支：`feat/monorepo-backend`

核心文件：

- `backend/app/services/document_summary_service.py`
- `backend/app/services/document_service.py`
- `backend/app/services/workspace_document_service.py`
- `backend/app/tools/document_tools.py`
- `backend/app/core/agent_loop.py`
- `backend/app/core/native_tool_calling.py`
- `backend/app/services/agent_replay_service.py`

## 2. 本次代码改动概览

本次主要完成：

1. 新增默认工具 `workspace_document_summary`，支持 `list` 和 `summarize`。
2. 小文档单次调用摘要模型，长文档顺序执行 Map + Reduce。
3. 最多处理前 8 个 section，并用 `completion_status`、`stop_reasons` 表达 partial。
4. 安全读取当前 workspace 的 parsed text，不把磁盘路径放入 ToolResult。
5. 每个实际处理的 section 形成 direct source，`score=null`。
6. SSE 增加 `tool_progress`，但一次工具调用仍只持久化为一个 AgentStep。
7. 扩展 replay fixture，保存 summary outputs、sources 和 nullable score，不重放实时 progress。
8. 补充文件名 selector、artifact 预算、模型失败、路径校验和 replay 匿名化测试。
9. 对 partial 最终回答增加确定性的覆盖声明兜底。

| 文件 | 作用 | 本次变化 |
|---|---|---|
| `document_summary_service.py` | 摘要 pipeline | 分块、Map、Reduce、partial、截断和调用计数 |
| `document_tools.py` | Agent 工具 contract | list/summarize 输入、输出、错误和 source artifacts |
| `document_service.py` | 文件安全读取 | parsed path、UTF-8、非空和字符数校验 |
| `workspace_document_service.py` | workspace 数据访问 | 按 ID/展示文件名解析文档 |
| `agent_events.py` | 事件适配 | 把工具进度映射成 Agent SSE 事件 |
| `agent_executor.py` | 最终回答 contract | 根据 partial artifacts 追加覆盖声明 |
| `agent_replay_service.py` | replay | 文档 ID 匿名化和 summary fixtures |
| `safe_strings.py` | 字符串安全规则 | basename 校验、路径识别和展示名选择 |

## 3. 本次关键问题与回答

### Q1：长文档总结只是新增一个工具吗？会不会改变 Agent 主流程？

**用户困惑：**

工具内部要调用最多 9 次 LLM，看起来像新增了一套 Agent 流程。

**回答要点：**

- 对外仍只是一个 `workspace_document_summary` 工具。
- Agent executor 仍按“LLM 决策 → 工具调用 → observation → 下一次 LLM”运行。
- Map + Reduce 是工具内部实现，不产生多个 AgentStep。
- 工具内部 LLM 调用数放在 artifacts，外层 `llm_call_count` 仍只统计 executor 调用。
- 没有增加新的 invocation lifecycle 状态、数据库表或后台任务。

**复习版理解：**

工具边界看 ToolResult 和 AgentStep，而不是看工具内部做了多少工作。只要内部工作最终收敛成一次
ToolResult，对 Agent loop 来说它仍是一次普通工具调用。

---

### Q2：Map 和 Reduce 是什么意思？

**回答要点：**

- Map：按原始顺序分别总结每个 section。
- Reduce：把 section summaries 合并成一份连贯的最终摘要。
- 当前实现顺序执行 Map，不并行，因此调用顺序、进度和失败位置容易解释。

**复习版理解：**

Map 解决“单次上下文放不下全文”，Reduce 解决“多个局部摘要不是一份完整回答”。这里借用了
MapReduce 的思想，但不是分布式计算框架。

---

### Q3：`validate_safe_basename()` 和 Unicode category 校验解决什么问题？

**回答要点：**

- basename 不能包含 `/`、`\`，不能是隐藏文件名，也不能是空字符串。
- `unicodedata.category(character).startswith("C")` 拒绝控制字符、格式字符、代理字符等。
- `Zl`、`Zp` 分别是 Unicode 行分隔符和段落分隔符。
- 这能防止换行、制表符和双向文本控制符破坏 artifact、日志或 Agent observation 的结构。

**易错点：**

“安全 basename”只表示路径和结构安全，不表示它在 LLM 语义上安全。合法文件名仍可能是一句
prompt injection 指令。

---

### Q4：“不进入新的 invocation lifecycle 状态”是什么意思？

**回答要点：**

- 当前 invocation 状态仍是 `completed`、`max_steps_reached`、`needs_input`。
- summary partial 是 ToolResult 的完整性，不是 Agent invocation 的运行状态。
- partial 通过 `completion_status` 和 `stop_reasons` 表达，并保持 `ok=true`。
- 只有没有任何可用摘要时才返回工具失败。

**复习版理解：**

“工具结果是否完整”和“Agent 调用是否仍在运行”是两个维度。不要为了表达 section 没处理完，
就增加一个全局 lifecycle 状态。

---

### Q5：`_paragraph_spans()`、`match.start()/end()`、`_select_chunks()` 各做什么？

**回答要点：**

- `_paragraph_spans()` 用段落分隔正则产生原文中的 `(start, end)` 半开区间。
- `match.start()` 是匹配文本的起始下标，包含在匹配中。
- `match.end()` 是匹配结束后的下一个下标，不包含在匹配中。
- `_scan_chunks()` 是真正的统一扫描实现。
- `_select_chunks()` 固定传入 `max_chunks`，表达“摘要只保留有界前缀”的业务语义。
- `split_text()` 传入 `None`，用于测试或需要完整分块的场景。

**易错点：**

“薄函数”不一定没有价值。只要它固定了调用策略、表达了业务意图并防止调用者传错参数，就有存在意义。

---

### Q6：为什么要验证 summary 字符限制和 `SUMMARY_TRUNCATION_MARKER`？

**回答要点：**

- `section_summary_chars` 和 `final_summary_chars` 必须大于 0。
- 字符上限还必须比截断标记长，否则无法同时保留正文和显式截断标识。
- 模型超限后不能静默切断，必须追加 marker 并加入 `summary_output_limit`。

**复习版理解：**

输出限制不只是防止内容过长，也是一项用户可见 contract。发生截断时，系统必须承认结果不完整。

---

### Q7：`self.model_fields_set` 在输入校验中有什么作用？

**回答要点：**

- 它记录调用者在输入中实际提供了哪些字段。
- 字段“没有提供”和“明确提供为 null”在 selector contract 中不是一回事。
- `list` 即使收到 `document_id: null`，也应该判定调用者携带了 selector 并拒绝。
- `summarize` 必须实际提供且只提供一个非 null selector。

---

### Q8：为什么 list 循环中尝试构造 `ToolArtifacts`？`_summary_outputs()` 又做什么？

**回答要点：**

- list 不仅受 20 项数量限制，还受 8000 字符的完整 JSON artifact 预算限制。
- 每加入一项后构造 `ToolArtifacts`，可以复用通用输出名、路径和序列化预算校验。
- `_summary_outputs()` 负责把 service result 转换成稳定的结构化输出。
- 若多个 chunk summaries 合并后超过预算，它会统一降低每段摘要上限，标记
  `summary_output_limit`，并重建 partial content。
- 最终摘要只放在 ToolResult content，outputs 只保存 section summaries 和元数据，避免重复。

---

### Q9：`document_tools.py` 主要承担了哪些职责？

**回答要点：**

- 保留原有 workspace document search 工具。
- 新增 summary 输入 schema 和工具实现。
- 在工具边界检查 workspace、session 和剩余 Agent step。
- 把 workspace/document service 异常映射成稳定错误码。
- 组装 list outputs、summary outputs 和 direct source artifacts。
- 复用同一个展示文件名规则，保证 search、list、filename selector 和 citation 一致。

**易错点：**

工具层不应自己读取任意路径，也不应把数据库 session、`parsed_path` 或 `upload_path` 暴露给模型。

---

### Q10：为什么文件名安全问题连续出现？

**回答要点：**

- 最初更关注路径安全，没有完整区分存储名、原始名、展示名、selector 和 LLM metadata。
- 同一个文件名进入数据库、文件系统、artifact、Agent observation 和 LLM prompt 时，安全要求不同。
- `safe basename` 能解决路径穿越和控制字符，不能解决自然语言形式的 prompt injection。
- 当前已经统一 display filename 和 selector，但外层 Agent 仍可能在 ToolResult observation 中看到文件名。

**复习版理解：**

字符串不是经过一次 sanitizer 就能在所有上下文安全。应该先画清数据流和信任边界，再为不同使用场景
选择存储、规范化、编码、展示或完全不传递。

## 4. 本次涉及的技术点

| 技术点 | 在项目中的位置 | 为什么需要 | 类似方案 | 复习重点 |
|---|---|---|---|---|
| Map + Reduce | `DocumentSummaryService` | 有界处理长文档 | 层级摘要、滑动窗口 | Map/Reduce 各自解决什么问题 |
| Pydantic cross-field validation | `WorkspaceDocumentSummaryInput` | 表达 action/selector 互斥 | 多个输入 model | `model_fields_set` 与 null |
| Runtime-only dependency | `ToolContext.session` | 工具需要当前事务 | repository factory | `exclude=True` 不进入持久化 |
| Path containment | `read_parsed_text()` | 防止读取 workspace 外文件 | capability handle | `resolve()` 与 `is_relative_to()` |
| Partial contract | summary result/artifacts | 区分可用部分结果和失败 | 新 lifecycle 状态 | 工具状态和 invocation 状态分离 |
| Progress adapter | `AgentToolProgressReporter` | 工具不依赖 SSE 实现 | callback/event bus | progress 不持久化 |
| Nullable citation score | `ToolSourceArtifact`、`RAGSource` | direct source 没有相似度 | 伪造 1.0 | null 的真实语义 |
| Deterministic replay | `AgentReplayService` | 不重新调用工具和 LLM | integration rerun | fixture 保存边界而非实时过程 |

## 5. 本次代码设计决策

### 决策 1：单工具双 action

选择 `workspace_document_summary` 同时支持 `list` 和 `summarize`，让 Agent 先发现文档，再按 ID 或
展示文件名总结，不增加两个高度相关的默认工具。

### 决策 2：最多处理前 8 个 sections

当前目标是学习有界长任务，不引入后台恢复和无限成本。超限时返回可解释 partial，而不是假装全文完成。

### 决策 3：工具内部调用不进入 Agent metrics

`summary_llm_call_count` 属于工具 artifacts；Agent `llm_call_count` 继续表示 executor 决策次数，
避免改变已有 metrics 语义。

### 决策 4：direct source 使用 `score=null`

全文直接读取没有检索相似度，使用 null 比伪造 1.0 更准确，同时保留 section text 作为 citation。

### 决策 5：不新增数据库迁移

第一版把“数据库记录存在且 parsed file 校验通过”视为可总结，不新增 document status、task 表或
checkpoint 字段。

### 决策 6：replay 不保存 progress timeline

progress 是瞬时 UI 状态；replay 只验证最终 step、artifacts、sources 和 metrics，事件顺序由
executor/API 测试覆盖。

## 6. 踩坑点与注意事项

- 原始文件名合法，不代表适合直接进入 LLM prompt。
- list 返回的展示名必须能被 filename selector 精确解析。
- `stored_filename` 不能参与同名歧义判断，除非它就是对外展示名。
- 文件名长度要在调用摘要模型前确定，不能等 source artifact 构造时才失败。
- artifact 预算按 JSON 序列化后的字符数计算，转义字符会放大长度。
- `partial` 应保持 `ok=true`，否则会错误增加 `failed_step_count`。
- 直接读取的 source 没有检索 score。
- replay 匿名化要覆盖 action input、outputs、content、observation、interaction 和最终 answer。
- 对 partial 的最终回答兜底不能永远扫描历史 partial；同一文档重试成功后，应以最后一次结果为准。

## 7. 面试可讲版本

> 我为学习项目增加了一个 Workspace 长文档总结工具。工具先安全读取当前 workspace 的 parsed
> text，小文档直接总结，长文档按段落优先分块并顺序执行 Map + Reduce，同时限制最多处理前
> 8 段。对于超限和中途模型失败，我没有增加新的 Agent 状态，而是通过 ToolResult artifacts
> 返回明确的 partial、覆盖范围和 stop reasons。开发过程中最值得复盘的是文件名安全：路径安全、
> 展示安全和 LLM prompt 安全是不同问题，后续需要从数据流和信任边界重新设计。

## 8. 后续 TODO

- [x] 从头梳理文件名信任边界；结果见
  `docs/design/document-filename-trust-boundaries.md`。
- [x] summary/search ToolResult observation 改用 document ID，展示名仅在 list/source/UI 出现。
- [ ] partial disclosure 按 `document_id` 使用最后一次总结结果，覆盖失败后成功重试。
- [x] 为文件名信任边界建立上下文矩阵和集中测试。
- [x] 重构前的长文档总结功能已保留为 commit `9c64790`。
