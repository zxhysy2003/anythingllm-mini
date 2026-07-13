import asyncio

from app.core.rag import RetrievedChunk
from app.tools.document_tools import (
    NO_RELEVANT_CONTEXT,
    WorkspaceDocumentSearchInput,
    WorkspaceDocumentSearchTool,
)
from app.tools.registry import ToolContext
from tests.fakes import FakeRAGService


def make_chunk(
    workspace_id: str,
    *,
    text: str = "Workspace document context.",
    chunk_index: int = 0,
    score: float = 0.95,
) -> RetrievedChunk:
    return RetrievedChunk(
        id=f"{'a' * 32}:{chunk_index}",
        document_id="a" * 32,
        workspace_id=workspace_id,
        original_filename="guide.txt",
        stored_filename="guide.txt",
        extension=".txt",
        chunk_index=chunk_index,
        text=text,
        character_count=len(text),
        score=score,
    )


def run_search(tool, input_data, context):
    return asyncio.run(tool.run(input_data, context))


def test_document_search_requires_workspace_context():
    rag = FakeRAGService(chunks=[make_chunk("w" * 32)])
    tool = WorkspaceDocumentSearchTool(rag=rag)

    result = run_search(
        tool,
        WorkspaceDocumentSearchInput(question="What does the document say?"),
        ToolContext(),
    )

    assert result.ok is False
    assert result.error == "workspace_required"
    assert rag.retrieve_calls == []


def test_document_search_uses_context_workspace_and_query_options():
    workspace_id = "w" * 32
    chunk = make_chunk(workspace_id, text="The document explains agent tools.")
    rag = FakeRAGService(chunks=[chunk])
    tool = WorkspaceDocumentSearchTool(rag=rag)

    result = run_search(
        tool,
        WorkspaceDocumentSearchInput(
            question=" tools? ",
            top_k=3,
            similarity_threshold=0.8,
        ),
        ToolContext(workspace_id=workspace_id, conversation_id="c" * 32),
    )

    assert result.ok is True
    assert rag.retrieve_calls == [(" tools? ", workspace_id, 3, 0.8)]
    assert result.artifacts.model_dump(mode="json")["sources"] == [
        {
            "document_id": "a" * 32,
            "original_filename": "guide.txt",
            "chunk_index": 0,
            "text": "The document explains agent tools.",
            "score": 0.95,
        }
    ]
    assert "guide.txt chunk 0" in result.content
    assert "upload_path" not in str(result.artifacts)
    assert "parsed_path" not in str(result.artifacts)


def test_document_search_returns_empty_success_when_no_chunks_match():
    workspace_id = "w" * 32
    rag = FakeRAGService(chunks=[])
    tool = WorkspaceDocumentSearchTool(rag=rag)

    result = run_search(
        tool,
        WorkspaceDocumentSearchInput(question="No match"),
        ToolContext(workspace_id=workspace_id),
    )

    assert result.ok is True
    assert result.content == NO_RELEVANT_CONTEXT
    assert result.artifacts.sources == []
    assert result.artifacts.outputs == {}
    assert rag.retrieve_calls == [("No match", workspace_id, None, None)]
