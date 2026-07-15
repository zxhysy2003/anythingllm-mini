# Agent replay：把一次运行变成可复用回归样例

## 目的和边界

V4 已把一次 Agent 运行保存为 `agent_invocations` 和 ordered `agent_steps`，但持久化记录
本身只是观察证据。Agent replay 的第一版把它转换为可以重复检查的 JSON fixture：用相同的
模型输出、工具输入和持久化结果来验证工程边界是否因后续改动而退化。

它不重新调用 LLM，也不执行工具。因此，它不回答“同一句话能否每次生成完全相同的答案”，而是
检查这些确定性行为：

- `react_text` 的 `llm_output` 是否仍能解析为原 action / action input / parser error。
- 当前 ToolRegistry 是否仍能找到同名工具，且 input model 对原输入给出相同的有效或无效边界。
- step 的 `ok`、`error`、`observation` 是否仍与完整 `ToolResult` 合同一致。
- invocation `status` 是否仍与 `max_steps_reached` 汇总结果一致。
- assistant message 的 sources snapshot 是否仍等于 step artifacts 汇总结果。
- `step_count`、`tool_call_count`、`failed_step_count`、`source_count` 是否仍能从 fixture 推导。

`llm_call_count` 和 `total_latency_ms` 会保留在 fixture 中作为运行证据，但不会作为 replay
硬断言；native tool calling 也没有 `react_text` parser，所以 parser check 会显示
`not_applicable`。

## 本地命令

在 `backend/` 目录运行：

```bash
conda run -n anythingllm-mini python -m app.maintenance.export_agent_replay \
  <agent_invocation_id> --output /absolute/path/to/fixture.json

conda run -n anythingllm-mini python -m app.maintenance.replay_agent_fixture \
  /absolute/path/to/fixture.json
```

导出命令只读数据库，并要求显式 `--output`。若文件已存在，命令失败；确认覆盖时才加入
`--overwrite`。回放命令返回：`0` 表示全部检查通过，`1` 表示 fixture 与当前确定性边界不匹配，
`2` 表示文件、合同或运行环境错误。

导出 fixture 保留原始 user message、answer、LLM step 文本、工具输入、artifact 内容和 source
文本，以便复现 parser 与 registry 行为；它会移除 invocation/workspace/conversation/message ID
和时间戳，并把 source 中的 document ID 改为当前 fixture 内稳定的 `document_1`、`document_2`
别名。这仍可能包含业务文本，只应输出到明确指定的本地路径，不能直接提交到 Git。

## 从失败运行到测试

1. 在本地调试页或 invocation detail 中定位失败的 `agent_invocation_id`。
2. 导出到临时本地路径，确认其中没有不应分享的用户、文档或工具输入内容。
3. 不直接提交导出文件；改写为 `backend/tests/fixtures/agent_replay/` 中的合成样例，保留触发
   parser、registry、artifact 或 metrics 问题所需的最小结构。
4. 在 `test_agent_replay_service.py` 中添加该 fixture 的回放断言，并用 fake registry/input
   model 固定工程边界。

仓库当前的三份合成样例覆盖 react calculator 成功、react parser failure 与 native calculator
成功。它们是回归资产，而不是对真实模型回答质量的排行榜或在线评估平台。
