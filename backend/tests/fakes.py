from pathlib import Path

from pydantic import BaseModel, Field

from app.services.chat_service import ChatResult
from app.services.document_service import (
    DeletedDocumentFiles,
    DocumentStoragePaths,
    ParsedDocumentFile,
    SavedDocumentFile,
)
from app.services.rag_service import IndexedDocument, RAGContextBuildResult, RAGSource
from app.tools.registry import ToolResult


class CollectingEventEmitter:
    def __init__(self):
        self.events = []

    async def emit(self, event_type, payload=None):
        self.events.append({"type": event_type, "payload": payload or {}})


class ConfirmationRequiredInput(BaseModel):
    value: str = Field(min_length=1)


class ConfirmationRequiredTool:
    name = "confirmation_required"
    description = "Require approval before running."
    input_model = ConfirmationRequiredInput
    risk_level = "high"
    side_effects = True
    requires_confirmation = True
    allowed_in_agent_modes = None

    def __init__(self):
        self.executed = False

    async def run(self, input_data, context):
        del context
        self.executed = True
        return ToolResult(ok=True, content=f"approved {input_data.value}")


class FakeRAGService:
    def __init__(
        self,
        chunks=None,
        *,
        indexed_chunk_count: int = 2,
        deleted_chunk_count: int = 2,
        context_chunk_limit: int | None = None,
    ):
        self.chunks = chunks or []
        self.indexed_chunk_count = indexed_chunk_count
        self.deleted_chunk_count = deleted_chunk_count
        self.context_chunk_limit = context_chunk_limit
        self.indexed_workspace_id = None
        self.retrieve_calls = []
        self.index_calls = []
        self.delete_calls = []
        self.deleted_documents = []
        self.delete_error = None

    async def retrieve(
        self,
        question,
        workspace_id,
        top_k,
        similarity_threshold,
    ):
        self.retrieve_calls.append(
            (question, workspace_id, top_k, similarity_threshold)
        )
        return self.chunks

    async def index_document(self, parsed_file, workspace_id):
        self.indexed_workspace_id = workspace_id
        self.index_calls.append((parsed_file, workspace_id))
        return IndexedDocument(
            document_id=parsed_file.id,
            chunk_count=self.indexed_chunk_count,
        )

    async def delete_document(self, document_id, workspace_id):
        if self.delete_error is not None:
            raise self.delete_error
        self.delete_calls.append((document_id, workspace_id))
        self.deleted_documents.append((document_id, workspace_id))
        return self.deleted_chunk_count

    def to_source(self, chunk):
        return RAGSource(
            document_id=chunk.document_id,
            display_filename=chunk.display_filename,
            chunk_index=chunk.chunk_index,
            text=chunk.text,
            score=chunk.score,
        )

    def build_system_prompt(self, chunks, base_prompt):
        return self.build_context_prompt(chunks, base_prompt).system_prompt

    def build_context_prompt(self, chunks, base_prompt, max_context_chars=None):
        used_chunks = (
            list(chunks)
            if self.context_chunk_limit is None
            else list(chunks[: self.context_chunk_limit])
        )
        sources = [self.to_source(chunk) for chunk in used_chunks]
        system_prompt = base_prompt
        if used_chunks:
            context = "\n".join(chunk.text for chunk in used_chunks)
            system_prompt = f"{base_prompt}\n\nContext: {context}"
        return RAGContextBuildResult(
            system_prompt=system_prompt,
            chunks=used_chunks,
            sources=sources,
            retrieved_count=len(chunks),
            dropped_count=len(chunks) - len(used_chunks),
            context_char_count=sum(len(chunk.text) for chunk in used_chunks),
        )


class FakeChatService:
    def __init__(self, *, echo: bool = False):
        self.echo = echo
        self.calls = []

    async def chat(
        self,
        message,
        system_prompt,
        history,
        temperature,
    ):
        self.calls.append(
            {
                "message": message,
                "system_prompt": system_prompt,
                "history": history,
                "temperature": temperature,
            }
        )
        answer = f"echo: {message}" if self.echo else f"answer-{len(self.calls)}"
        return ChatResult(
            message=message,
            answer=answer,
            provider="deepseek",
            model="deepseek-v4-flash",
        )


class FakeDocumentService:
    async def save_upload_file(self, file):
        return SavedDocumentFile(
            id="d" * 32,
            display_filename=file.filename,
            content_type=file.content_type,
            size_bytes=5,
            extension=".txt",
        )

    async def parse_saved_file(self, saved_file):
        return ParsedDocumentFile(
            id=saved_file.id,
            display_filename=saved_file.display_filename,
            extension=saved_file.extension,
            text="hello",
            character_count=5,
        )

    async def build_document_file_deletion_plan(
        self,
        document_id,
        extension,
    ):
        return DocumentStoragePaths(
            document_id=document_id,
            extension=extension,
            upload_file=Path(f"/tmp/uploads/{document_id}/source{extension}"),
            parsed_file=Path(f"/tmp/parsed/{document_id}/content.txt"),
        )

    async def delete_document_files(self, deletion_plan):
        return DeletedDocumentFiles(
            upload_file_deleted=True,
            parsed_file_deleted=True,
        )

    async def validate_document_file_paths(self, document_id, extension):
        return None
