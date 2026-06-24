# V2 RAG Main Flow

V2 在 V1 文档解析结果之上增加文本分块、Embedding、向量存储和检索问答。

## Index Flow

```text
POST /documents/upload
  -> 保存原文件
  -> 解析纯文本
  -> TextChunker 分块
  -> multilingual-e5-small 生成向量
  -> Chroma 持久化
  -> 返回文档 metadata 和 chunk_count
```

默认分块大小为 400 个字符，重叠 60 个字符。分块优先选择段落边界，超长段落
再按字符长度切分。

每个 Chunk 使用确定性 ID：

```text
{document_id}:{chunk_index}
```

Chroma 同时保存 Chunk 文本、向量和文档 metadata。重新索引同一文档时，会更新现有
Chunk 并删除已经失效的旧 Chunk。

## Query Flow

```text
POST /rag/query
  -> 为问题生成查询向量
  -> 从 Chroma 检索 top_k 个 Chunk
  -> 过滤低于 similarity_threshold 的 Chunk
  -> 将 Chunk 组装为受约束的 system prompt
  -> 调用 ChatService 和 DeepSeek
  -> 返回答案与 sources
```

`/chat` 仍然是普通聊天，不进行文档检索。`/rag/query` 只根据检索上下文回答。当前
默认相似度阈值为 `0.75`，只有 `score >= 0.75` 的 Chunk 才会进入 Prompt 和
`sources`。没有已索引文档或所有候选都低于阈值时，直接返回无上下文提示，不调用
DeepSeek。

## Main Components

```text
app/core/rag.py             -> Chunk 模型与文本分块
app/core/embeddings.py      -> 多语言 Embedding 包装
app/core/vectorstore.py     -> Chroma 持久化与检索
app/services/rag_service.py -> 索引和问答编排
app/api/rag.py              -> RAG HTTP API
```

Embedding 使用 `intfloat/multilingual-e5-small`：文档添加 `passage:` 前缀，查询添加
`query:` 前缀，并生成归一化向量。模型采用延迟加载，首次真实索引时需要下载模型。

## API

上传并索引：

```http
POST /documents/upload
Content-Type: multipart/form-data
```

RAG 问答：

```http
POST /rag/query
Content-Type: application/json

{"question": "文档主要讲了什么？"}
```

响应中的 `sources` 包含文档 ID、原文件名、Chunk 序号、文本和相似度，方便检查
答案依据。

## V2 Boundary

当前所有文档共享一个 Chroma collection，并使用全局 `SIMILARITY_THRESHOLD`。V2
暂不实现 workspace 独立阈值、数据库文档记录、rerank、混合检索、后台索引和流式
回答，这些能力在后续阶段按需加入。
