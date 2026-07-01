# V3 Workspace 与会话历史

## 目标

V3 在 V2 RAG 上增加两个边界：

- Workspace 隔离文档、检索范围和聊天配置。
- Conversation 隔离多段聊天，并把消息保存到数据库。

整体关系：

```text
Workspace -> WorkspaceDocument
          -> Conversation -> ConversationMessage
```

## 数据流

Workspace 文档上传：

```text
UploadFile
-> 保存和解析
-> 分块和 Embedding
-> 写入带 workspace_id 的 Chroma metadata
-> 保存 WorkspaceDocument
```

如果 Chroma 索引成功但 `WorkspaceDocument` 数据库记录保存失败，服务会删除刚写入的
该文档 Chunk，避免聊天能检索到但文档列表看不到的孤儿向量。

Workspace 文档删除：

```text
workspace_id + document_id
-> 校验文档属于当前 Workspace
-> 校验本地上传文件和解析文件路径安全
-> 删除该 document_id + workspace_id 范围内的 Chroma Chunk
-> 删除本地上传文件和解析文件
-> 删除 WorkspaceDocument 数据库记录
```

本地文件删除前会校验路径位于对应的 `upload_dir/{document_id}` 和
`parsed_dir/{document_id}` 下，避免根据异常元数据误删其他文件。

Workspace 删除：

```text
workspace_id
-> 确认 Workspace 存在
-> 一次性加载 WorkspaceDocument、Conversation 和 ConversationMessage
-> 为全部文档构建并校验本地文件删除计划
-> 按 document_id + workspace_id 删除 Chroma Chunk
-> 删除本地 upload/parsed 文件
-> 显式删除 ConversationMessage、Conversation、WorkspaceDocument 和 Workspace
-> 一次提交数据库事务
```

这里故意不依赖 SQLite cascade。任一文档路径不安全时会在副作用发生前失败；Chroma
删除失败时不会删除本地文件或数据库；本地文件删除失败时不会删除数据库。最后数据库
提交失败时会返回 `WorkspacePersistenceError`，但不会尝试恢复已经删除的 Chroma Chunk
或本地文件。

Workspace 聊天：

```text
用户消息
-> 读取当前 Conversation 最近 N 轮历史
-> 只检索当前 Workspace 的 Chunk
-> 按 chat/query 模式组织 Prompt
-> 调用 DeepSeek 或返回无上下文提示
-> 事务保存 user 和 assistant 消息，以及 assistant 调试 metrics
```

## Chat 与 Query

- `chat`：检索到文档时使用 RAG；没有相关文档时仍可进行普通聊天。
- `query`：只允许根据文档回答；没有相关 Chunk 时不调用 DeepSeek。

Workspace 独立保存 `system_prompt`、`temperature`、`history_limit`、
`chat_mode`、`top_k` 和 `similarity_threshold`。

`history_limit` 表示历史轮数。一轮包含一条 user 消息和一条 assistant 消息，传给
DeepSeek 前会恢复为按时间正序排列的 message 列表。

Workspace chat 会在响应和 assistant message 上保存基础 metrics：检索到的 Chunk 数、
实际使用的 source 数、预算丢弃数、context 字符数、是否有可用上下文、query 模式是否
拒答、是否调用 LLM，以及 retrieval/LLM/total latency。user message 的 `metrics`
保持 `{}`。这些 metrics 只用于本地调试，不记录用户消息、文档正文、source text、
本地文件路径或 API key。

## HTTP 接口

```text
POST  /workspaces
GET   /workspaces
GET   /workspaces/{workspace_id}
PATCH /workspaces/{workspace_id}
DELETE /workspaces/{workspace_id}

POST /workspaces/{workspace_id}/documents/upload
GET  /workspaces/{workspace_id}/documents
DELETE /workspaces/{workspace_id}/documents/{document_id}

POST /workspaces/{workspace_id}/conversations
GET  /workspaces/{workspace_id}/conversations
GET  /workspaces/{workspace_id}/conversations/{conversation_id}/messages
POST /workspaces/{workspace_id}/conversations/{conversation_id}/chat
```

V0 的 `/chat` 和 V2 的 `/documents/upload`、`/rag/query` 继续保留，方便对照各阶段。
这三个全局入口在 OpenAPI 中标记为 legacy/deprecated；V4 新能力应挂在 Workspace
Conversation 路径下。V2 旧接口只访问 `workspace_id="__global__"` 的全局 Chunk，
不会读取 Workspace 文档。

## 轻量日志

V3.5 使用 Python 标准库 `logging` 补了一层本地结构化日志，覆盖文档上传/删除、RAG
检索、Workspace 聊天保存和 Workspace 删除。事件名保持稳定，例如
`document.upload.completed`、`document.delete.completed`、`rag.retrieve.completed`、
`workspace.chat.completed`、`workspace.delete.start`、`workspace.delete.completed` 和
`workspace.delete.failed`。

日志字段只放 `workspace_id`、`document_id`、`conversation_id`、chunk 计数、
删除计数、`chat_mode` 和 `has_context` 这类排查字段。不记录用户消息全文、文档正文、
source text、本地文件路径或 API key。

## 当前边界

V3 仍使用同步请求和单机 SQLite，不包含用户权限、流式回答、历史摘要、问题改写、
软删除和后台索引。文档删除和 Workspace 删除都采用硬删除；如果最后数据库删除提交
失败，不会尝试恢复已经删除的 Chroma Chunk 或本地文件。旧 V2 Chunk 没有
`workspace_id`，不会被 Workspace 范围的查询命中；旧的无 `workspace_id` Chroma 数据
也不会被新的 V2 全局检索命中，学习环境可以清空 `storage/chroma` 后重新上传。

当前项目使用 Alembic 管理数据库 schema。已有旧 SQLite 如果停留在 V3 基础表结构、
但缺少 `conversation_messages.metrics`，需要先 `alembic stamp 0001_baseline_v3_schema`
标记基线，再 `alembic upgrade head` 应用 metrics 迁移。新数据库直接运行
`alembic upgrade head` 即可创建完整 schema。
