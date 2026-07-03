from time import perf_counter
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

from app.core.agent_loop import (
    DEFAULT_AGENT_STEPS,
    AgentLoop,
    AgentStep,
)
from app.models.conversation import (
    DEFAULT_CONVERSATION_TITLE,
    Conversation,
    ConversationMessage,
)
from app.models.workspace import utc_now
from app.services.chat_service import ChatService, chat_service
from app.services.exceptions import WorkspacePersistenceError
from app.services.rag_service import RAGSource
from app.services.workspace_service import (
    AUTO_TITLE_LENGTH,
    WorkspaceService,
    workspace_service,
)
from app.tools.registry import (
    ToolContext,
    ToolRegistry,
    create_default_tool_registry,
)

QUERY_MODE_AGENT_INSTRUCTION = (
    "This workspace is in query mode. For questions that may depend on workspace "
    "documents, use workspace_document_search before giving a final answer."
)


class WorkspaceAgentMetrics(BaseModel):
    llm_call_count: int
    step_count: int
    tool_call_count: int
    failed_step_count: int
    source_count: int
    max_steps_reached: bool
    total_latency_ms: int


class WorkspaceAgentResult(BaseModel):
    conversation_id: str
    message: str
    answer: str
    steps: list[AgentStep]
    sources: list[RAGSource]
    provider: str | None
    model: str | None
    metrics: WorkspaceAgentMetrics


class AgentService:
    def __init__(
        self,
        *,
        workspace: WorkspaceService | None = None,
        agent_loop: AgentLoop | None = None,
        chat: ChatService | None = None,
        tool_registry: ToolRegistry | None = None,
    ):
        self.workspace = workspace or workspace_service
        if agent_loop is None:
            registry = tool_registry or create_default_tool_registry()
            agent_loop = AgentLoop(
                llm=chat or chat_service,
                tool_registry=registry,
            )
        self.agent_loop = agent_loop

    async def run_in_conversation(
        self,
        session: Session,
        workspace_id: str,
        conversation_id: str,
        message: str,
        *,
        max_steps: int = DEFAULT_AGENT_STEPS,
    ) -> WorkspaceAgentResult:
        started_at = perf_counter()
        context = self.workspace.prepare_workspace_conversation_context(
            session,
            workspace_id,
            conversation_id,
            message,
        )
        agent_result = await self.agent_loop.run(
            message=context.message,
            context=ToolContext(
                workspace_id=context.workspace.id,
                conversation_id=context.conversation.id,
            ),
            system_prompt=self._agent_system_prompt(
                context.workspace.system_prompt,
                context.workspace.chat_mode,
            ),
            history=context.history,
            temperature=context.workspace.temperature,
            max_steps=max_steps,
        )
        sources = self._extract_sources(agent_result.steps)
        metrics = WorkspaceAgentMetrics(
            llm_call_count=agent_result.llm_call_count,
            step_count=len(agent_result.steps),
            tool_call_count=sum(1 for step in agent_result.steps if step.action),
            failed_step_count=sum(1 for step in agent_result.steps if not step.ok),
            source_count=len(sources),
            max_steps_reached=agent_result.max_steps_reached,
            total_latency_ms=self._elapsed_ms(started_at),
        )
        self._save_exchange(
            session,
            context.conversation,
            context.message,
            agent_result.answer,
            sources,
            agent_result.provider,
            agent_result.model,
            metrics,
            agent_result.steps,
        )
        return WorkspaceAgentResult(
            conversation_id=context.conversation.id,
            message=context.message,
            answer=agent_result.answer,
            steps=agent_result.steps,
            sources=sources,
            provider=agent_result.provider,
            model=agent_result.model,
            metrics=metrics,
        )

    def _agent_system_prompt(self, base_prompt: str, chat_mode: str) -> str:
        if chat_mode != "query":
            return base_prompt
        return f"{base_prompt.strip()}\n\n{QUERY_MODE_AGENT_INSTRUCTION}"

    def _extract_sources(self, steps: list[AgentStep]) -> list[RAGSource]:
        sources = []
        for step in steps:
            if step.tool_result is None:
                continue
            raw_sources = step.tool_result.data.get("sources")
            if not isinstance(raw_sources, list):
                continue
            sources.extend(self._coerce_sources(raw_sources))
        return sources

    def _coerce_sources(self, raw_sources: list[Any]) -> list[RAGSource]:
        sources = []
        for raw_source in raw_sources:
            if isinstance(raw_source, RAGSource):
                sources.append(raw_source)
                continue
            if not isinstance(raw_source, dict):
                continue
            try:
                sources.append(RAGSource.model_validate(raw_source))
            except ValidationError:
                continue
        return sources

    def _elapsed_ms(self, started_at: float) -> int:
        return max(0, round((perf_counter() - started_at) * 1000))

    def _save_exchange(
        self,
        session: Session,
        conversation: Conversation,
        user_content: str,
        assistant_content: str,
        sources: list[RAGSource],
        provider: str | None,
        model: str | None,
        metrics: WorkspaceAgentMetrics,
        steps: list[AgentStep],
    ) -> None:
        user_message = ConversationMessage(
            conversation_id=conversation.id,
            role="user",
            content=user_content,
        )
        assistant_message = ConversationMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=assistant_content,
            sources=[source.model_dump(mode="json") for source in sources],
            metrics=self._message_metrics(metrics, steps),
            provider=provider,
            model=model,
        )
        now = utc_now()
        conversation.updated_at = now
        if conversation.title == DEFAULT_CONVERSATION_TITLE:
            conversation.title = user_content[:AUTO_TITLE_LENGTH]

        session.add(user_message)
        session.add(assistant_message)
        session.add(conversation)
        try:
            session.commit()
        except SQLAlchemyError as exc:
            session.rollback()
            raise WorkspacePersistenceError(
                "failed to save agent conversation messages"
            ) from exc

    def _message_metrics(
        self,
        metrics: WorkspaceAgentMetrics,
        steps: list[AgentStep],
    ) -> dict[str, Any]:
        data = metrics.model_dump(mode="json")
        data["agent_mode"] = "react_text"
        data["agent_steps"] = [step.model_dump(mode="json") for step in steps]
        return data


agent_service = AgentService()
