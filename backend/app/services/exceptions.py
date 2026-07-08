class ChatServiceError(RuntimeError):
    """Raised when the LLM call fails inside the chat service."""


class RAGIndexError(RuntimeError):
    """Raised when parsed document text cannot be indexed."""


class RAGQueryError(RuntimeError):
    """Raised when document retrieval fails."""


class AgentInvocationNotFoundError(LookupError):
    """Raised when an agent invocation does not belong to the conversation."""


class WorkspaceNotFoundError(LookupError):
    """Raised when a workspace id does not exist."""


class ConversationNotFoundError(LookupError):
    """Raised when a conversation does not belong to the workspace."""


class WorkspaceDocumentNotFoundError(LookupError):
    """Raised when a document does not belong to the workspace."""


class WorkspacePersistenceError(RuntimeError):
    """Raised when workspace metadata or messages cannot be persisted."""
