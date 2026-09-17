"""add conversation Agent execution claims

Revision ID: 0005_add_agent_invocation_claims
Revises: 0004_add_agent_pending_input
Create Date: 2026-07-16 00:00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0005_add_agent_invocation_claims"
down_revision: Union[str, Sequence[str], None] = "0004_add_agent_pending_input"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PENDING_INVOCATION_INDEX = "uq_agent_invocations_pending_conversation"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "conversations" in inspector.get_table_names():
        op.add_column(
            "conversations",
            sa.Column("agent_execution_claim_id", sa.String(length=64), nullable=True),
        )
        op.add_column(
            "conversations",
            sa.Column("agent_execution_claimed_at", sa.DateTime(), nullable=True),
        )
    op.create_index(
        PENDING_INVOCATION_INDEX,
        "agent_invocations",
        ["conversation_id"],
        unique=True,
        sqlite_where=sa.text("status = 'needs_input'"),
    )


def downgrade() -> None:
    op.drop_index(PENDING_INVOCATION_INDEX, table_name="agent_invocations")
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if "conversations" in inspector.get_table_names():
        op.drop_column("conversations", "agent_execution_claimed_at")
        op.drop_column("conversations", "agent_execution_claim_id")
