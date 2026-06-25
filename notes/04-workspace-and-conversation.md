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

Workspace 聊天：

```text
用户消息
-> 读取当前 Conversation 最近 N 轮历史
-> 只检索当前 Workspace 的 Chunk
-> 按 chat/query 模式组织 Prompt
-> 调用 DeepSeek 或返回无上下文提示
-> 事务保存 user 和 assistant 消息
```

## Chat 与 Query

- `chat`：检索到文档时使用 RAG；没有相关文档时仍可进行普通聊天。
- `query`：只允许根据文档回答；没有相关 Chunk 时不调用 DeepSeek。

Workspace 独立保存 `system_prompt`、`temperature`、`history_limit`、
`chat_mode`、`top_k` 和 `similarity_threshold`。

`history_limit` 表示历史轮数。一轮包含一条 user 消息和一条 assistant 消息，传给
DeepSeek 前会恢复为按时间正序排列的 message 列表。

## HTTP 接口

```text
POST  /workspaces
GET   /workspaces
GET   /workspaces/{workspace_id}
PATCH /workspaces/{workspace_id}

POST /workspaces/{workspace_id}/documents/upload
GET  /workspaces/{workspace_id}/documents

POST /workspaces/{workspace_id}/conversations
GET  /workspaces/{workspace_id}/conversations
GET  /workspaces/{workspace_id}/conversations/{conversation_id}/messages
POST /workspaces/{workspace_id}/conversations/{conversation_id}/chat
```

V0 的 `/chat` 和 V2 的 `/documents/upload`、`/rag/query` 继续保留，方便对照各阶段。

## 当前边界

V3 仍使用同步请求和单机 SQLite，不包含用户权限、流式回答、历史摘要、问题改写、
Workspace 删除和后台索引。旧 V2 Chunk 没有 `workspace_id`，不会被 Workspace 范围的
查询命中。
