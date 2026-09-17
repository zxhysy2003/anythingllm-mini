# 2026-07-11 Agent Tool Artifacts 学习记录

## 2026-07-11 21:25 问题记录：为什么当前最应该统一 Tool artifacts 合同

**问题：**

在后端 Agent 已经具备事件流、tool policy、invocation/step 持久化和调试页之后，为什么
下一步优先做 Tool artifacts 统一合同？它在解决什么问题，又会带来什么好处？

**场景背景（实现前）：**

- 当时 `ToolResult` 使用 `content` 表达给 Agent 下一轮推理使用的 observation，使用无约束的
  `data: dict[str, Any]` 保存额外数据。
- `workspace_document_search` 当时通过 `data["sources"]` 返回结构化引用。
- `AgentService` 当时需要主动识别 `data["sources"]`，再把 sources 汇总到 assistant message；
  invocation step 则保存完整 `tool_result`。
- 这说明项目已经出现结构化工具产物，但当时仍依赖约定俗成的“魔法 key”，尚未形成显式
  artifact contract。
- 当时只更新了 post-V4 路线图，Tool artifacts 合同还没有实现。

**旧合同中 `content` 与 `data` 的职责：**

| 字段 | 当前主要消费者 | 含义 |
| --- | --- | --- |
| `content: str` | Agent / LLM | 工具给模型阅读的 observation；成功结果和失败说明都会进入下一轮推理 |
| `data: dict[str, Any]` | service、持久化、API、调试页 | 结构化附加数据；当前不会自动作为 observation 发给模型 |

- `calculator` 会返回 `content="7"` 和 `data={"result": 7}`：前者让模型继续回答，后者保留
  机器可读结果。
- `workspace_document_search` 会把检索摘要放进 `content`，把完整结构化引用放进
  `data["sources"]`。
- 旧 `data` 同时承载工具产物和 validation / policy 等诊断信息，因此不能简单把整个
  `data` 重命名为 `artifacts`；需要先明确哪些字段属于稳定产物合同。

**回答要点：**

- 如果继续使用无约束 `data`，未来每个工具都可能自行定义 `sources`、`summary`、`outputs`、
  `result` 等字段，service、API 和调试页需要分别理解每个工具的私有格式。
- 显式 artifact model 可以统一 `sources` 和轻量 `outputs` 的类型、序列化和安全边界，减少
  工具与 `AgentService` 之间的隐式耦合。
- 统一合同能明确 conversation message 和 invocation detail 的职责：普通聊天历史只保存最终
  消息和展示所需 sources，完整且安全的工具产物保存在 invocation detail 中。
- replay / fixture 导出需要稳定的数据语义；如果只导出形状不确定的 JSON 字典，就难以形成
  通用的 parser、tool registry 和 metrics 回归样例。
- 长文档总结也会产生 sources、分块结果或部分结果，因此应先打稳 artifact contract，再增加
  复杂工具，避免临时字段固化后再迁移。

**复习版理解：**

Tool artifacts 统一合同不是为了让当前 `calculator` 或文档搜索变得更聪明，而是在新增复杂
工具前，把“工具返回什么”说清楚。旧 `ToolResult.data` 像一个自由口袋，虽然能装任何内容，
但下游不知道里面有哪些字段、哪些可以展示、哪些可以持久化、哪些可能泄露内部路径。统一后，
Agent loop 读取 `content` 作为 observation，后端基础设施读取经过校验的 `sources` / `outputs`
作为 artifact。这样工具、service、API、持久化、调试页和 replay 可以围绕同一个稳定合同工作。

**规划时的行为与目标边界：**

- 规划时已实现：document search sources、assistant message sources、invocation step tool result、
  `agent_ui.html` sources/step 展示。
- 规划时未实现：显式 artifact model、轻量 outputs 合同、统一安全序列化和通用 artifact inspector。
- 第一版只支持 `sources` 和 JSON-safe `outputs`；执行时选择直接替换旧 API 并重置本地数据。
- 第一版不做二进制附件、文件生成、新数据库表、后台任务或产品前端。
- 任意本地路径、隐藏文件路径、内部解析路径不应进入 Agent API、持久化 artifact 或调试页。

**相关文件：**

- `backend/app/tools/registry.py`：当前 `ToolResult` 定义。
- `backend/app/tools/document_tools.py`：旧 `data["sources"]` 的生产位置。
- `backend/app/services/agent_service.py`：sources 提取、assistant message 和 invocation step 持久化。
- `backend/app/api/schemas/agents.py`：当前 Agent tool result 对外 schema。
- `backend/app/web/agent_ui.html`：当前 sources 和 tool result 调试展示。
- `docs/design/post-v4-agent-learning-roadmap.md`：Tool artifacts 的状态、优先级和建议边界。

**易错点：**

- 不要把“已经有 `data["sources"]`”误认为“已经有统一 artifact contract”。前者只是一个工具
  和 service 之间的隐式约定。
- 不要把 observation 和 artifact 混为一谈：observation 面向模型推理，artifact 面向系统消费。
- 不要为了统一合同过早设计复杂继承体系；当前两个工具只需要最小、显式、可验证的结构。
- 不要把 tool policy 的 approval/error metadata 全部强行归入 artifact，它们属于不同语义边界。

**面试可讲版本：**

> 在 Agent 工具系统中，我发现工具除了返回给模型阅读的 observation，还会返回 sources 等
> 结构化结果。项目最初把这些内容放在无约束的 `data` 字典里，导致 service 需要识别固定的
> 字段名。我计划把 observation 和 artifact 分开，并先统一 sources 和轻量 outputs 的合同，
> 这样持久化、API、调试页和 replay 都能使用同一份经过校验的数据结构，同时避免内部路径被
> 意外暴露。这个改动不会增加 Agent 自治能力，而是在新增复杂工具前先稳定后端边界。

**后续可复习关键词：**

- `ToolResult`
- `content` vs `data`
- observation vs artifact
- `sources` / `outputs`
- Pydantic contract
- invocation detail
- conversation history boundary
- JSON-safe serialization
- replay fixture
- path safety

## 实现记录：第一版 Tool artifacts 合同

本轮已经把前面的设计判断落到后端代码中：

- `ToolResult.data` 已直接替换为 `artifacts` 和 `error_details`，不保留旧 API 兼容层。
- `content` 仍是两个 executor 唯一传给 LLM 的 observation。
- calculator 使用 `artifacts.outputs={"result": ...}`；document search 使用类型化
  `artifacts.sources`。
- validation 和 tool policy 的结构化失败信息迁入 `error_details`。
- outputs 使用最多 20 项、总长度最多 8,000 字符的 JSON-safe 命名映射，并拒绝本地路径。
- AgentService、API、JSON step persistence、SSE 和 `agent_ui.html` 已统一使用新合同。
- assistant message 仍只保存最终答案和展示所需 sources，不保存完整 artifact。
- `agent_steps.tool_result` 本身仍是 JSON 列，因此不需要新增 Alembic revision。

这次选择了直接替换旧合同，并重置本地数据库和 storage，而不是为学习项目维护双写和历史
读取适配。这样可以把注意力放在清晰的新边界上，但也意味着旧 invocation 不再保留。

## 2026-07-13 问题记录：Tool artifacts 的 Pydantic 模型边界

**问题：**

`artifacts.py` 中的 `BaseModel`、`ConfigDict`、`Field`、`JsonValue` 和
`ValidationError` 分别做什么？为什么继承 `BaseModel` 后通常不用手写 `__init__()`？以后是否
所有类都应继承它？路径规则里的正则是否应引入更易读的第三方 DSL？

**回答要点：**

- `artifacts.py` 不是工具执行器，而是工具结构化产物的合同和安全边界：`sources` 保存类型化
  文档引用，`outputs` 保存受限的 JSON 结果；它们随后进入 step 持久化、API、SSE 和调试页。
- 当前使用的 Pydantic 2 中，`BaseModel` 提供构造、类型校验、嵌套模型处理和
  `model_dump()` 等序列化入口。类型注解相当于声明构造参数和数据合同。
- `ConfigDict(extra="forbid")` 拒绝未声明字段，防止 `upload_path`、`parsed_path` 等内部字段
  被悄悄带进 artifact；`Field` 表达长度、数值范围和 `default_factory` 默认集合；
  `JsonValue` 将 outputs 限制为 JSON 形状，额外的 `json.dumps(..., allow_nan=False)` 保证严格 JSON。
- validator 内部抛出 `ValueError`，调用模型的一侧收到的是汇总后的 `ValidationError`；registry
  将工具输入校验失败转为 `error_details`，而不是让整个 Agent run 直接崩溃。
- `BaseModel` 适合 API schema、tool input/result、artifact 和配置等“数据必须长成什么样”的边界；
  `AgentService`、`ToolRegistry`、工具实现和 executor 的重点是行为、依赖与运行状态，应保持普通
  class；数据库表使用 `SQLModel`，轻量可信内部状态可使用 `dataclass`。
- 路径识别的重点是“拒绝看起来像本地路径的输出”，不是验证路径是否合法。`pathvalidate` 更适合
  路径/文件名合法性校验，不能直接替代当前的拒绝逻辑；`VerbalExpressions` 类 DSL 对少量规则
  增益不大。后续若要重构可优先用 `PureWindowsPath`、`urlsplit()` 和命名清楚的小函数，复杂正则
  再使用标准库 `re.VERBOSE` 加注释。

**复习版理解：**

Pydantic 不是“所有类的基类”，而是数据边界的守门员。`ToolArtifacts` 继承 `BaseModel`，是因为
工具结果既要跨层传递又要序列化，必须在产生时就校验；`AgentService` 不继承它，是因为 service
主要负责调度工作。`BaseModel` 已有通用 `__init__`：根据字段注解构建校验流程、处理默认值并执行
validator，所以一般把业务规则放在 `field_validator`，而不是覆盖构造函数。

**相关代码：**

- `backend/app/tools/artifacts.py`：artifact 模型、字段约束、路径安全校验。
- `backend/app/tools/registry.py`：`ToolResult` 和 `ValidationError` 到 `error_details` 的转换。
- `backend/app/tools/document_tools.py`：把检索 chunk 转为 `ToolSourceArtifact`。
- `backend/tests/test_tool_artifacts.py`：默认值、额外字段、JSON、路径和文件名边界测试。

**易错点：**

- 不要把 `JsonValue` 误解为严格 JSON 序列化的全部保证；`NaN`/`Infinity` 仍应由显式序列化检查拒绝。
- 不要为了字段清洗或校验而直接重写 Pydantic 的 `__init__()`；优先使用 field/model validator。
- 不要因为一个类包含数据就继承 `BaseModel`；先区分它是数据合同、业务服务还是数据库模型。
- 不要把路径合法性校验和“禁止泄露本地路径”的安全策略混为一谈。

**面试可讲版本：**

> 我在 Agent 工具返回值处使用 Pydantic 建立了 artifact 合同：来源引用和 JSON outputs 会在生成时
> 校验，再交给持久化、API 和调试页。这里我把 Pydantic 放在数据跨层传递的边界，而没有让 service
> 和 registry 也继承模型类；这样既能得到类型、序列化和安全校验，又保持业务编排代码的职责清晰。

**后续可复习关键词：**

- `BaseModel`
- `ConfigDict(extra="forbid")`
- `Field(default_factory=...)`
- `JsonValue`
- `ValidationError`
- `field_validator`
- DTO / service / ORM boundary
- strict JSON
- path validation vs path leakage policy
