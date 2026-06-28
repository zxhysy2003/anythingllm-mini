from pathlib import Path

from app.core.config import PROJECT_ROOT
from app.db.session import _resolve_database_url


def test_resolve_database_url_anchors_relative_sqlite_path_to_project_root():
    resolved_url = _resolve_database_url("sqlite:///./anythingllm_mini.db")

    assert resolved_url.startswith("sqlite:////")
    assert Path(resolved_url.removeprefix("sqlite:///")) == (
        PROJECT_ROOT / "anythingllm_mini.db"
    )


def test_resolve_database_url_leaves_memory_sqlite_url_unchanged():
    assert _resolve_database_url("sqlite://") == "sqlite://"
    assert _resolve_database_url("sqlite:///:memory:") == "sqlite:///:memory:"


def test_resolve_database_url_leaves_non_sqlite_url_unchanged():
    url = "postgresql+psycopg2://postgres:postgres@localhost/db"

    assert _resolve_database_url(url) == url
