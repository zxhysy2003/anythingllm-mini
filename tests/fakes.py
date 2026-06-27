from pathlib import Path

from app.services.chat_service import ChatResult
from app.services.document_service import (
    DeletedDocumentFiles,
    DocumentFileDeletionPlan,
    ParsedDocumentFile,
    SavedDocumentFile,
)
from app.services.rag_service import IndexedDocument, RAGSource


class FakeRAGService:
    def __init__(
        self,
        chunks=None,
        *,
        indexed_chunk_count: int = 2,
        deleted_chunk_count: int = 2,
    ):
        self.chunks = chunks or []
        self.indexed_chunk_count = indexed_chunk_count
        self.deleted_chunk_count = deleted_chunk_count
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
            original_filename=chunk.original_filename,
            chunk_index=chunk.chunk_index,
            text=chunk.text,
            score=chunk.score,
        )

    def build_system_prompt(self, chunks, base_prompt):
        if not chunks:
            return base_prompt
        return f"{base_prompt}\n\nContext: {chunks[0].text}"


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
            original_filename=file.filename,
            stored_filename="guide.txt",
            content_type=file.content_type,
            size_bytes=5,
            upload_path="/tmp/uploads/guide.txt",
            extension=".txt",
        )

    async def parse_saved_file(self, saved_file):
        return ParsedDocumentFile(
            id=saved_file.id,
            original_filename=saved_file.original_filename,
            stored_filename=saved_file.stored_filename,
            extension=saved_file.extension,
            text="hello",
            character_count=5,
            parsed_path="/tmp/parsed/guide.txt",
        )

    async def build_document_file_deletion_plan(
        self,
        document_id,
        upload_path,
        parsed_path,
    ):
        return DocumentFileDeletionPlan(
            document_id=document_id,
            upload_file=Path(upload_path),
            parsed_file=Path(parsed_path),
            upload_path=upload_path,
            parsed_path=parsed_path,
        )

    async def delete_document_files(self, deletion_plan):
        return DeletedDocumentFiles(
            upload_path=deletion_plan.upload_path,
            parsed_path=deletion_plan.parsed_path,
            upload_file_deleted=True,
            parsed_file_deleted=True,
        )

    async def validate_document_file_paths(self, document_id, upload_path, parsed_path):
        return None
