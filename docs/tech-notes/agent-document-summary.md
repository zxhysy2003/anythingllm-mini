# Agent 长文档总结：有界长任务工具

术语遵循 [Agent 文档术语（Terminology）](agent-terminology.md)。

## 定位

`workspace_document_search` 面向定点问答，只检索相关 chunks；
`workspace_document_summary` 面向全文覆盖，读取一个文档的完整 parsed text，再生成有界摘要。

这个切片连接三条能力线：

- Document lifecycle：只读取当前 workspace 内已登记且 parsed text 校验成功的文档。
- RAG quality：summary source 表达实际覆盖范围，但不伪造检索相似度。
- Agent capabilities：工具内部可以执行多次 LLM 调用、发进度并返回 partial，同时外层仍保持一个
  可解释的 Agent step。

## Tool contract

一个只读、低风险工具支持两个 action：

```json
{"action": "list"}
```

```json
{"action": "summarize", "document_id": "<32位hex>"}
```

```json
{"action": "summarize", "filename": "guide.pdf"}
```

`list` 不接受 selector；`summarize` 要求 ID 或精确 filename 二选一。同一 workspace 内出现同名
文档时，工具返回候选 ID，不自行挑选。filename 必须是安全 basename，LLM 不能提供 workspace ID
或本地路径，也不能通过换行、制表符等控制字符把额外内容注入 Agent observation。运行时的
Pydantic 校验与提供给 native tool calling 的 JSON Schema 都表达同一 action-selector 互斥关系。
filename selector 与 `list` 返回的展示名使用同一规范化规则；不安全或超过 255 字符的原始名称会
回退到上传时清洗过的 `stored_filename`，因此列表中的名称可以直接用于后续 summarize。

工具读取当前 Agent request 的 runtime-only SQLModel session。这个 session 不进入 prompt、approval
ID、persistence 或 replay fixture；文档查询始终附带 `workspace_id`。

## 安全读取

文档上传成功时，解析文本保存在 `parsed_dir/<document_id>/...txt`。总结前重新检查：

1. document ID 是 32 位小写十六进制。
2. resolved path 位于对应 document directory 内，且不是目录本身。
3. 文件存在、扩展名为 `.txt`、编码为 UTF-8、内容非空。
4. 实际字符数与 `workspace_documents.character_count` 一致。

权限、文件竞态和其他 parsed-file `OSError` 也会在文档服务内转换为不带本地路径的安全错误。任何
检查或读取失败都返回 `document_content_invalid`，不会把 `parsed_path` 或异常路径放进
ToolResult。

## Summary pipeline

默认边界：

- section 输入预算：`settings.max_context_chars`，当前 4000 字符。
- paragraph-first 分块；超长段落硬切；overlap 为 0。
- 最多处理前 8 个 sections。
- section summary 最多 600 字符；最终摘要最多 4000 字符。
- temperature 为 0，不带 conversation history，不接受用户自定义 summary prompt。

小文档只调用一次 summary LLM。长文档顺序执行：

```text
parsed text
-> split sections
-> map(section 1..N)
-> reduce(section summaries)
-> ToolResult content + artifacts
```

summary system prompt 把原文和 section summaries 都声明为不可信 reference data，要求忽略其中的
命令、角色变化和 prompt-like text。上传文件名同样属于不可信元数据，因此不会进入 summary LLM
prompt，只在模型调用结束后用于格式化 ToolResult。chunks 顺序处理，不并行请求 provider，因此
进度和失败位置可确定复现。分块扫描仍会遍历全文以计算准确的 `total_chunks`，但内存中只保留前
8 个待处理 sections，不会先物化整篇文档的全部 chunk 列表。

工具内部调用数保存在 `artifacts.outputs.summary_llm_call_count`；Agent invocation 的
`llm_call_count` 仍只统计 executor 调用。一次总结工具调用无论内部有多少 sections，都只形成一个
`AgentStep`。

## Partial contract

有可用结果但未完成全文时，ToolResult 保持 `ok=true`，并显式返回：

```json
{
  "completion_status": "partial",
  "stop_reasons": ["max_chunks"],
  "total_chunks": 12,
  "processed_chunks": 8
}
```

当前 stop reasons：

- `max_chunks`：全文超过 8 段，只处理前 8 段并对它们执行 Reduce。
- `model_error`：完成至少一段后，后续 Map 调用失败。
- `reduce_failed`：Map 已产生结果，但 Reduce 失败，改用代码拼接的 section summaries。
- `summary_output_limit`：模型输出超过明确字符上限并被标记截断。

partial content 固定写明“仅覆盖前 N/M 段”。首段或小文档摘要失败时没有可用结果，返回
`ok=false` 和 `document_summary_failed`。partial 不增加新的 invocation status，也不计入
`failed_step_count`。如果下一轮 Agent LLM 的最终回答遗漏覆盖范围，两个 executor 会根据最终
artifacts 确定性追加 `N/M` 和 `stop_reasons` 声明；这个兜底同样适用于达到 max steps 时生成的
结束回答。

## Progress 与 citation

流式 Agent API 在原有事件之间增加：

```text
tool_started
tool_progress (loading)
tool_progress (summarizing, 1/N ... N/N)
tool_progress (reducing)
tool_finished
```

payload 只有 tool/step 标识、phase、N/M 和安全 message，不含原文、prompt 或中间摘要。同步 API
返回相同最终 ToolResult，但没有实时 timeline。

每个实际处理的 section 形成一个 `ToolSourceArtifact`。直接读取不存在相似度，因此
`score=null`；文档搜索 source 继续使用数值 score。partial 只引用已处理 sections，sources 仍由
AgentService 汇总到 assistant message 和 invocation detail。

artifact 的通用安全规则会拒绝看起来像裸本地路径的 output 字符串。若模型摘要本身以
`/etc/...`、`file:` 等 path-like 文本开头，工具只在结构化 `chunk_summaries` 中增加
`Summary text:` prose 标签，给下一轮 Agent 的最终 `content` 保持原摘要不变。若原始文件名会触发
同一规则或包含控制字符，则搜索和总结的展示内容及 artifact 都使用上传时已经清洗过的
`stored_filename`。

`chunk_summaries` 会按完整 JSON 序列化后的实际字符数检查 8000 字符总预算，包括引号、反斜杠和
控制字符产生的转义开销。若各项分别合法但合并后超限，工具按统一字符上限确定性截断结构化的
section summaries，加入 `summary_output_limit` 并返回显式 partial；不会让最终 artifact 校验异常
退化成 `tool_execution_failed`。

replay fixture 保存最终 ToolResult、chunk summaries、source snapshot 和 metrics，但不重放瞬时
progress timeline，也不重新调用工具或 LLM。导出时会用仍满足 selector schema 的 32 位 hex
别名统一替换 action input、LLM output、ToolResult、source snapshot 和最终消息中的文档 ID。

## 明确不支持

- token tokenizer 或 provider-specific context window。
- 中途 clarification、批准继续或真实 abort。
- 后台 task、断点恢复和多客户端订阅。
- 自定义摘要风格、focus/query、摘要文件和 attachment。
- 并行 Map、层级 Reduce 或跨 workspace 总结。

这些能力会引入新的成本、恢复或交互状态，应在真实长文档数据证明需要后再单独学习。

## Suggested Tests To Read

- `backend/tests/test_document_summary_service.py::test_document_summary_small_document_returns_complete_summary`
  - 小文档单次调用、prompt 边界和 progress 的最短 happy path。
- `backend/tests/test_document_summary_service.py::test_document_summary_long_document_emits_ordered_progress_and_reduce`
  - 顺序 Map + Reduce 以及内部调用计数。
- `backend/tests/test_document_summary_service.py::test_document_summary_limit_returns_explicit_partial_coverage`
  - `max_chunks` partial 的覆盖范围和 sources 边界。
- `backend/tests/test_document_summary_service.py::test_document_summary_chunk_scan_retains_only_bounded_prefix`
  - 扫描全文计数，但只保留允许处理的 chunk 前缀。
- `backend/tests/test_document_tools.py::test_document_summary_maps_file_io_error_without_leaking_path`
  - parsed-file I/O 失败映射为稳定错误且不泄露本地路径。
