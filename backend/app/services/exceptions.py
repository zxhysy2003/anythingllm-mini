class ChatServiceError(RuntimeError):
    """Raised when the LLM call fails inside the chat service."""


class RAGIndexError(RuntimeError):
    """Raised when parsed document text cannot be indexed."""


class RAGQueryError(RuntimeError):
    """Raised when document retrieval fails."""


class AgentInvocationNotFoundError(LookupError):
    """Raised when an agent invocation does not belong to the conversation."""


class AgentInvocationConflictError(RuntimeError):
    """Raised when an Agent invocation cannot enter the requested lifecycle state."""


class WorkspaceNotFoundError(LookupError):
    """Raised when a workspace id does not exist."""


class ConversationNotFoundError(LookupError):
    """Raised when a conversation does not belong to the workspace."""


class WorkspaceDocumentNotFoundError(LookupError):
    """Raised when a document does not belong to the workspace."""


class WorkspaceDocumentAmbiguousError(LookupError):
    """Raised when an exact filename selects multiple workspace documents."""

    def __init__(self, filename: str, document_ids: list[str]) -> None:
        super().__init__(f"multiple workspace documents match filename: {filename}")
        self.filename = filename
        self.document_ids = document_ids


class WorkspacePersistenceError(RuntimeError):
    """Raised when workspace metadata or messages cannot be persisted."""
