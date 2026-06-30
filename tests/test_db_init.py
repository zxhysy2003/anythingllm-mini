from alembic import command
from sqlalchemy import inspect, text
from sqlmodel import Session, create_engine

from app.db.init_db import alembic_config, create_db_and_tables
from app.models.conversation import ConversationMessage


def sqlite_url(database_path) -> str:
    return f"sqlite:///{database_path}"


def test_create_db_and_tables_runs_alembic_upgrade_for_new_database(tmp_path):
    database_url = sqlite_url(tmp_path / "new.db")
    engine = create_engine(database_url)

    create_db_and_tables(engine)

    inspector = inspect(engine)
    assert "alembic_version" in inspector.get_table_names()
    assert "metrics" in {
        column["name"] for column in inspector.get_columns("conversation_messages")
    }
    with engine.connect() as connection:
        version = connection.execute(text("select version_num from alembic_version"))
    assert version.scalar_one() == "0002_add_message_metrics"


def test_existing_sqlite_without_metrics_can_be_stamped_and_upgraded(tmp_path):
    database_url = sqlite_url(tmp_path / "old.db")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("""
                CREATE TABLE conversation_messages (
                    id VARCHAR NOT NULL PRIMARY KEY,
                    conversation_id VARCHAR NOT NULL,
                    role VARCHAR(16) NOT NULL,
                    content VARCHAR NOT NULL,
                    sources JSON NOT NULL,
                    provider VARCHAR(64),
                    model VARCHAR(255),
                    created_at DATETIME NOT NULL
                )
                """))
        connection.execute(text("""
                INSERT INTO conversation_messages (
                    id,
                    conversation_id,
                    role,
                    content,
                    sources,
                    provider,
                    model,
                    created_at
                )
                VALUES (
                    'm1',
                    'c1',
                    'assistant',
                    'answer',
                    '[]',
                    'deepseek',
                    'deepseek-v4-flash',
                    '2026-01-01 00:00:00.000000'
                )
                """))

    config = alembic_config(database_url)
    command.stamp(config, "0001_baseline_v3_schema")
    command.upgrade(config, "head")

    columns = {
        column["name"]
        for column in inspect(engine).get_columns("conversation_messages")
    }
    assert "metrics" in columns
    with Session(engine) as session:
        message = session.get(ConversationMessage, "m1")
        assert message is not None
        assert message.content == "answer"
        assert message.metrics == {}
