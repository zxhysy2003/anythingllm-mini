"""replace persisted file paths and legacy filenames with display filename

Revision ID: 0006_filename_boundaries
Revises: 0005_add_agent_invocation_claims
Create Date: 2026-07-27 00:00:00

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0006_filename_boundaries"
down_revision: Union[str, Sequence[str], None] = "0005_add_agent_invocation_claims"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RESET_REQUIRED_MESSAGE = (
    "document filename boundaries are a breaking upgrade; clear workspace "
    "document records, storage, and Chroma data, then run the migration again"
)


def _workspace_documents_exists() -> bool:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return "workspace_documents" in inspector.get_table_names()


def _require_empty_documents() -> None:
    bind = op.get_bind()
    document_count = bind.execute(
        sa.text("SELECT COUNT(*) FROM workspace_documents")
    ).scalar_one()
    if document_count:
        raise RuntimeError(RESET_REQUIRED_MESSAGE)


def upgrade() -> None:
    if not _workspace_documents_exists():
        return
    _require_empty_documents()
    with op.batch_alter_table("workspace_documents") as batch_op:
        batch_op.add_column(
            sa.Column("display_filename", sa.String(length=255), nullable=False)
        )
        batch_op.drop_column("original_filename")
        batch_op.drop_column("stored_filename")
        batch_op.drop_column("upload_path")
        batch_op.drop_column("parsed_path")


def downgrade() -> None:
    if not _workspace_documents_exists():
        return
    _require_empty_documents()
    with op.batch_alter_table("workspace_documents") as batch_op:
        batch_op.add_column(sa.Column("original_filename", sa.String(), nullable=False))
        batch_op.add_column(sa.Column("stored_filename", sa.String(), nullable=False))
        batch_op.add_column(sa.Column("upload_path", sa.String(), nullable=False))
        batch_op.add_column(sa.Column("parsed_path", sa.String(), nullable=False))
        batch_op.drop_column("display_filename")
