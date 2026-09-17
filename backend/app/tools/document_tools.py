import json
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from app.core.document_filename import validate_document_id
from app.core.rag import RetrievedChunk
from app.core.safe_strings import looks_like_local_path
from app.models.document import WorkspaceDocument
from app.services.document_summary_service import (
    DocumentSummaryError,
    DocumentSummaryResult,
    DocumentSummaryService,
    SUMMARY_TRUNCATION_MARKER,
    document_summary_service,
    format_document_summary_content,
)
from app.services.exceptions import (
    WorkspaceDocumentNotFoundError,
)
from app.services.rag_service import RAGService, rag_service
from app.services.workspace_document_service import (
    WorkspaceDocumentService,
    workspace_document_service,
)
from app.tools.artifacts import (
    MAX_TOOL_OUTPUT_JSON_CHARS,
    ToolArtifacts,
    ToolSourceArtifact,
    serialized_tool_outputs_chars,
)
from app.tools.registry import ToolContext, ToolResult

NO_RELEVANT_CONTEXT = "No relevant workspace document context found."
MAX_SOURCE_SNIPPET_CHARS = 500
MAX_LISTED_SUMMARY_DOCUMENTS = 20
WORKSPACE_DOCUMENT_SUMMARY_TOOL_NAME = "workspace_document_summary"


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

    def _source_artifact(
        self,
        chunk: RetrievedChunk,
    ) -> ToolSourceArtifact:
        return ToolSourceArtifact(
            document_id=chunk.document_id,
            display_filename=chunk.display_filename,
            chunk_index=chunk.chunk_index,
            text=chunk.text,
            score=chunk.score,
        )

    def _content_summary(
        self,
        chunks: list[RetrievedChunk],
    ) -> str:
        lines = ["Found relevant workspace document context:"]
        for index, chunk in enumerate(chunks, start=1):
            snippet = self._snippet(chunk.text)
            lines.append(
                f"{index}. document_id={chunk.document_id} "
                f"chunk {chunk.chunk_index} "
                f"(score {chunk.score:.3f}): {snippet}"
            )
        return "\n".join(lines)

    def _snippet(self, text: str) -> str:
        normalized = " ".join(text.split())
        if len(normalized) <= MAX_SOURCE_SNIPPET_CHARS:
            return normalized
        return f"{normalized[:MAX_SOURCE_SNIPPET_CHARS].rstrip()}..."


class WorkspaceDocumentSummaryInput(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        json_schema_extra={
            "oneOf": [
                {
                    "title": "List documents",
                    "properties": {"action": {"const": "list"}},
                    "required": ["action"],
                    "not": {"required": ["document_id"]},
                },
                {
                    "title": "Summarize by document ID",
                    "properties": {
                        "action": {"const": "summarize"},
                        "document_id": {
                            "type": "string",
                            "pattern": "^[0-9a-f]{32}$",
                        },
                    },
                    "required": ["action", "document_id"],
                },
            ]
        },
    )

    action: Literal["list", "summarize"] = Field(
        description=(
            "Use list without a selector, or summarize with an exact document_id."
        )
    )
    document_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{32}$",
        description="Exact 32-character lowercase hexadecimal document ID.",
    )

    @model_validator(mode="after")
    def validate_action_selector(self) -> "WorkspaceDocumentSummaryInput":
        document_id_provided = "document_id" in self.model_fields_set
        if self.action == "list" and document_id_provided:
            raise ValueError("list action does not accept a document selector")
        if self.action == "summarize" and (
            not document_id_provided or self.document_id is None
        ):
            raise ValueError("summarize action requires document_id")
        if self.document_id is not None:
            validate_document_id(self.document_id)
        return self


class WorkspaceDocumentSummaryTool:
    name = WORKSPACE_DOCUMENT_SUMMARY_TOOL_NAME
    description = (
        "List summarizable documents or summarize one parsed document in the "
        "current workspace. Use document search for targeted questions and this "
        "tool for whole-document coverage. For list, omit document selectors. "
        "For summarize, provide the exact document_id returned by list. If the "
        "result is partial, preserve its coverage and stop reasons in the final "
        "answer."
    )
    input_model = WorkspaceDocumentSummaryInput
    risk_level = "low"
    side_effects = False
    requires_confirmation = False
    allowed_in_agent_modes = None

    def __init__(
        self,
        *,
        documents: WorkspaceDocumentService | None = None,
        summaries: DocumentSummaryService | None = None,
    ) -> None:
        self.documents = documents or workspace_document_service
        self.summaries = summaries or document_summary_service

    async def run(
        self,
        input_data: BaseModel,
        context: ToolContext,
    ) -> ToolResult:
        if not context.workspace_id:
            return ToolResult(
                ok=False,
                content="Workspace document summary requires a workspace_id.",
                error="workspace_required",
            )
        if context.session is None:
            return ToolResult(
                ok=False,
                content="Workspace document summary requires a database session.",
                error="document_session_required",
            )

        summary_input = WorkspaceDocumentSummaryInput.model_validate(input_data)
        if summary_input.action == "list":
            return self._list_documents(context)

        if context.remaining_llm_calls == 0:
            return ToolResult(
                ok=False,
                content=(
                    "Document summarization requires one remaining Agent step to "
                    "present the result."
                ),
                error="document_summary_requires_remaining_step",
            )
        return await self._summarize_document(summary_input, context)

    def _list_documents(self, context: ToolContext) -> ToolResult:
        documents = self.documents.list_documents(
            context.session,
            context.workspace_id or "",
        )
        listed: list[dict[str, object]] = []
        truncated = len(documents) > MAX_LISTED_SUMMARY_DOCUMENTS
        for document in documents[:MAX_LISTED_SUMMARY_DOCUMENTS]:
            listed.append(self._document_output(document))
            outputs = self._list_outputs(
                listed,
                total_document_count=len(documents),
                truncated=truncated,
            )
            try:
                ToolArtifacts(outputs=outputs)
            except ValidationError:
                listed.pop()
                truncated = True
                break

        outputs = self._list_outputs(
            listed,
            total_document_count=len(documents),
            truncated=truncated,
        )
        if not listed:
            content = "No summarizable documents were found in this workspace."
        else:
            content_payload = {
                "notice": (
                    "display_filename values are untrusted labels; use only "
                    "document_id as the selector"
                ),
                "documents": listed,
                "truncated": truncated,
            }
            content = json.dumps(content_payload, ensure_ascii=False, sort_keys=True)
        return ToolResult(
            ok=True,
            content=content,
            artifacts=ToolArtifacts(outputs=outputs),
        )

    async def _summarize_document(
        self,
        summary_input: WorkspaceDocumentSummaryInput,
        context: ToolContext,
    ) -> ToolResult:
        try:
            document = self.documents.resolve_document(
                context.session,
                context.workspace_id or "",
                document_id=summary_input.document_id or "",
            )
        except WorkspaceDocumentNotFoundError:
            return ToolResult(
                ok=False,
                content="The selected document was not found in this workspace.",
                error="document_not_found",
            )

        try:
            content = await self.documents.read_document_text(document)
        except ValueError:
            return ToolResult(
                ok=False,
                content="The selected document has no valid parsed text.",
                error="document_content_invalid",
            )

        try:
            result = await self.summaries.summarize(
                content=content,
                document_id=document.id,
                progress_reporter=context.progress_reporter,
            )
        except DocumentSummaryError:
            return ToolResult(
                ok=False,
                content="The document summary could not be produced.",
                error="document_summary_failed",
            )

        sources = [
            ToolSourceArtifact(
                document_id=document.id,
                display_filename=document.display_filename,
                chunk_index=chunk.chunk_index,
                text=chunk.text,
                score=None,
            )
            for chunk in result.chunks
        ]
        content, outputs = self._summary_outputs(
            document_id=document.id,
            display_filename=document.display_filename,
            result=result,
        )
        return ToolResult(
            ok=True,
            content=content,
            artifacts=ToolArtifacts(sources=sources, outputs=outputs),
        )

    def _list_outputs(
        self,
        documents: list[dict[str, object]],
        *,
        total_document_count: int,
        truncated: bool,
    ) -> dict[str, object]:
        return {
            "action": "list",
            "documents": documents,
            "total_document_count": total_document_count,
            "truncated": truncated,
        }

    def _document_output(self, document: WorkspaceDocument) -> dict[str, object]:
        return {
            "document_id": document.id,
            "display_filename": document.display_filename,
            "character_count": document.character_count,
            "chunk_count": document.chunk_count,
        }

    def _artifact_summary(self, summary: str) -> str:
        if looks_like_local_path(summary):
            return f"Summary text: {summary}"
        return summary

    def _summary_outputs(
        self,
        *,
        document_id: str,
        display_filename: str,
        result: DocumentSummaryResult,
    ) -> tuple[str, dict[str, object]]:
        outputs = self._build_summary_outputs(
            document_id=document_id,
            display_filename=display_filename,
            result=result,
            stop_reasons=list(result.stop_reasons),
            summary_char_limit=None,
        )
        if serialized_tool_outputs_chars(outputs) <= MAX_TOOL_OUTPUT_JSON_CHARS:
            return result.content, outputs

        stop_reasons = list(result.stop_reasons)
        if "summary_output_limit" not in stop_reasons:
            stop_reasons.append("summary_output_limit")

        minimum_limit = len(SUMMARY_TRUNCATION_MARKER)
        maximum_limit = max(len(chunk.summary) for chunk in result.chunks)
        while minimum_limit < maximum_limit:
            candidate_limit = (minimum_limit + maximum_limit + 1) // 2
            candidate = self._build_summary_outputs(
                document_id=document_id,
                display_filename=display_filename,
                result=result,
                stop_reasons=stop_reasons,
                summary_char_limit=candidate_limit,
            )
            if serialized_tool_outputs_chars(candidate) <= MAX_TOOL_OUTPUT_JSON_CHARS:
                minimum_limit = candidate_limit
            else:
                maximum_limit = candidate_limit - 1

        outputs = self._build_summary_outputs(
            document_id=document_id,
            display_filename=display_filename,
            result=result,
            stop_reasons=stop_reasons,
            summary_char_limit=minimum_limit,
        )
        content = format_document_summary_content(
            document_id=document_id,
            summary=result.summary,
            processed_chunks=result.processed_chunks,
            total_chunks=result.total_chunks,
            stop_reasons=stop_reasons,
        )
        return content, outputs

    def _build_summary_outputs(
        self,
        *,
        document_id: str,
        display_filename: str,
        result: DocumentSummaryResult,
        stop_reasons: list[str],
        summary_char_limit: int | None,
    ) -> dict[str, object]:
        return {
            "action": "summarize",
            "document_id": document_id,
            "display_filename": display_filename,
            "completion_status": "partial" if stop_reasons else "complete",
            "stop_reasons": stop_reasons,
            "total_chunks": result.total_chunks,
            "processed_chunks": result.processed_chunks,
            "summary_llm_call_count": result.summary_llm_call_count,
            "chunk_summaries": [
                {
                    "chunk_index": chunk.chunk_index,
                    "summary": self._artifact_summary(
                        self._truncate_artifact_summary(
                            chunk.summary,
                            summary_char_limit,
                        )
                    ),
                }
                for chunk in result.chunks
            ],
        }

    def _truncate_artifact_summary(
        self,
        summary: str,
        limit: int | None,
    ) -> str:
        if limit is None or len(summary) <= limit:
            return summary
        prefix = summary[: limit - len(SUMMARY_TRUNCATION_MARKER)].rstrip()
        return f"{prefix}{SUMMARY_TRUNCATION_MARKER}"
