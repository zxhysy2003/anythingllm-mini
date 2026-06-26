import asyncio

import pytest

from app.core.rag import GLOBAL_WORKSPACE_ID, DocumentChunk
from app.core import vectorstore as vectorstore_module
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


class FakeChromaClient:
    def __init__(self, collection):
        self.collection = collection

    def get_or_create_collection(self, name, metadata):
        self.collection.metadata = metadata
        return self.collection


class FakeChromaCollection:
    def __init__(self):
        self.metadata = {}
        self.records = {}
        self.upsert_calls = 0
        self.fail_on_upsert_call = None
        self.fail_next_delete = False

    def get(self, where, include):
        ids = [
            record_id
            for record_id, record in self.records.items()
            if self._matches_where(record["metadata"], where)
        ]
        return {"ids": sorted(ids)}

    def upsert(self, ids, embeddings, documents, metadatas):
        self.upsert_calls += 1
        if self.upsert_calls == self.fail_on_upsert_call:
            raise RuntimeError("upsert failed")

        for record_id, embedding, document, metadata in zip(
            ids,
            embeddings,
            documents,
            metadatas,
            strict=True,
        ):
            self.records[record_id] = {
                "embedding": embedding,
                "document": document,
                "metadata": metadata,
            }

    def delete(self, ids):
        if self.fail_next_delete:
            self.fail_next_delete = False
            raise RuntimeError("delete failed")

        for record_id in ids:
            self.records.pop(record_id, None)

    def _matches_where(self, metadata, where):
        if "$and" in where:
            return all(self._matches_where(metadata, item) for item in where["$and"])
        return all(metadata.get(key) == value for key, value in where.items())


def create_fake_store(tmp_path, collection) -> ChromaVectorStore:
    return ChromaVectorStore(
        persist_dir=tmp_path / "fake-chroma",
        collection_name="fake-documents",
        embedding_model_name="fake-embedding-model",
        client_factory=lambda _: FakeChromaClient(collection),
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


def test_chroma_store_cleans_partial_batches_when_upsert_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(vectorstore_module, "UPSERT_BATCH_SIZE", 1)
    collection = FakeChromaCollection()
    collection.fail_on_upsert_call = 2
    store = create_fake_store(tmp_path, collection)
    workspace_id = "1" * 32
    document_id = "a" * 32
    chunks = [
        make_chunk(document_id, 0, "first", workspace_id),
        make_chunk(document_id, 1, "second", workspace_id),
    ]

    with pytest.raises(RuntimeError, match="upsert failed"):
        asyncio.run(store.upsert_chunks(chunks, [[1.0, 0.0], [0.9, 0.1]]))

    assert asyncio.run(store.count(workspace_id=workspace_id)) == 0
    assert collection.records == {}


def test_chroma_store_clears_document_when_reindex_stale_delete_fails(
    tmp_path,
):
    collection = FakeChromaCollection()
    store = create_fake_store(tmp_path, collection)
    workspace_id = "1" * 32
    document_id = "a" * 32
    old_chunks = [
        make_chunk(document_id, 0, "old first", workspace_id),
        make_chunk(document_id, 1, "old second", workspace_id),
    ]
    new_chunks = [make_chunk(document_id, 0, "new first", workspace_id)]
    asyncio.run(store.upsert_chunks(old_chunks, [[1.0, 0.0], [0.0, 1.0]]))
    collection.fail_next_delete = True

    with pytest.raises(RuntimeError, match="delete failed"):
        asyncio.run(store.upsert_chunks(new_chunks, [[0.8, 0.2]]))

    assert asyncio.run(store.count(workspace_id=workspace_id)) == 0
    assert collection.records == {}
