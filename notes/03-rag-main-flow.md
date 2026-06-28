# V2 RAG Main Flow

V2 在 V1 文档解析结果之上增加文本分块、Embedding、向量存储和检索问答。

这份笔记记录的是 V2 主流程。当前代码已经叠加了 V3 的 Workspace 范围隔离和
V3.5 的 RAG context 字符预算；这些后续变化在相关章节中单独标注。

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
Chunk 并删除已经失效的旧 Chunk。如果分批写入或 stale Chunk 删除失败，会按
`document_id + workspace_id` 清理该文档范围内的索引，避免留下不完整内容。

V2 的全局接口会把 Chunk 写入固定范围 `workspace_id="__global__"`。这样进入 V3 后，
旧 `/documents/upload` 和 `/rag/query` 仍可用于学习对照，但不会读取 Workspace 上传
的文档。

## Query Flow

```text
POST /rag/query
  -> 为问题生成查询向量
  -> 从 Chroma 检索 top_k 个 Chunk
  -> 过滤低于 similarity_threshold 的 Chunk
  -> 按 max_context_chars 预算组装受约束的 system prompt
  -> 调用 ChatService 和 DeepSeek
  -> 返回答案与实际进入 prompt 的 sources
```

`/chat` 仍然是普通聊天，不进行文档检索。`/rag/query` 只根据检索上下文回答。当前
默认相似度阈值为 `0.75`，只有 `score >= 0.75` 的 Chunk 才会进入 Prompt 和
`sources`。没有已索引文档或所有候选都低于阈值时，直接返回无上下文提示，不调用
DeepSeek。

V3.5 后，RAG context 还会受 `MAX_CONTEXT_CHARS` 限制，默认 `4000` 个字符。预算只
统计完整 source block 和 source 之间的空行分隔符，不统计 workspace system prompt
或 conversation history。系统按检索分数顺序保留能完整放入预算的 source block，
遇到第一个超预算 block 后停止；因此返回给 API 的 `sources` 只包含实际进入 prompt
的 Chunk。如果检索到了 Chunk，但预算后没有任何 source 可用，也会按无上下文处理，
直接返回无上下文提示，不调用 DeepSeek。

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

## Real Embedding Smoke Test

日常测试会跳过真实模型验证，不下载模型或访问网络。需要验证多语言 E5 与 Chroma
集成时，显式运行：

```bash
RUN_REAL_EMBEDDING_TESTS=1 \
conda run -n anythingllm-mini pytest -q \
-m real_embedding tests/test_real_embedding_smoke.py
```

测试读取当前 `EMBEDDING_MODEL_NAME`。默认配置首次运行会下载
`intfloat/multilingual-e5-small`；如果配置为本地模型路径，则直接加载该目录。测试使用
真实模型生成中文文档和查询向量，验证归一化、向量维度、临时 Chroma 写入及相关
Chunk 排名，不调用 DeepSeek，也不会写入项目的 `storage/chroma`。

## V2 Boundary

V2 阶段只实现 Chroma，所有文档共享一个 Chroma collection，并使用全局
`SIMILARITY_THRESHOLD`。当前代码进入 V3 后，Workspace 接口可以保存独立的 `top_k`
和 `similarity_threshold`，但旧 `/documents/upload` 和 `/rag/query` 仍属于
`__global__` 范围，不属于任何 Workspace。旧的无 `workspace_id` Chroma 数据不会被
新的全局检索命中，学习环境可以清空 `storage/chroma` 后重新上传。

V2 阶段暂不实现 workspace 独立阈值、数据库文档记录、rerank、混合检索、后台索引和
流式回答。其中 workspace 独立阈值和数据库文档记录已经在 V3 补上；rerank、混合
检索、后台索引和流式回答仍留到后续阶段。
