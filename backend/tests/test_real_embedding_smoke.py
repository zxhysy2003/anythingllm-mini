import asyncio
import math
import os

import pytest

from app.core.embeddings import SentenceTransformerEmbeddings
from app.core.rag import DocumentChunk
from app.core.vectorstore import ChromaVectorStore

pytestmark = [
    pytest.mark.real_embedding,
    pytest.mark.skipif(
        os.getenv("RUN_REAL_EMBEDDING_TESTS") != "1",
        reason="set RUN_REAL_EMBEDDING_TESTS=1 to run the real model smoke test",
    ),
]


def test_real_multilingual_embedding_retrieves_relevant_chinese_chunk(tmp_path):
    workspace_id = "1" * 32
    texts = [
        (
            "FastAPI 可以使用 UploadFile 接收 multipart/form-data 上传的文件，"
            "并通过异步 read 方法读取内容。"
        ),
        "Chroma 是向量数据库，可以保存 Embedding 并执行余弦相似度检索。",
        "pathlib.Path 使用斜杠运算符拼接目录与文件名，适合处理文件系统路径。",
    ]
    question = "FastAPI 接收上传文件时应该使用什么类型？"
    embeddings = SentenceTransformerEmbeddings()

    document_vectors = asyncio.run(embeddings.embed_documents(texts))
    query_vector = asyncio.run(embeddings.embed_query(question))
    vectors = [*document_vectors, query_vector]

    dimension = len(query_vector)
    assert dimension > 0
    assert all(len(vector) == dimension for vector in vectors)
    assert all(
        isinstance(component, float) for vector in vectors for component in vector
    )
    assert all(
        math.sqrt(sum(component * component for component in vector))
        == pytest.approx(1.0, abs=1e-5)
        for vector in vectors
    )

    document_id = "real-embedding-smoke"
    chunks = [
        DocumentChunk(
            id=f"{document_id}:{index}",
            document_id=document_id,
            workspace_id=workspace_id,
            display_filename="smoke.txt",
            extension=".txt",
            chunk_index=index,
            text=text,
            character_count=len(text),
        )
        for index, text in enumerate(texts)
    ]
    store = ChromaVectorStore(
        persist_dir=tmp_path / "chroma",
        collection_name="real-embedding-smoke",
        embedding_model_name=embeddings.model_name,
    )

    chunk_count = asyncio.run(store.upsert_chunks(chunks, document_vectors))
    results = asyncio.run(
        store.query(
            query_vector,
            workspace_id=workspace_id,
            top_k=len(chunks),
            similarity_threshold=0.0,
        )
    )

    assert chunk_count == len(chunks)
    assert asyncio.run(store.count(workspace_id=workspace_id)) == len(chunks)
    assert results
    assert results[0].id == f"{document_id}:0"
    assert "UploadFile" in results[0].text
