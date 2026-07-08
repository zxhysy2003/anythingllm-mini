from collections.abc import Generator
from pathlib import Path

from sqlalchemy.engine import make_url
from sqlmodel import Session, create_engine

from app.core.config import PROJECT_ROOT, settings


def create_db_engine(database_url: str | None = None):
    url = _resolve_database_url(database_url or settings.database_url)
    connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
    return create_engine(url, connect_args=connect_args)


def _resolve_database_url(database_url: str) -> str:
    url = make_url(database_url)
    if not url.drivername.startswith("sqlite"):
        return database_url
    if url.database in {None, "", ":memory:"}:
        return database_url

    database_path = Path(url.database)
    if not database_path.is_absolute():
        database_path = PROJECT_ROOT / database_path
    return str(url.set(database=str(database_path)))


engine = create_db_engine()


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session
