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


def test_settings_accepts_chroma_vector_store():
    configured_settings = Settings(vector_store="chroma")

    assert configured_settings.vector_store == "chroma"


def test_settings_rejects_unimplemented_qdrant_vector_store():
    with pytest.raises(ValidationError):
        Settings(vector_store="qdrant")
