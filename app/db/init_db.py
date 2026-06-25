from sqlalchemy.engine import Engine
from sqlmodel import SQLModel

from app.db.session import engine
from app.models import Conversation, ConversationMessage, Workspace, WorkspaceDocument


def create_db_and_tables(db_engine: Engine = engine) -> None:
    # Importing the models above registers their tables on SQLModel.metadata.
    _ = (Conversation, ConversationMessage, Workspace, WorkspaceDocument)
    SQLModel.metadata.create_all(db_engine)
