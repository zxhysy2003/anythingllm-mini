import asyncio
import threading

import pytest

from app.core.embeddings import SentenceTransformerEmbeddings


class FakeSentenceTransformer:
    def __init__(self):
        self.calls = []

    def encode(self, texts, **options):
        self.calls.append((texts, options, threading.get_ident()))
        return [[0.6, 0.8] for _ in texts]


def test_embedding_wrapper_uses_e5_prefixes_and_reuses_model():
    fake_model = FakeSentenceTransformer()
    loaded_models = []

    def create_model(model_name):
        loaded_models.append(model_name)
        return fake_model

    embeddings = SentenceTransformerEmbeddings(
        model_name="intfloat/multilingual-e5-small",
        model_factory=create_model,
    )
    main_thread = threading.get_ident()

    document_vectors = asyncio.run(embeddings.embed_documents([" first ", "second"]))
    query_vector = asyncio.run(embeddings.embed_query(" question "))

    assert loaded_models == ["intfloat/multilingual-e5-small"]
    assert fake_model.calls[0][0] == ["passage: first", "passage: second"]
    assert fake_model.calls[1][0] == ["query: question"]
    assert fake_model.calls[0][1]["normalize_embeddings"] is True
    assert fake_model.calls[0][1]["convert_to_numpy"] is True
    assert fake_model.calls[0][2] != main_thread
    assert document_vectors == [[0.6, 0.8], [0.6, 0.8]]
    assert query_vector == [0.6, 0.8]


def test_embedding_wrapper_rejects_empty_input():
    embeddings = SentenceTransformerEmbeddings(model_factory=lambda _: None)

    with pytest.raises(ValueError, match="at least one text"):
        asyncio.run(embeddings.embed_documents([]))

    with pytest.raises(ValueError, match="embedding text cannot be empty"):
        asyncio.run(embeddings.embed_query("   "))
