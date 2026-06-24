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
