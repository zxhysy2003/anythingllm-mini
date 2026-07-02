# 2026-07-01 模块学习记录：Agent Tool Calling

## 2026-07-01 15:04 问题记录：`__init__.py` 与 `__all__`

**问题：**

在实现 V4 工具系统时，`app/tools/__init__.py` 为什么不像有些目录那样保持空文件，而是集中导入了 `ToolRegistry`、`ToolContext`、`CalculatorTool` 等对象？其中的 `__all__` 是什么意思？为什么项目中有些 `__init__.py` 有内容，有些却没有内容？

**回答要点：**

- `__init__.py` 首先可以让目录成为 Python package，使项目可以稳定使用 `app.tools`、`app.models` 这类包路径导入。
- 空的 `__init__.py` 通常只是包标记，表示这个目录目前不需要对外提供统一入口。
- 有内容的 `__init__.py` 通常承担“公共出口”职责，把包内常用对象集中暴露出来。
- `app/tools/__init__.py` 有内容，是因为 V4 工具系统已经形成明确子系统，后续 Agent Loop 可以直接从 `app.tools` 导入公共接口。
- `__all__` 声明这个包希望对外暴露的公共名字，主要影响 `from app.tools import *`，也起到文档化公共 API 的作用。
- `__all__` 不是权限控制；不在 `__all__` 里的对象仍然可以通过具体模块路径导入。

**复习版理解：**

可以把 `__init__.py` 理解成一个包的入口文件。空文件表示“这个目录是一个包，但暂时没有统一门面”。非空文件表示“这个包想对外提供更稳定、更方便的导入入口”。

在本项目里，`app/tools` 是 V4 Agent Loop 的工具层边界。它内部拆成了 `registry.py`、`calculator.py`、`document_tools.py`，但外部代码不一定应该关心这些内部拆分。通过 `app/tools/__init__.py` 统一导出后，后续代码可以写：

```python
from app.tools import ToolRegistry, ToolContext, CalculatorTool
```

而不是到处写：

```python
from app.tools.registry import ToolRegistry, ToolContext
from app.tools.calculator import CalculatorTool
```

这让工具系统的公共 API 更清晰，也降低了后续重排内部文件时影响外部调用方的概率。

**相关文件：**

- `app/tools/__init__.py`
- `app/tools/registry.py`
- `app/tools/calculator.py`
- `app/tools/document_tools.py`
- `app/models/__init__.py`
- `app/services/__init__.py`

**易错点：**

- 不要把 `__all__` 理解成访问权限控制；它只是公共导出名单。
- 不要所有目录都机械地在 `__init__.py` 里集中导出对象；这样可能增加循环导入风险。
- 判断是否需要非空 `__init__.py`，要看这个目录是否已经形成稳定子系统，是否值得提供统一导入入口。
- 对普通内部目录，空 `__init__.py` 往往更简单、更安全。

**后续可复习关键词：**

- Python package
- `__init__.py`
- `__all__`
- public API
- import boundary
- Agent tool registry

## 2026-07-01 21:29 问题记录：工具模块循环导入

**问题：**

运行 `pytest -q tests/test_calculator_tool.py` 时，测试收集阶段报错：

```text
ImportError: cannot import name 'ToolContext' from partially initialized module 'app.tools'
```

本次问题是：为什么 `calculator.py` 从 `app.tools` 导入 `ToolContext` / `ToolResult` 会导致循环导入？修复时为什么要改成从 `app.tools.registry` 直接导入？

**回答要点：**

- `app/tools/__init__.py` 是 `app.tools` 包的对外门面，会集中导出 `CalculatorTool`、`ToolContext`、`ToolResult` 等公共对象。
- Python 导入 `app.tools.calculator` 时，会先初始化包 `app.tools`，也就是执行 `app/tools/__init__.py`。
- `__init__.py` 第一行导入 `CalculatorTool`，进入 `calculator.py`；但 `calculator.py` 又反过来写 `from app.tools import ToolContext, ToolResult`。
- 此时 `app.tools` 还没有执行完，`ToolContext` 和 `ToolResult` 还没有被导出到包门面上，所以 Python 报 `partially initialized module`。
- 修复方式是让包内部模块直接依赖真实定义来源：`from app.tools.registry import ToolContext, ToolResult`。
- 更准确的判断规则是：包内部模块不要绕回自己的 `__init__.py` 拿东西；包外调用方可以通过 `__init__.py` 使用这个包暴露出来的公共 API。

**复习版理解：**

`__init__.py` 可以理解成一个包的“对外入口”，适合让包外代码用更稳定的方式导入公共对象。例如外部模块可以写 `from app.tools import CalculatorTool`，把 `app.tools` 当成一个整体使用。

但 `calculator.py` 本身就在 `app.tools` 包里面，如果它再从 `app.tools` 这个门面导入对象，就相当于内部模块反过来依赖自己的包入口。初始化顺序一旦变成 `__init__.py -> calculator.py -> app.tools`，就可能在包还没初始化完时访问尚未导出的名字，形成循环导入。

所以本项目里更稳的写法是：

```python
from app.tools.registry import ToolContext, ToolResult
```

这表示 `calculator.py` 直接依赖 `registry.py` 中的真实定义，不经过包门面。`document_tools.py` 也是同样模式。

**相关文件：**

- `app/tools/__init__.py`
- `app/tools/calculator.py`
- `app/tools/registry.py`
- `app/tools/document_tools.py`
- `tests/test_calculator_tool.py`
- `tests/test_tools_registry.py`

**易错点：**

- 不要把“是否相关”作为主要判断标准；更关键的是“当前模块在包内还是包外”。
- 包内部模块之间依赖时，优先导入具体模块路径，例如 `app.tools.registry`。
- 包外调用方使用 `app.tools` 这种门面导入是合理的，因为它不参与 `app.tools` 自己的初始化过程。
- `partially initialized module` 经常意味着循环导入，而不是目标类或函数真的不存在。
- 单测在 collection 阶段就失败，通常说明导入链路本身有问题，还没进入具体测试逻辑。

**后续可复习关键词：**

- circular import
- partially initialized module
- package facade
- `__init__.py`
- public API
- direct module dependency
- pytest collection

## 2026-07-02 20:33 问题记录：ReAct 文本协议

**问题：**

什么是 ReAct 文本协议？结合 `anythingllm-mini` 当前 V4 Agent Loop 应该怎么理解？

**回答要点：**

- ReAct 可以理解成 Reason + Act 的 agent 执行模式：模型先决定下一步要不要行动，行动后拿到 observation，再继续判断，直到给出最终答案。
- 本项目里的“文本协议”不是 HTTP 协议，也不是 provider-native tool calling，而是约定 LLM 必须输出固定文本格式。
- 如果模型要调用工具，需要输出：

```text
Action: calculator
Action Input: {"expression": "1 + 2"}
```

- 如果模型已经可以回答，需要输出：

```text
Final Answer: 结果是 3
```

- `build_agent_system_prompt()` 负责把可用工具、输入 schema 和输出格式要求写进 system prompt。
- `parse_agent_output()` 负责把模型的自然语言输出解析成结构化结果：要么是 final answer，要么是 action + action input，要么是 parser error。
- `AgentLoop` 根据解析结果调用 `ToolRegistry.run(...)`，把工具返回内容记录成 observation，再带着 observation 进入下一轮 LLM 调用。
- 当前实现故意不要求模型输出 `Thought`，避免把模型推理过程变成稳定 API，也降低日志和测试的复杂度。

**复习版理解：**

在 `anythingllm-mini` 里，ReAct 文本协议是 LLM 和 Python Agent Loop 之间的一层“可解析约定”。普通 Workspace Chat 是固定流程：系统先准备上下文，再让 LLM 直接回答；而 Agent Loop 会先让 LLM 自己判断是否需要调用工具。

当 LLM 输出 `Action` / `Action Input` 时，`parse_agent_output()` 会把文本转成可执行的工具调用，`ToolRegistry.run(...)` 再统一处理工具查找、输入校验和异常包装。工具执行结果不会直接成为最终答案，而是作为 observation 回到下一轮 LLM 输入中，让模型基于观察结果继续决定下一步。

当 LLM 输出 `Final Answer` 时，Agent Loop 停止循环，并把后面的文本作为最终 assistant answer。这个设计的学习价值在于：先用简单文本格式理解 agent loop、tool call、observation 和 stop condition 的关系，后续再升级到 provider-native function calling 时，核心控制流不会变。

**相关文件：**

- `app/core/agent_loop.py`
- `app/tools/registry.py`
- `app/tools/calculator.py`
- `app/tools/document_tools.py`
- `tests/test_agent_loop.py`
- `docs/design/v4-agent-loop-design.md`

**后续可复习关键词：**

- ReAct
- Action / Action Input
- Final Answer
- observation
- parser
- ToolRegistry
- AgentLoop
- provider-native tool calling
