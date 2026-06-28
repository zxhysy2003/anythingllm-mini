import pytest
from pydantic import ValidationError

from app.core.config import Settings


@pytest.mark.parametrize("threshold", [0.0, 0.75, 1.0])
def test_settings_accepts_similarity_threshold_boundaries(threshold):
    configured_settings = Settings(similarity_threshold=threshold)

    assert configured_settings.similarity_threshold == threshold


@pytest.mark.parametrize("threshold", [-0.01, 1.01])
def test_settings_rejects_similarity_threshold_outside_range(threshold):
    with pytest.raises(
        ValidationError,
        match="similarity_threshold must be between 0 and 1",
    ):
        Settings(similarity_threshold=threshold)


@pytest.mark.parametrize("max_context_chars", [1, 4000])
def test_settings_accepts_positive_max_context_chars(max_context_chars):
    configured_settings = Settings(max_context_chars=max_context_chars)

    assert configured_settings.max_context_chars == max_context_chars


@pytest.mark.parametrize("max_context_chars", [0, -1])
def test_settings_rejects_non_positive_max_context_chars(max_context_chars):
    with pytest.raises(ValidationError, match="value must be greater than zero"):
        Settings(max_context_chars=max_context_chars)


def test_settings_accepts_chroma_vector_store():
    configured_settings = Settings(vector_store="chroma")

    assert configured_settings.vector_store == "chroma"


def test_settings_rejects_unimplemented_qdrant_vector_store():
    with pytest.raises(ValidationError):
        Settings(vector_store="qdrant")
