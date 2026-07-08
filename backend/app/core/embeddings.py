import asyncio
import threading
from collections.abc import Callable, Sequence
from typing import Any

from app.core.config import settings


class SentenceTransformerEmbeddings:
    def __init__(
        self,
        model_name: str | None = None,
        model_factory: Callable[[str], Any] | None = None,
    ):
        self.model_name = model_name or settings.embedding_model_name
        self._model_factory = model_factory
        self._model: Any | None = None
        self._model_lock = threading.Lock()

    async def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        prepared_texts = self._prepare_texts(texts, prefix="passage: ")
        return await asyncio.to_thread(self._encode, prepared_texts)

    async def embed_query(self, text: str) -> list[float]:
        prepared_texts = self._prepare_texts([text], prefix="query: ")
        vectors = await asyncio.to_thread(self._encode, prepared_texts)
        return vectors[0]

    def _prepare_texts(self, texts: Sequence[str], prefix: str) -> list[str]:
        if not texts:
            raise ValueError("at least one text is required for embedding")

        prepared_texts = []
        for text in texts:
            normalized_text = text.strip()
            if not normalized_text:
                raise ValueError("embedding text cannot be empty")
            prepared_texts.append(f"{prefix}{normalized_text}")
        return prepared_texts

    def _encode(self, texts: list[str]) -> list[list[float]]:
        model = self._get_model()
        encoded = model.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [[float(component) for component in vector] for vector in encoded]

    def _get_model(self) -> Any:
        if self._model is not None:
            return self._model

        with self._model_lock:
            if self._model is None:
                self._model = self._create_model()
        return self._model

    def _create_model(self) -> Any:
        if self._model_factory is not None:
            return self._model_factory(self.model_name)

        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(self.model_name)


embedding_service = SentenceTransformerEmbeddings()
