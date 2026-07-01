# V2 RAG 待处理问题

本文记录 V2 代码审查中确认存在、后续已处理的问题。

## 1. 分批写入失败可能留下部分索引

状态：已解决

### 原始行为

`ChromaVectorStore._upsert_chunks()` 每 100 个 Chunk 写入一批。如果前面的批次已经
成功，而后续批次失败，上传接口会返回 HTTP 500，但已经写入的部分向量仍可能留在
Chroma 中并参与后续检索。

### 原始影响

- RAG 可能检索到上传失败文档的不完整内容。
- 重新索引失败时，collection 可能同时包含新旧索引数据。

### 解决方案

- 采用简单方案：写入失败时删除该 `document_id + workspace_id` 范围内的所有向量。
- 如果重新索引时删除 stale Chunk 失败，也清理该文档范围内的索引，避免新旧混合。
- 暂不实现临时版本写入或索引事务。

### 验收标准

- 索引失败后，该文档范围内的残留 Chunk 会被清理。
- 成功重新索引后，不残留旧 Chunk。
- 已增加分批 upsert 失败和 stale delete 失败测试。

## 2. Qdrant 配置与实际实现不一致

状态：已解决

### 原始行为

`Settings.vector_store` 接受 `chroma` 和 `qdrant`，但 V2 始终实例化
`ChromaVectorStore`。设置 `VECTOR_STORE=qdrant` 时，程序仍会使用 Chroma。

### 原始影响

- 配置会被静默忽略。
- 使用者可能误以为向量已经写入 Qdrant。

### 解决方案

- 在只支持 Chroma 时，将配置限制为 `Literal["chroma"]`。
- 移除 `.env.example` 中未启用的 Qdrant 示例。
- 多向量数据库支持留到后续阶段，再增加 vector store factory。

### 验收标准

- 配置声明的 vector store 与运行时实现一致。
- `Settings(vector_store="qdrant")` 会触发 Pydantic 校验错误，不再静默回退。
