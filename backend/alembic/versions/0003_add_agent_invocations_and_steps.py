"""add agent invocations and steps

Revision ID: 0003_add_agent_invocations_and_steps
Revises: 0002_add_message_metrics
Create Date: 2026-07-04 00:00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0003_add_agent_invocations_and_steps"
down_revision: Union[str, Sequence[str], None] = "0002_add_message_metrics"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_invocations",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("workspace_id", sa.String(), nullable=False),
        sa.Column("conversation_id", sa.String(), nullable=False),
        sa.Column("user_message_id", sa.String(), nullable=False),
        sa.Column("assistant_message_id", sa.String(), nullable=False),
        sa.Column("input_message", sa.String(), nullable=False),
        sa.Column("agent_mode", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=True),
        sa.Column("model", sa.String(length=255), nullable=True),
        sa.Column("max_steps", sa.Integer(), nullable=False),
        sa.Column("llm_call_count", sa.Integer(), nullable=False),
        sa.Column("step_count", sa.Integer(), nullable=False),
        sa.Column("tool_call_count", sa.Integer(), nullable=False),
        sa.Column("failed_step_count", sa.Integer(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.Column("max_steps_reached", sa.Boolean(), nullable=False),
        sa.Column("total_latency_ms", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["assistant_message_id"],
            ["conversation_messages.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_message_id"],
            ["conversation_messages.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id"],
            ["workspaces.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_agent_invocations_assistant_message_id",
        "agent_invocations",
        ["assistant_message_id"],
    )
    op.create_index(
        "ix_agent_invocations_conversation_id",
        "agent_invocations",
        ["conversation_id"],
    )
    op.create_index(
        "ix_agent_invocations_created_at",
        "agent_invocations",
        ["created_at"],
    )
    op.create_index(
        "ix_agent_invocations_user_message_id",
        "agent_invocations",
        ["user_message_id"],
    )
    op.create_index(
        "ix_agent_invocations_workspace_id",
        "agent_invocations",
        ["workspace_id"],
    )

    op.create_table(
        "agent_steps",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("invocation_id", sa.String(), nullable=False),
        sa.Column("step_index", sa.Integer(), nullable=False),
        sa.Column("llm_output", sa.String(), nullable=False),
        sa.Column("action", sa.String(length=255), nullable=True),
        sa.Column("action_input", sa.JSON(), nullable=False),
        sa.Column("observation", sa.String(), nullable=True),
        sa.Column("ok", sa.Boolean(), nullable=False),
        sa.Column("error", sa.String(length=255), nullable=True),
        sa.Column("tool_result", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["invocation_id"],
            ["agent_invocations.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_steps_created_at", "agent_steps", ["created_at"])
    op.create_index("ix_agent_steps_invocation_id", "agent_steps", ["invocation_id"])
    op.create_index("ix_agent_steps_step_index", "agent_steps", ["step_index"])


def downgrade() -> None:
    op.drop_index("ix_agent_steps_step_index", table_name="agent_steps")
    op.drop_index("ix_agent_steps_invocation_id", table_name="agent_steps")
    op.drop_index("ix_agent_steps_created_at", table_name="agent_steps")
    op.drop_table("agent_steps")

    op.drop_index("ix_agent_invocations_workspace_id", table_name="agent_invocations")
    op.drop_index(
        "ix_agent_invocations_user_message_id",
        table_name="agent_invocations",
    )
    op.drop_index("ix_agent_invocations_created_at", table_name="agent_invocations")
    op.drop_index(
        "ix_agent_invocations_conversation_id",
        table_name="agent_invocations",
    )
    op.drop_index(
        "ix_agent_invocations_assistant_message_id",
        table_name="agent_invocations",
    )
    op.drop_table("agent_invocations")
