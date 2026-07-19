from alembic import command
import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, create_engine, select

from app.core.config import BACKEND_ROOT
from app.db.init_db import alembic_config, create_db_and_tables
from app.models.agent import AgentInvocation, AgentStepRecord
from app.models.conversation import ConversationMessage


def sqlite_url(database_path) -> str:
    return f"sqlite:///{database_path}"


def test_alembic_config_uses_backend_migration_scripts(tmp_path):
    config = alembic_config(sqlite_url(tmp_path / "config.db"))

    assert config.config_file_name == str(BACKEND_ROOT / "alembic.ini")
    assert config.get_main_option("script_location") == str(BACKEND_ROOT / "alembic")


def test_create_db_and_tables_runs_alembic_upgrade_for_new_database(tmp_path):
    database_url = sqlite_url(tmp_path / "new.db")
    engine = create_engine(database_url)

    create_db_and_tables(engine)

    inspector = inspect(engine)
    table_names = inspector.get_table_names()
    assert "alembic_version" in table_names
    assert "agent_invocations" in table_names
    assert "agent_steps" in table_names
    assert "metrics" in {
        column["name"] for column in inspector.get_columns("conversation_messages")
    }
    with engine.connect() as connection:
        version = connection.execute(text("select version_num from alembic_version"))
    assert version.scalar_one() == "0005_add_agent_invocation_claims"
    invocation_columns = {
        column["name"]: column for column in inspector.get_columns("agent_invocations")
    }
    assert invocation_columns["assistant_message_id"]["nullable"] is True
    assert invocation_columns["ended_at"]["nullable"] is True
    assert "pending_input" in invocation_columns
    assert "resume_state" in invocation_columns
    conversation_columns = {
        column["name"]: column for column in inspector.get_columns("conversations")
    }
    assert "agent_execution_claim_id" in conversation_columns
    assert "agent_execution_claimed_at" in conversation_columns


def test_upgrade_from_0003_preserves_completed_agent_invocations(tmp_path):
    database_url = sqlite_url(tmp_path / "agent-v3.db")
    config = alembic_config(database_url)

    command.upgrade(config, "0003_add_agent_invocations_and_steps")
    engine = create_engine(database_url)
    with engine.begin() as connection:
        connection.execute(text("""
                INSERT INTO conversations (
                    id, workspace_id, title, created_at, updated_at
                ) VALUES (
                    'conversation-1', 'workspace-1', 'Saved conversation',
                    '2026-07-15 00:00:00', '2026-07-15 00:00:00'
                )
                """))
        connection.execute(text("""
                INSERT INTO agent_invocations (
                    id, workspace_id, conversation_id, user_message_id,
                    assistant_message_id, input_message, agent_mode, status,
                    provider, model, max_steps, llm_call_count, step_count,
                    tool_call_count, failed_step_count, source_count,
                    max_steps_reached, total_latency_ms, started_at, ended_at,
                    created_at
                ) VALUES (
                    'invocation-0003', 'workspace-1', 'conversation-1', 'user-1',
                    'assistant-1', 'saved input', 'react_text', 'completed',
                    'fake', 'fake-model', 5, 2, 1, 1, 0, 0, 0, 12,
                    '2026-07-15 00:00:00', '2026-07-15 00:00:01',
                    '2026-07-15 00:00:00'
                )
                """))

    command.upgrade(config, "head")

    with engine.connect() as connection:
        row = connection.execute(text("""
                SELECT assistant_message_id, ended_at, pending_input, resume_state
                FROM agent_invocations
                WHERE id = 'invocation-0003'
                """)).one()
    assert row.assistant_message_id == "assistant-1"
    assert row.ended_at is not None
    assert row.pending_input is None
    assert row.resume_state is None
    with engine.connect() as connection:
        conversation = connection.execute(text("""
                SELECT agent_execution_claim_id, agent_execution_claimed_at
                FROM conversations
                WHERE id = 'conversation-1'
                """)).one()
    assert conversation.agent_execution_claim_id is None
    assert conversation.agent_execution_claimed_at is None
    conversation_columns = {
        column["name"]: column
        for column in inspect(engine).get_columns("conversations")
    }
    assert "agent_execution_claim_id" in conversation_columns
    assert "agent_execution_claimed_at" in conversation_columns
    foreign_key_columns = {
        foreign_key["constrained_columns"][0]
        for foreign_key in inspect(engine).get_foreign_keys("agent_invocations")
    }
    assert {
        "workspace_id",
        "conversation_id",
        "user_message_id",
        "assistant_message_id",
    } <= foreign_key_columns


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
    table_names = inspect(engine).get_table_names()
    assert "agent_invocations" in table_names
    assert "agent_steps" in table_names
    with Session(engine) as session:
        message = session.get(ConversationMessage, "m1")
        assert message is not None
        assert message.content == "answer"
        assert message.metrics == {}
        assert session.exec(select(AgentInvocation)).all() == []
        assert session.exec(select(AgentStepRecord)).all() == []


def test_pending_invocation_index_allows_only_one_waiting_invocation_per_conversation(
    tmp_path,
):
    database_url = sqlite_url(tmp_path / "pending-invocations.db")
    engine = create_engine(database_url)

    create_db_and_tables(engine)

    insert_pending_invocation = text("""
        INSERT INTO agent_invocations (
            id, workspace_id, conversation_id, user_message_id, input_message,
            agent_mode, status, max_steps, llm_call_count, step_count,
            tool_call_count, failed_step_count, source_count, max_steps_reached,
            total_latency_ms, started_at, created_at
        ) VALUES (
            :id, 'workspace-1', 'conversation-1', :user_message_id, 'question',
            'react_text', 'needs_input', 4, 1, 1, 1, 0, 0, 0, 1,
            '2026-07-16 00:00:00', '2026-07-16 00:00:00'
        )
    """)
    with engine.begin() as connection:
        connection.execute(
            insert_pending_invocation,
            {"id": "pending-1", "user_message_id": "user-1"},
        )

    with pytest.raises(IntegrityError, match="agent_invocations.conversation_id"):
        with engine.begin() as connection:
            connection.execute(
                insert_pending_invocation,
                {"id": "pending-2", "user_message_id": "user-2"},
            )
