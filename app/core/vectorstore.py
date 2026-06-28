from __future__ import annotations

import asyncio
import threading
from contextlib import suppress
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from app.core.config import PROJECT_ROOT, settings
from app.core.rag import GLOBAL_WORKSPACE_ID, DocumentChunk, RetrievedChunk

if TYPE_CHECKING:
    from chromadb.api import ClientAPI

UPSERT_BATCH_SIZE = 100


class VectorDocumentKey(BaseModel):
    workspace_id: str
    document_id: str
    chunk_count: int


class ChromaVectorStore:
    def __init__(
        self,
        persist_dir: str | Path | None = None,
        collection_name: str | None = None,
        embedding_model_name: str | None = None,
        client_factory: Callable[[str], ClientAPI] | None = None,
    ):
        self.persist_dir = self._resolve_persist_dir(
            persist_dir or settings.chroma_persist_dir
        )
        self.collection_name = collection_name or settings.chroma_collection_name
        self.embedding_model_name = (
            embedding_model_name or settings.embedding_model_name
        )
        self._client_factory = client_factory
        self._client: ClientAPI | None = None
        self._client_lock = threading.Lock()

    async def upsert_chunks(
        self,
        chunks: Sequence[DocumentChunk],
        embeddings: Sequence[Sequence[float]],
    ) -> int:
        chunk_list = list(chunks)
        embedding_list = [list(vector) for vector in embeddings]
        self._validate_upsert(chunk_list, embedding_list)
        return await asyncio.to_thread(
            self._upsert_chunks,
            chunk_list,
            embedding_list,
        )

    async def query(
        self,
        query_embedding: Sequence[float],
        top_k: int | None = None,
        similarity_threshold: float | None = None,
        workspace_id: str | None = None,
    ) -> list[RetrievedChunk]:
        vector = list(query_embedding)
        if not vector:
            raise ValueError("query embedding cannot be empty")

        result_count = settings.top_k if top_k is None else top_k
        if result_count <= 0:
            raise ValueError("top_k must be greater than zero")

        threshold = (
            settings.similarity_threshold
            if similarity_threshold is None
            else similarity_threshold
        )
        if not 0 <= threshold <= 1:
            raise ValueError("similarity_threshold must be between 0 and 1")

        scope = self._normalize_workspace_id(workspace_id)
        return await asyncio.to_thread(
            self._query,
            vector,
            result_count,
            threshold,
            scope,
        )

    async def has_documents(self, workspace_id: str | None = None) -> bool:
        return await self.count(workspace_id=workspace_id) > 0

    async def count(self, workspace_id: str | None = None) -> int:
        scope = self._normalize_workspace_id(workspace_id)
        return await asyncio.to_thread(self._count, scope)

    async def count_document_chunks(
        self,
        document_id: str,
        workspace_id: str,
    ) -> int:
        normalized_document_id = document_id.strip()
        if not normalized_document_id:
            raise ValueError("document_id cannot be empty")
        scope = self._normalize_workspace_id(workspace_id)
        return await asyncio.to_thread(
            self._count_document_chunks,
            normalized_document_id,
            scope,
        )

    async def list_document_keys(
        self,
        workspace_id: str | None = None,
    ) -> list[VectorDocumentKey]:
        scope = (
            None if workspace_id is None else self._normalize_workspace_id(workspace_id)
        )
        return await asyncio.to_thread(self._list_document_keys, scope)

    async def delete_document(
        self,
        document_id: str,
        workspace_id: str | None = None,
    ) -> int:
        normalized_document_id = document_id.strip()
        if not normalized_document_id:
            raise ValueError("document_id cannot be empty")

        scope = self._normalize_workspace_id(workspace_id)
        return await asyncio.to_thread(
            self._delete_document,
            normalized_document_id,
            scope,
        )

    def _upsert_chunks(
        self,
        chunks: list[DocumentChunk],
        embeddings: list[list[float]],
    ) -> int:
        collection = self._get_collection()
        document_id = chunks[0].document_id
        workspace_id = self._normalize_workspace_id(chunks[0].workspace_id)
        existing = collection.get(
            where=self._document_where(document_id, workspace_id),
            include=[],
        )
        existing_ids = set(existing.get("ids") or [])

        try:
            for start in range(0, len(chunks), UPSERT_BATCH_SIZE):
                batch_chunks = chunks[start : start + UPSERT_BATCH_SIZE]
                batch_embeddings = embeddings[start : start + UPSERT_BATCH_SIZE]
                collection.upsert(
                    ids=[chunk.id for chunk in batch_chunks],
                    embeddings=batch_embeddings,
                    documents=[chunk.text for chunk in batch_chunks],
                    metadatas=[self._chunk_metadata(chunk) for chunk in batch_chunks],
                )

            new_ids = {chunk.id for chunk in chunks}
            stale_ids = existing_ids - new_ids
            if stale_ids:
                collection.delete(ids=sorted(stale_ids))
        except Exception:
            with suppress(Exception):
                self._delete_document(document_id, workspace_id)
            raise
        return len(chunks)

    def _query(
        self,
        query_embedding: list[float],
        top_k: int,
        similarity_threshold: float,
        workspace_id: str,
    ) -> list[RetrievedChunk]:
        collection = self._get_collection()
        collection_count = self._collection_count(collection, workspace_id)
        if collection_count == 0:
            return []

        response = collection.query(
            query_embeddings=[query_embedding],
            n_results=min(top_k, collection_count),
            include=["documents", "metadatas", "distances"],
            where={"workspace_id": workspace_id},
        )
        ids = (response.get("ids") or [[]])[0]
        documents = (response.get("documents") or [[]])[0]
        metadatas = (response.get("metadatas") or [[]])[0]
        distances = (response.get("distances") or [[]])[0]

        results = []
        for chunk_id, text, metadata, distance in zip(
            ids,
            documents,
            metadatas,
            distances,
            strict=True,
        ):
            if text is None or metadata is None or distance is None:
                continue
            score = 1.0 - float(distance)
            if score < similarity_threshold:
                continue
            results.append(
                RetrievedChunk(
                    id=chunk_id,
                    document_id=str(metadata["document_id"]),
                    workspace_id=str(metadata["workspace_id"]),
                    original_filename=str(metadata["original_filename"]),
                    stored_filename=str(metadata["stored_filename"]),
                    extension=str(metadata["extension"]),
                    chunk_index=int(metadata["chunk_index"]),
                    text=text,
                    character_count=int(metadata["character_count"]),
                    score=score,
                )
            )
        return results

    def _count(self, workspace_id: str) -> int:
        collection = self._get_collection()
        return self._collection_count(collection, workspace_id)

    def _count_document_chunks(self, document_id: str, workspace_id: str) -> int:
        collection = self._get_collection()
        response = collection.get(
            where=self._document_where(document_id, workspace_id),
            include=[],
        )
        return len(response.get("ids") or [])

    def _list_document_keys(
        self,
        workspace_id: str | None,
    ) -> list[VectorDocumentKey]:
        collection = self._get_collection()
        query_options: dict[str, Any] = {"include": ["metadatas"]}
        if workspace_id is not None:
            query_options["where"] = {"workspace_id": workspace_id}
        response = collection.get(**query_options)
        metadatas = response.get("metadatas") or []

        counts: dict[tuple[str, str], int] = {}
        for metadata in metadatas:
            if not metadata:
                continue
            document_id = metadata.get("document_id")
            metadata_workspace_id = metadata.get("workspace_id")
            if not document_id or not metadata_workspace_id:
                continue
            key = (str(metadata_workspace_id), str(document_id))
            counts[key] = counts.get(key, 0) + 1

        return [
            VectorDocumentKey(
                workspace_id=key[0],
                document_id=key[1],
                chunk_count=chunk_count,
            )
            for key, chunk_count in sorted(counts.items())
        ]

    def _collection_count(self, collection: Any, workspace_id: str) -> int:
        response = collection.get(
            where={"workspace_id": workspace_id},
            include=[],
        )
        return len(response.get("ids") or [])

    def _delete_document(self, document_id: str, workspace_id: str) -> int:
        collection = self._get_collection()
        existing = collection.get(
            where=self._document_where(document_id, workspace_id),
            include=[],
        )
        ids = existing.get("ids") or []
        if not ids:
            return 0
        collection.delete(ids=ids)
        return len(ids)

    def _get_collection(self) -> Any:
        collection = self._get_client().get_or_create_collection(
            name=self.collection_name,
            metadata={
                "hnsw:space": "cosine",
                "embedding_model": self.embedding_model_name,
            },
        )
        collection_metadata = collection.metadata or {}
        stored_model = collection_metadata.get("embedding_model")
        if stored_model != self.embedding_model_name:
            raise ValueError(
                "Chroma collection embedding model does not match configuration"
            )
        return collection

    def _get_client(self) -> ClientAPI:
        if self._client is not None:
            return self._client

        with self._client_lock:
            if self._client is None:
                self.persist_dir.mkdir(parents=True, exist_ok=True)
                self._client = self._create_client()
        return self._client

    def _create_client(self) -> ClientAPI:
        if self._client_factory is not None:
            return self._client_factory(str(self.persist_dir))

        import chromadb

        return chromadb.PersistentClient(path=str(self.persist_dir))

    def _resolve_persist_dir(self, persist_dir: str | Path) -> Path:
        path = Path(persist_dir)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path.resolve()

    def _validate_upsert(
        self,
        chunks: list[DocumentChunk],
        embeddings: list[list[float]],
    ) -> None:
        if not chunks:
            raise ValueError("at least one document chunk is required")
        if len(chunks) != len(embeddings):
            raise ValueError("chunk and embedding counts must match")
        if any(not vector for vector in embeddings):
            raise ValueError("embedding vectors cannot be empty")

        document_ids = {chunk.document_id for chunk in chunks}
        if len(document_ids) != 1:
            raise ValueError("all chunks must belong to the same document")

        workspace_ids = {
            self._normalize_workspace_id(chunk.workspace_id) for chunk in chunks
        }
        if len(workspace_ids) != 1:
            raise ValueError("all chunks must belong to the same workspace")

    def _chunk_metadata(self, chunk: DocumentChunk) -> dict[str, str | int]:
        return {
            "document_id": chunk.document_id,
            "workspace_id": self._normalize_workspace_id(chunk.workspace_id),
            "original_filename": chunk.original_filename,
            "stored_filename": chunk.stored_filename,
            "extension": chunk.extension,
            "chunk_index": chunk.chunk_index,
            "character_count": chunk.character_count,
        }

    def _normalize_workspace_id(self, workspace_id: str | None) -> str:
        return workspace_id or GLOBAL_WORKSPACE_ID

    def _document_where(self, document_id: str, workspace_id: str) -> dict[str, Any]:
        return {
            "$and": [
                {"document_id": document_id},
                {"workspace_id": workspace_id},
            ]
        }


vector_store = ChromaVectorStore()
