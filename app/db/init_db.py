from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine

from app.core.config import PROJECT_ROOT, settings
from app.db.session import _resolve_database_url


def alembic_config(database_url: str | None = None) -> Config:
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(PROJECT_ROOT / "alembic"))
    config.set_main_option(
        "sqlalchemy.url",
        _resolve_database_url(database_url or settings.database_url),
    )
    return config


def run_migrations(database_url: str | None = None) -> None:
    command.upgrade(alembic_config(database_url), "head")


def create_db_and_tables(db_engine: Engine | None = None) -> None:
    database_url = None
    if db_engine is not None:
        database_url = db_engine.url.render_as_string(hide_password=False)
    run_migrations(database_url)
