from datetime import datetime
from time import perf_counter
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, select

from app.core.agent_executor import (
    DEFAULT_AGENT_STEPS,
    AgentExecutor,
    AgentStep,
)
from app.core.agent_modes import (
    AGENT_MODE_NATIVE_TOOL_CALLING,
    AGENT_MODE_REACT_TEXT,
)
from app.core.agent_loop import ReactTextAgentExecutor
from app.core.native_tool_calling import DeepSeekNativeToolCallingExecutor
from app.models.agent import (
    AGENT_INVOCATION_STATUS_COMPLETED,
    AGENT_INVOCATION_STATUS_MAX_STEPS_REACHED,
    AgentInvocation,
    AgentStepRecord,
)
from app.models.conversation import (
    DEFAULT_CONVERSATION_TITLE,
    Conversation,
    ConversationMessage,
)
from app.models.workspace import utc_now
from app.services.chat_service import (
    ChatService,
    chat_service,
    tool_calling_chat_service,
)
from app.services.exceptions import (
    AgentInvocationNotFoundError,
    WorkspacePersistenceError,
)
from app.services.rag_service import RAGSource
from app.services.workspace_service import (
    AUTO_TITLE_LENGTH,
    WorkspaceService,
    workspace_service,
)
from app.tools.registry import (
    ToolContext,
    ToolRegistry,
    ToolResult,
    create_default_tool_registry,
)

QUERY_MODE_AGENT_INSTRUCTION = (
    "This workspace is in query mode. For questions that may depend on workspace "
    "documents, use workspace_document_search before giving a final answer."
)


class WorkspaceAgentMetrics(BaseModel):
    max_steps: int
    llm_call_count: int
    step_count: int
    tool_call_count: int
    failed_step_count: int
    source_count: int
    max_steps_reached: bool
    total_latency_ms: int


class WorkspaceAgentResult(BaseModel):
    conversation_id: str
    agent_invocation_id: str
    message: str
    answer: str
    steps: list[AgentStep]
    sources: list[RAGSource]
    provider: str | None
    model: str | None
    metrics: WorkspaceAgentMetrics


class WorkspaceAgentInvocationResult(BaseModel):
    id: str
    workspace_id: str
    conversation_id: str
    user_message_id: str
    assistant_message_id: str
    input_message: str
    agent_mode: str
    status: str
    provider: str | None
    model: str | None
    max_steps: int
    llm_call_count: int
    step_count: int
    tool_call_count: int
    failed_step_count: int
    source_count: int
    max_steps_reached: bool
    total_latency_ms: int
    started_at: datetime
    ended_at: datetime
    created_at: datetime
    steps: list[AgentStep]


class AgentService:
    def __init__(
        self,
        *,
        workspace: WorkspaceService | None = None,
        agent_executor: AgentExecutor | None = None,
        native_agent_executor: AgentExecutor | None = None,
        chat: ChatService | None = None,
        tool_registry: ToolRegistry | None = None,
    ):
        self.workspace = workspace or workspace_service
        registry = tool_registry or create_default_tool_registry()
        if agent_executor is None:
            agent_executor = ReactTextAgentExecutor(
                llm=chat or chat_service,
                tool_registry=registry,
            )
        if native_agent_executor is None:
            native_agent_executor = DeepSeekNativeToolCallingExecutor(
                llm=tool_calling_chat_service,
                tool_registry=registry,
            )
        self.agent_executor = agent_executor
        self.agent_executors = {
            AGENT_MODE_REACT_TEXT: agent_executor,
            agent_executor.agent_mode: agent_executor,
            AGENT_MODE_NATIVE_TOOL_CALLING: native_agent_executor,
            native_agent_executor.agent_mode: native_agent_executor,
        }

    async def run_in_conversation(
        self,
        session: Session,
        workspace_id: str,
        conversation_id: str,
        message: str,
        *,
        max_steps: int = DEFAULT_AGENT_STEPS,
        agent_mode: str = AGENT_MODE_REACT_TEXT,
    ) -> WorkspaceAgentResult:
        started_at = perf_counter()
        invocation_started_at = utc_now()
        agent_executor = self._agent_executor(agent_mode)
        context = self.workspace.prepare_workspace_conversation_context(
            session,
            workspace_id,
            conversation_id,
            message,
        )
        agent_result = await agent_executor.run(
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
        invocation_ended_at = utc_now()
        metrics = WorkspaceAgentMetrics(
            max_steps=max_steps,
            llm_call_count=agent_result.llm_call_count,
            step_count=len(agent_result.steps),
            tool_call_count=sum(1 for step in agent_result.steps if step.action),
            failed_step_count=sum(1 for step in agent_result.steps if not step.ok),
            source_count=len(sources),
            max_steps_reached=agent_result.max_steps_reached,
            total_latency_ms=self._elapsed_ms(started_at),
        )
        agent_invocation_id = self._save_exchange(
            session,
            context.workspace.id,
            context.conversation,
            context.message,
            agent_result.answer,
            sources,
            agent_result.provider,
            agent_result.model,
            metrics,
            agent_result.steps,
            agent_result.agent_mode,
            invocation_started_at,
            invocation_ended_at,
        )
        return WorkspaceAgentResult(
            conversation_id=context.conversation.id,
            agent_invocation_id=agent_invocation_id,
            message=context.message,
            answer=agent_result.answer,
            steps=agent_result.steps,
            sources=sources,
            provider=agent_result.provider,
            model=agent_result.model,
            metrics=metrics,
        )

    def get_invocation(
        self,
        session: Session,
        workspace_id: str,
        conversation_id: str,
        invocation_id: str,
    ) -> WorkspaceAgentInvocationResult:
        self.workspace.get_workspace(session, workspace_id)
        statement = select(AgentInvocation).where(
            AgentInvocation.id == invocation_id,
            AgentInvocation.workspace_id == workspace_id,
            AgentInvocation.conversation_id == conversation_id,
        )
        invocation = session.exec(statement).first()
        if invocation is None:
            raise AgentInvocationNotFoundError(
                f"agent invocation not found in conversation: {invocation_id}"
            )

        steps_statement = (
            select(AgentStepRecord)
            .where(AgentStepRecord.invocation_id == invocation.id)
            .order_by(AgentStepRecord.step_index.asc())
        )
        steps = [
            self._step_from_record(record)
            for record in session.exec(steps_statement).all()
        ]
        return WorkspaceAgentInvocationResult(
            **invocation.model_dump(),
            steps=steps,
        )

    def _agent_executor(self, agent_mode: str) -> AgentExecutor:
        try:
            return self.agent_executors[agent_mode]
        except KeyError:
            raise ValueError(f"unsupported agent_mode: {agent_mode}") from None

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
        workspace_id: str,
        conversation: Conversation,
        user_content: str,
        assistant_content: str,
        sources: list[RAGSource],
        provider: str | None,
        model: str | None,
        metrics: WorkspaceAgentMetrics,
        steps: list[AgentStep],
        agent_mode: str,
        invocation_started_at: datetime,
        invocation_ended_at: datetime,
    ) -> str:
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
            provider=provider,
            model=model,
        )
        invocation = AgentInvocation(
            workspace_id=workspace_id,
            conversation_id=conversation.id,
            user_message_id=user_message.id,
            assistant_message_id=assistant_message.id,
            input_message=user_content,
            agent_mode=agent_mode,
            status=self._invocation_status(metrics),
            provider=provider,
            model=model,
            started_at=invocation_started_at,
            ended_at=invocation_ended_at,
            **metrics.model_dump(),
        )
        assistant_message.metrics = self._message_metrics(
            metrics,
            invocation.id,
            agent_mode,
        )
        now = utc_now()
        conversation.updated_at = now
        if conversation.title == DEFAULT_CONVERSATION_TITLE:
            conversation.title = user_content[:AUTO_TITLE_LENGTH]

        session.add(user_message)
        session.add(assistant_message)
        session.add(invocation)
        for step in steps:
            session.add(self._step_record(invocation.id, step))
        session.add(conversation)
        try:
            session.commit()
        except SQLAlchemyError as exc:
            session.rollback()
            raise WorkspacePersistenceError(
                "failed to save agent conversation messages"
            ) from exc
        return invocation.id

    def _message_metrics(
        self,
        metrics: WorkspaceAgentMetrics,
        agent_invocation_id: str,
        agent_mode: str,
    ) -> dict[str, Any]:
        return {
            **metrics.model_dump(mode="json"),
            "agent_mode": agent_mode,
            "agent_invocation_id": agent_invocation_id,
        }

    def _invocation_status(self, metrics: WorkspaceAgentMetrics) -> str:
        if metrics.max_steps_reached:
            return AGENT_INVOCATION_STATUS_MAX_STEPS_REACHED
        return AGENT_INVOCATION_STATUS_COMPLETED

    def _step_record(
        self,
        invocation_id: str,
        step: AgentStep,
    ) -> AgentStepRecord:
        tool_result = None
        if step.tool_result is not None:
            tool_result = step.tool_result.model_dump(mode="json")
        return AgentStepRecord(
            invocation_id=invocation_id,
            step_index=step.step_index,
            llm_output=step.llm_output,
            action=step.action,
            action_input=step.action_input,
            observation=step.observation,
            ok=step.ok,
            error=step.error,
            tool_result=tool_result,
        )

    def _step_from_record(self, record: AgentStepRecord) -> AgentStep:
        tool_result = None
        if record.tool_result is not None:
            tool_result = ToolResult.model_validate(record.tool_result)
        return AgentStep(
            step_index=record.step_index,
            llm_output=record.llm_output,
            action=record.action,
            action_input=record.action_input,
            observation=record.observation,
            ok=record.ok,
            error=record.error,
            tool_result=tool_result,
        )


agent_service = AgentService()
