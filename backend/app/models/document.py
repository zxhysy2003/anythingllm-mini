from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.workspace import utc_now


class WorkspaceDocument(SQLModel, table=True):
    __tablename__ = "workspace_documents"

    id: str = Field(primary_key=True)
    workspace_id: str = Field(
        foreign_key="workspaces.id",
        ondelete="CASCADE",
        index=True,
    )
    display_filename: str = Field(max_length=255)
    content_type: str | None = None
    extension: str = Field(max_length=16)
    size_bytes: int
    character_count: int
    chunk_count: int
    created_at: datetime = Field(default_factory=utc_now)
