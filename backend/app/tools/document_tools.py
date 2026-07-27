from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from app.core.rag import RetrievedChunk
from app.core.safe_strings import looks_like_local_path, validate_safe_basename
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
    WorkspaceDocumentAmbiguousError,
    WorkspaceDocumentNotFoundError,
)
from app.services.rag_service import RAGService, rag_service
from app.services.workspace_document_service import (
    WorkspaceDocumentService,
    document_display_filename,
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
        display_filenames = [
            document_display_filename(
                chunk.original_filename,
                chunk.stored_filename,
            )
            for chunk in chunks
        ]
        sources = [
            self._source_artifact(chunk, display_filename)
            for chunk, display_filename in zip(
                chunks,
                display_filenames,
                strict=True,
            )
        ]
        if not sources:
            return ToolResult(
                ok=True,
                content=NO_RELEVANT_CONTEXT,
            )

        return ToolResult(
            ok=True,
            content=self._content_summary(chunks, display_filenames),
            artifacts=ToolArtifacts(sources=sources),
        )

    def _source_artifact(
        self,
        chunk: RetrievedChunk,
        display_filename: str,
    ) -> ToolSourceArtifact:
        return ToolSourceArtifact(
            document_id=chunk.document_id,
            original_filename=display_filename,
            chunk_index=chunk.chunk_index,
            text=chunk.text,
            score=chunk.score,
        )

    def _content_summary(
        self,
        chunks: list[RetrievedChunk],
        display_filenames: list[str],
    ) -> str:
        lines = ["Found relevant workspace document context:"]
        for index, (chunk, display_filename) in enumerate(
            zip(chunks, display_filenames, strict=True),
            start=1,
        ):
            snippet = self._snippet(chunk.text)
            lines.append(
                f"{index}. {display_filename} "
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
        json_schema_extra={
            "oneOf": [
                {
                    "title": "List documents",
                    "properties": {"action": {"const": "list"}},
                    "required": ["action"],
                    "not": {
                        "anyOf": [
                            {"required": ["document_id"]},
                            {"required": ["filename"]},
                        ]
                    },
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
                    "not": {"required": ["filename"]},
                },
                {
                    "title": "Summarize by filename",
                    "properties": {
                        "action": {"const": "summarize"},
                        "filename": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 255,
                        },
                    },
                    "required": ["action", "filename"],
                    "not": {"required": ["document_id"]},
                },
            ]
        }
    )

    action: Literal["list", "summarize"] = Field(
        description=(
            "Use list without a selector, or summarize with exactly one of "
            "document_id and filename."
        )
    )
    document_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{32}$",
        description="Exact 32-character lowercase hexadecimal document ID.",
    )
    filename: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Exact safe basename in the current workspace.",
    )

    @field_validator("filename")
    @classmethod
    def validate_filename(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return validate_safe_basename(value)

    @model_validator(mode="after")
    def validate_action_selector(self) -> "WorkspaceDocumentSummaryInput":
        provided_selectors = {
            name
            for name in ("document_id", "filename")
            if name in self.model_fields_set
        }
        if self.action == "list" and provided_selectors:
            raise ValueError("list action does not accept a document selector")
        if self.action == "summarize" and (
            len(provided_selectors) != 1
            or any(getattr(self, name) is None for name in provided_selectors)
        ):
            raise ValueError("summarize action requires exactly one document selector")
        return self


class WorkspaceDocumentSummaryTool:
    name = WORKSPACE_DOCUMENT_SUMMARY_TOOL_NAME
    description = (
        "List summarizable documents or summarize one parsed document in the "
        "current workspace. Use document search for targeted questions and this "
        "tool for whole-document coverage. For list, omit document selectors. "
        "For summarize, provide exactly one document_id or exact filename. If the "
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
            lines = ["Summarizable workspace documents:"]
            for item in listed:
                lines.append(
                    f"- {item['original_filename']} "
                    f"(document_id={item['document_id']}, "
                    f"characters={item['character_count']}, "
                    f"chunks={item['chunk_count']})"
                )
            if truncated:
                lines.append(
                    "The document list was truncated by the tool output limit."
                )
            content = "\n".join(lines)
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
                document_id=summary_input.document_id,
                filename=summary_input.filename,
            )
        except WorkspaceDocumentAmbiguousError as exc:
            return ToolResult(
                ok=False,
                content=(
                    f"Multiple documents match {exc.filename!r}; select one by "
                    f"document_id: {', '.join(exc.document_ids)}."
                ),
                error="document_selector_ambiguous",
                error_details={"candidate_document_ids": exc.document_ids},
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

        display_filename = self._display_filename(document)
        try:
            result = await self.summaries.summarize(
                content=content,
                filename=display_filename,
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
                original_filename=display_filename,
                chunk_index=chunk.chunk_index,
                text=chunk.text,
                score=None,
            )
            for chunk in result.chunks
        ]
        content, outputs = self._summary_outputs(
            document_id=document.id,
            filename=display_filename,
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
            "original_filename": self._display_filename(document),
            "character_count": document.character_count,
            "chunk_count": document.chunk_count,
        }

    def _display_filename(self, document: WorkspaceDocument) -> str:
        return document_display_filename(
            document.original_filename,
            document.stored_filename,
        )

    def _artifact_summary(self, summary: str) -> str:
        if looks_like_local_path(summary):
            return f"Summary text: {summary}"
        return summary

    def _summary_outputs(
        self,
        *,
        document_id: str,
        filename: str,
        result: DocumentSummaryResult,
    ) -> tuple[str, dict[str, object]]:
        outputs = self._build_summary_outputs(
            document_id=document_id,
            filename=filename,
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
                filename=filename,
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
            filename=filename,
            result=result,
            stop_reasons=stop_reasons,
            summary_char_limit=minimum_limit,
        )
        content = format_document_summary_content(
            filename=filename,
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
        filename: str,
        result: DocumentSummaryResult,
        stop_reasons: list[str],
        summary_char_limit: int | None,
    ) -> dict[str, object]:
        return {
            "action": "summarize",
            "document_id": document_id,
            "original_filename": filename,
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
