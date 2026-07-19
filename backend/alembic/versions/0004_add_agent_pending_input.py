"""add pending Agent input state

Revision ID: 0004_add_agent_pending_input
Revises: 0003_add_agent_invocations_and_steps
Create Date: 2026-07-15 00:00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0004_add_agent_pending_input"
down_revision: Union[str, Sequence[str], None] = "0003_add_agent_invocations_and_steps"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table(
        "agent_invocations",
        reflect_kwargs={"resolve_fks": False},
    ) as batch_op:
        batch_op.alter_column(
            "assistant_message_id", existing_type=sa.String(), nullable=True
        )
        batch_op.alter_column("ended_at", existing_type=sa.DateTime(), nullable=True)
        batch_op.add_column(sa.Column("pending_input", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("resume_state", sa.JSON(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table(
        "agent_invocations",
        reflect_kwargs={"resolve_fks": False},
    ) as batch_op:
        batch_op.drop_column("resume_state")
        batch_op.drop_column("pending_input")
        batch_op.alter_column("ended_at", existing_type=sa.DateTime(), nullable=False)
        batch_op.alter_column(
            "assistant_message_id",
            existing_type=sa.String(),
            nullable=False,
        )
