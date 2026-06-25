import asyncio

from app.core.rag import GLOBAL_WORKSPACE_ID, DocumentChunk
from app.core.vectorstore import ChromaVectorStore


def make_chunk(
    document_id: str,
    index: int,
    text: str,
    workspace_id: str = GLOBAL_WORKSPACE_ID,
) -> DocumentChunk:
    return DocumentChunk(
        id=f"{document_id}:{index}",
        document_id=document_id,
        workspace_id=workspace_id,
        original_filename="guide.txt",
        stored_filename="guide.txt",
        extension=".txt",
        chunk_index=index,
        text=text,
        character_count=len(text),
    )


def create_store(tmp_path) -> ChromaVectorStore:
    return ChromaVectorStore(
        persist_dir=tmp_path / "chroma",
        collection_name="test-documents",
        embedding_model_name="fake-embedding-model",
    )


def test_chroma_store_persists_and_queries_chunks(tmp_path):
    store = create_store(tmp_path)
    document_id = "a" * 32
    chunks = [
        make_chunk(document_id, 0, "apple document"),
        make_chunk(document_id, 1, "banana document"),
    ]

    count = asyncio.run(store.upsert_chunks(chunks, [[1.0, 0.0], [0.0, 1.0]]))
    persisted_store = create_store(tmp_path)
    results = asyncio.run(
        persisted_store.query(
            [1.0, 0.0],
            top_k=2,
            similarity_threshold=0.0,
        )
    )

    assert count == 2
    assert asyncio.run(persisted_store.count()) == 2
    assert [result.text for result in results] == [
        "apple document",
        "banana document",
    ]
    assert results[0].score > results[1].score
    assert results[0].document_id == document_id
    assert results[0].chunk_index == 0


def test_chroma_store_filters_results_below_similarity_threshold(tmp_path):
    store = create_store(tmp_path)
    document_id = "c" * 32
    chunks = [
        make_chunk(document_id, 0, "matching document"),
        make_chunk(document_id, 1, "unrelated document"),
    ]
    asyncio.run(store.upsert_chunks(chunks, [[1.0, 0.0], [0.0, 1.0]]))

    filtered_results = asyncio.run(
        store.query(
            [1.0, 0.0],
            top_k=2,
            similarity_threshold=0.75,
        )
    )
    boundary_results = asyncio.run(
        store.query(
            [1.0, 0.0],
            top_k=2,
            similarity_threshold=0.0,
        )
    )

    assert [result.text for result in filtered_results] == ["matching document"]
    assert [result.text for result in boundary_results] == [
        "matching document",
        "unrelated document",
    ]


def test_chroma_store_removes_stale_chunks_when_reindexing(tmp_path):
    store = create_store(tmp_path)
    document_id = "b" * 32
    old_chunks = [
        make_chunk(document_id, 0, "old first"),
        make_chunk(document_id, 1, "old second"),
    ]
    asyncio.run(store.upsert_chunks(old_chunks, [[1.0, 0.0], [0.0, 1.0]]))

    new_chunks = [make_chunk(document_id, 0, "new content")]
    asyncio.run(store.upsert_chunks(new_chunks, [[1.0, 0.0]]))
    results = asyncio.run(store.query([1.0, 0.0], top_k=5))

    assert asyncio.run(store.count()) == 1
    assert [result.id for result in results] == [f"{document_id}:0"]
    assert results[0].text == "new content"


def test_chroma_store_filters_chunks_by_workspace(tmp_path):
    store = create_store(tmp_path)
    first_workspace = "1" * 32
    second_workspace = "2" * 32
    chunks = [
        make_chunk("a" * 32, 0, "first workspace", first_workspace),
        make_chunk("b" * 32, 0, "second workspace", second_workspace),
    ]
    asyncio.run(store.upsert_chunks([chunks[0]], [[1.0, 0.0]]))
    asyncio.run(store.upsert_chunks([chunks[1]], [[1.0, 0.0]]))

    results = asyncio.run(
        store.query(
            [1.0, 0.0],
            top_k=5,
            similarity_threshold=0.0,
            workspace_id=first_workspace,
        )
    )

    assert asyncio.run(store.count(workspace_id=first_workspace)) == 1
    assert [result.text for result in results] == ["first workspace"]
    assert results[0].workspace_id == first_workspace


def test_chroma_store_default_query_only_reads_global_chunks(tmp_path):
    store = create_store(tmp_path)
    workspace_id = "1" * 32
    global_chunk = make_chunk("g" * 32, 0, "global document")
    workspace_chunk = make_chunk("w" * 32, 0, "workspace document", workspace_id)
    asyncio.run(store.upsert_chunks([global_chunk], [[1.0, 0.0]]))
    asyncio.run(store.upsert_chunks([workspace_chunk], [[1.0, 0.0]]))

    default_results = asyncio.run(
        store.query(
            [1.0, 0.0],
            top_k=5,
            similarity_threshold=0.0,
        )
    )
    workspace_results = asyncio.run(
        store.query(
            [1.0, 0.0],
            top_k=5,
            similarity_threshold=0.0,
            workspace_id=workspace_id,
        )
    )

    assert asyncio.run(store.count()) == 1
    assert [result.text for result in default_results] == ["global document"]
    assert default_results[0].workspace_id == GLOBAL_WORKSPACE_ID
    assert [result.text for result in workspace_results] == ["workspace document"]


def test_chroma_store_deletes_document_chunks_by_workspace(tmp_path):
    store = create_store(tmp_path)
    workspace_id = "1" * 32
    other_workspace_id = "2" * 32
    first_document_id = "a" * 32
    second_document_id = "b" * 32
    chunks = [
        make_chunk(first_document_id, 0, "remove me", workspace_id),
        make_chunk(first_document_id, 1, "remove me too", workspace_id),
        make_chunk(second_document_id, 0, "keep me", other_workspace_id),
    ]
    asyncio.run(store.upsert_chunks(chunks[:2], [[1.0, 0.0], [0.9, 0.1]]))
    asyncio.run(store.upsert_chunks([chunks[2]], [[1.0, 0.0]]))

    deleted_count = asyncio.run(
        store.delete_document(first_document_id, workspace_id=workspace_id)
    )

    assert deleted_count == 2
    assert asyncio.run(store.count(workspace_id=workspace_id)) == 0
    assert asyncio.run(store.count(workspace_id=other_workspace_id)) == 1
    remaining = asyncio.run(
        store.query(
            [1.0, 0.0],
            top_k=5,
            similarity_threshold=0.0,
            workspace_id=other_workspace_id,
        )
    )
    assert [result.text for result in remaining] == ["keep me"]
