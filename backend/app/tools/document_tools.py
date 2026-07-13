from pydantic import BaseModel, Field

from app.core.rag import RetrievedChunk
from app.services.rag_service import RAGService, rag_service
from app.tools.artifacts import ToolArtifacts, ToolSourceArtifact
from app.tools.registry import ToolContext, ToolResult

NO_RELEVANT_CONTEXT = "No relevant workspace document context found."
MAX_SOURCE_SNIPPET_CHARS = 500


class WorkspaceDocumentSearchInput(BaseModel):
    question: str = Field(min_length=1)
    top_k: int | None = Field(default=None, gt=0)
    similarity_threshold: float | None = Field(default=None, ge=0, le=1)


class WorkspaceDocumentSearchTool:
    name = "workspace_document_search"
    description = "Search indexed documents in the current workspace."
    input_model = WorkspaceDocumentSearchInput
    risk_level = "low"
    side_effects = False
    requires_confirmation = False
    allowed_in_agent_modes = None

    def __init__(self, rag: RAGService | None = None):
        self.rag = rag or rag_service

    async def run(
        self,
        input_data: BaseModel,
        context: ToolContext,
    ) -> ToolResult:
        if not context.workspace_id:
            return ToolResult(
                ok=False,
                content="Workspace document search requires a workspace_id.",
                error="workspace_required",
            )

        search_input = WorkspaceDocumentSearchInput.model_validate(input_data)
        chunks = await self.rag.retrieve(
            search_input.question,
            workspace_id=context.workspace_id,
            top_k=search_input.top_k,
            similarity_threshold=search_input.similarity_threshold,
        )
        sources = [self._source_artifact(chunk) for chunk in chunks]
        if not sources:
            return ToolResult(
                ok=True,
                content=NO_RELEVANT_CONTEXT,
            )

        return ToolResult(
            ok=True,
            content=self._content_summary(chunks),
            artifacts=ToolArtifacts(sources=sources),
        )

    def _source_artifact(self, chunk: RetrievedChunk) -> ToolSourceArtifact:
        return ToolSourceArtifact(
            document_id=chunk.document_id,
            original_filename=chunk.original_filename,
            chunk_index=chunk.chunk_index,
            text=chunk.text,
            score=chunk.score,
        )

    def _content_summary(self, chunks: list[RetrievedChunk]) -> str:
        lines = ["Found relevant workspace document context:"]
        for index, chunk in enumerate(chunks, start=1):
            snippet = self._snippet(chunk.text)
            lines.append(
                f"{index}. {chunk.original_filename} "
                f"chunk {chunk.chunk_index} "
                f"(score {chunk.score:.3f}): {snippet}"
            )
        return "\n".join(lines)

    def _snippet(self, text: str) -> str:
        normalized = " ".join(text.split())
        if len(normalized) <= MAX_SOURCE_SNIPPET_CHARS:
            return normalized
        return f"{normalized[:MAX_SOURCE_SNIPPET_CHARS].rstrip()}..."
