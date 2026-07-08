"""add message metrics

Revision ID: 0002_add_message_metrics
Revises: 0001_baseline_v3_schema
Create Date: 2026-06-29 21:41:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0002_add_message_metrics"
down_revision: Union[str, Sequence[str], None] = "0001_baseline_v3_schema"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversation_messages",
        sa.Column(
            "metrics",
            sa.JSON(),
            nullable=False,
            server_default="{}",
        ),
    )


def downgrade() -> None:
    op.drop_column("conversation_messages", "metrics")
