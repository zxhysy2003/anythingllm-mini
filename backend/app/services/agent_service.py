import asyncio
from datetime import datetime, timedelta
from time import perf_counter
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlalchemy import case, or_, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from app.core.agent_events import AgentEventEmitter, emit_agent_event
from app.core.agent_executor import DEFAULT_AGENT_STEPS, AgentExecutor, AgentStep
from app.core.agent_modes import AGENT_MODE_NATIVE_TOOL_CALLING, AGENT_MODE_REACT_TEXT
from app.core.agent_loop import ReactTextAgentExecutor
from app.core.native_tool_calling import DeepSeekNativeToolCallingExecutor
from app.models.agent import (
    AGENT_INVOCATION_STATUS_COMPLETED,
    AGENT_INVOCATION_STATUS_MAX_STEPS_REACHED,
    AGENT_INVOCATION_STATUS_NEEDS_INPUT,
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
    AgentInvocationConflictError,
    AgentInvocationNotFoundError,
    ConversationNotFoundError,
    WorkspacePersistenceError,
)
from app.services.rag_service import RAGSource
from app.services.workspace_service import (
    AUTO_TITLE_LENGTH,
    WorkspaceService,
    workspace_service,
)
from app.tools.interactions import (
    ClarificationResolution,
    PendingClarification,
)
from app.tools.registry import (
    ToolContext,
    ToolRegistry,
    ToolResult,
    create_default_tool_registry,
)

CLARIFICATION_TIMEOUT = timedelta(minutes=10)
AGENT_EXECUTION_CLAIM_LEASE = timedelta(minutes=15)
AGENT_EXECUTION_CLAIM_HEARTBEAT_INTERVAL = timedelta(minutes=5)
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
    status: str
    answer: str | None
    pending_input: PendingClarification | None = None
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
    assistant_message_id: str | None
    answer: str | None
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
    ended_at: datetime | None
    created_at: datetime
    pending_input: PendingClarification | None = None
    steps: list[AgentStep]


class AgentResumeState(BaseModel):
    """The minimum local state required to continue one paused invocation."""

    model_config = ConfigDict(extra="forbid")

    system_prompt: str
    history: list[dict[str, str]]
    temperature: float | None
    executor_state: dict[str, Any] | None = None


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
        approved_tool_call_ids: list[str] | set[str] | None = None,
        event_emitter: AgentEventEmitter | None = None,
    ) -> WorkspaceAgentResult:
        execution_claim_id: str | None = None
        heartbeat_task: asyncio.Task[None] | None = None
        try:
            agent_executor = self._agent_executor(agent_mode)
            context = self.workspace.prepare_workspace_conversation_context(
                session,
                workspace_id,
                conversation_id,
                message,
            )
            system_prompt = self._agent_system_prompt(
                context.workspace.system_prompt,
                context.workspace.chat_mode,
            )
            self._require_no_pending_invocation(
                session,
                context.workspace.id,
                context.conversation.id,
            )
            claim_id = uuid4().hex
            self._claim_agent_execution(
                session,
                context.conversation,
                claim_id,
            )
            execution_claim_id = claim_id
            heartbeat_task = self._start_execution_claim_heartbeat(
                session,
                context.conversation.id,
                execution_claim_id,
            )
            started_at = perf_counter()
            invocation_started_at = utc_now()
            await emit_agent_event(
                event_emitter,
                "agent_started",
                {
                    "workspace_id": context.workspace.id,
                    "conversation_id": context.conversation.id,
                    "agent_mode": agent_mode,
                    "max_steps": max_steps,
                },
            )
            agent_result = await agent_executor.run(
                message=context.message,
                context=ToolContext(
                    workspace_id=context.workspace.id,
                    conversation_id=context.conversation.id,
                    agent_mode=agent_executor.agent_mode,
                    approved_tool_call_ids=set(approved_tool_call_ids or []),
                ),
                system_prompt=system_prompt,
                history=context.history,
                temperature=context.workspace.temperature,
                max_steps=max_steps,
                event_emitter=event_emitter,
            )
            metrics = self._metrics(
                max_steps=max_steps,
                agent_result=agent_result,
                total_latency_ms=self._elapsed_ms(started_at),
            )
            sources = self._extract_sources(agent_result.steps)
            if agent_result.pending_interaction is not None:
                await self._stop_execution_claim_heartbeat(heartbeat_task)
                heartbeat_task = None
                pending_input = self._pending_input(agent_result)
                invocation_id = self._save_pending_invocation(
                    session=session,
                    workspace_id=context.workspace.id,
                    conversation=context.conversation,
                    user_content=context.message,
                    agent_result=agent_result,
                    metrics=metrics,
                    pending_input=pending_input,
                    resume_state=AgentResumeState(
                        system_prompt=system_prompt,
                        history=[dict(item) for item in context.history],
                        temperature=context.workspace.temperature,
                        executor_state=agent_result.resume_state,
                    ),
                    started_at=invocation_started_at,
                    execution_claim_id=execution_claim_id,
                )
                execution_claim_id = None
                result = WorkspaceAgentResult(
                    conversation_id=context.conversation.id,
                    agent_invocation_id=invocation_id,
                    message=context.message,
                    status=AGENT_INVOCATION_STATUS_NEEDS_INPUT,
                    answer=None,
                    pending_input=pending_input,
                    steps=agent_result.steps,
                    sources=sources,
                    provider=agent_result.provider,
                    model=agent_result.model,
                    metrics=metrics,
                )
                await emit_agent_event(
                    event_emitter,
                    "agent_needs_input",
                    result.model_dump(mode="json"),
                )
                return result

            await self._stop_execution_claim_heartbeat(heartbeat_task)
            heartbeat_task = None
            invocation_id = self._save_completed_invocation(
                session=session,
                workspace_id=context.workspace.id,
                conversation=context.conversation,
                user_content=context.message,
                agent_result=agent_result,
                metrics=metrics,
                started_at=invocation_started_at,
                ended_at=utc_now(),
                execution_claim_id=execution_claim_id,
            )
            execution_claim_id = None
            result = self._result_from_agent_run(
                conversation_id=context.conversation.id,
                invocation_id=invocation_id,
                agent_result=agent_result,
                metrics=metrics,
                sources=sources,
            )
            await emit_agent_event(
                event_emitter,
                "agent_finished",
                result.model_dump(mode="json"),
            )
            return result
        except (asyncio.CancelledError, Exception) as exc:
            error = exc
            if heartbeat_task is not None:
                try:
                    await self._stop_execution_claim_heartbeat(heartbeat_task)
                except (
                    AgentInvocationConflictError,
                    WorkspacePersistenceError,
                ) as heartbeat_exc:
                    error = heartbeat_exc
            if execution_claim_id is not None:
                try:
                    self._release_agent_execution(
                        session,
                        workspace_id,
                        conversation_id,
                        execution_claim_id,
                    )
                except WorkspacePersistenceError as release_exc:
                    error = release_exc
            await emit_agent_event(
                event_emitter,
                "agent_failed",
                {"error": str(error), "error_type": type(error).__name__},
            )
            if error is exc:
                raise
            raise error from exc

    async def continue_in_conversation(
        self,
        session: Session,
        workspace_id: str,
        conversation_id: str,
        invocation_id: str,
        *,
        answer: str | None = None,
        skip: bool = False,
        event_emitter: AgentEventEmitter | None = None,
    ) -> WorkspaceAgentResult:
        execution_claim_id: str | None = None
        heartbeat_task: asyncio.Task[None] | None = None
        try:
            invocation = self._get_invocation_record(
                session,
                workspace_id,
                conversation_id,
                invocation_id,
            )
            if invocation.status != AGENT_INVOCATION_STATUS_NEEDS_INPUT:
                raise AgentInvocationConflictError(
                    "agent invocation is not waiting for user input"
                )
            pending_input = self._load_pending_input(invocation)
            resolution = self._resolve_clarification(
                pending_input,
                answer=answer,
                skip=skip,
            )
            resume_state = self._load_resume_state(invocation)
            conversation = session.get(Conversation, conversation_id)
            if conversation is None or conversation.workspace_id != workspace_id:
                raise ConversationNotFoundError(
                    f"conversation not found in workspace: {conversation_id}"
                )
            claim_id = uuid4().hex
            self._claim_agent_execution(
                session,
                conversation,
                claim_id,
                pending_invocation_id=invocation.id,
            )
            execution_claim_id = claim_id
            heartbeat_task = self._start_execution_claim_heartbeat(
                session,
                conversation.id,
                execution_claim_id,
            )
            agent_executor = self._agent_executor(invocation.agent_mode)
            steps = self._steps_for_invocation(session, invocation.id)
            resolved_steps, continuation_observation = self._resolve_pending_step(
                steps,
                resolution,
            )
            await emit_agent_event(
                event_emitter,
                "agent_started",
                {
                    "workspace_id": workspace_id,
                    "conversation_id": conversation_id,
                    "agent_mode": invocation.agent_mode,
                    "max_steps": invocation.max_steps,
                    "agent_invocation_id": invocation.id,
                    "resumed": True,
                },
            )
            started_at = perf_counter()
            agent_result = await agent_executor.run(
                message=invocation.input_message,
                context=ToolContext(
                    workspace_id=workspace_id,
                    conversation_id=conversation_id,
                    agent_mode=agent_executor.agent_mode,
                ),
                system_prompt=resume_state.system_prompt,
                history=resume_state.history,
                temperature=resume_state.temperature,
                max_steps=invocation.max_steps,
                event_emitter=event_emitter,
                initial_steps=resolved_steps,
                initial_llm_call_count=invocation.llm_call_count,
                continuation_observation=continuation_observation,
                resume_state=resume_state.executor_state,
            )
            if agent_result.pending_interaction is not None:
                raise WorkspacePersistenceError(
                    "a paused invocation cannot request a second clarification"
                )
            metrics = self._metrics(
                max_steps=invocation.max_steps,
                agent_result=agent_result,
                total_latency_ms=invocation.total_latency_ms
                + self._elapsed_ms(started_at),
            )
            sources = self._extract_sources(agent_result.steps)
            await self._stop_execution_claim_heartbeat(heartbeat_task)
            heartbeat_task = None
            self._finalize_invocation(
                session=session,
                invocation=invocation,
                conversation=conversation,
                agent_result=agent_result,
                metrics=metrics,
                sources=sources,
                ended_at=utc_now(),
                execution_claim_id=execution_claim_id,
            )
            execution_claim_id = None
            result = self._result_from_agent_run(
                conversation_id=conversation.id,
                invocation_id=invocation.id,
                agent_result=agent_result,
                metrics=metrics,
                sources=sources,
            )
            await emit_agent_event(
                event_emitter,
                "agent_finished",
                result.model_dump(mode="json"),
            )
            return result
        except (asyncio.CancelledError, Exception) as exc:
            error = exc
            if heartbeat_task is not None:
                try:
                    await self._stop_execution_claim_heartbeat(heartbeat_task)
                except (
                    AgentInvocationConflictError,
                    WorkspacePersistenceError,
                ) as heartbeat_exc:
                    error = heartbeat_exc
            if execution_claim_id is not None:
                try:
                    self._release_agent_execution(
                        session,
                        workspace_id,
                        conversation_id,
                        execution_claim_id,
                    )
                except WorkspacePersistenceError as release_exc:
                    error = release_exc
            await emit_agent_event(
                event_emitter,
                "agent_failed",
                {"error": str(error), "error_type": type(error).__name__},
            )
            if error is exc:
                raise
            raise error from exc

    def get_invocation(
        self,
        session: Session,
        workspace_id: str,
        conversation_id: str,
        invocation_id: str,
    ) -> WorkspaceAgentInvocationResult:
        invocation = self._get_invocation_record(
            session,
            workspace_id,
            conversation_id,
            invocation_id,
        )
        pending_input = (
            self._load_pending_input(invocation)
            if invocation.pending_input is not None
            else None
        )
        assistant_message = (
            None
            if invocation.assistant_message_id is None
            else session.get(ConversationMessage, invocation.assistant_message_id)
        )
        payload = invocation.model_dump()
        payload["answer"] = (
            None if assistant_message is None else assistant_message.content
        )
        payload["pending_input"] = pending_input
        payload["steps"] = self._steps_for_invocation(session, invocation.id)
        return WorkspaceAgentInvocationResult(**payload)

    def _agent_executor(self, agent_mode: str) -> AgentExecutor:
        try:
            return self.agent_executors[agent_mode]
        except KeyError:
            raise ValueError(f"unsupported agent_mode: {agent_mode}") from None

    def _agent_system_prompt(self, base_prompt: str, chat_mode: str) -> str:
        prompt = base_prompt.strip()
        if chat_mode == "query":
            prompt = f"{prompt}\n\n{QUERY_MODE_AGENT_INSTRUCTION}"
        return prompt

    def _require_no_pending_invocation(
        self,
        session: Session,
        workspace_id: str,
        conversation_id: str,
    ) -> None:
        statement = select(AgentInvocation.id).where(
            AgentInvocation.workspace_id == workspace_id,
            AgentInvocation.conversation_id == conversation_id,
            AgentInvocation.status == AGENT_INVOCATION_STATUS_NEEDS_INPUT,
        )
        if session.exec(statement).first() is not None:
            raise AgentInvocationConflictError(
                "conversation already has an Agent invocation waiting for user input"
            )

    def _claim_agent_execution(
        self,
        session: Session,
        conversation: Conversation,
        execution_claim_id: str,
        *,
        pending_invocation_id: str | None = None,
    ) -> None:
        claim_started_at = utc_now()
        pending_statement = select(AgentInvocation.id).where(
            AgentInvocation.conversation_id == conversation.id,
            AgentInvocation.status == AGENT_INVOCATION_STATUS_NEEDS_INPUT,
        )
        pending_condition = (
            pending_statement.where(
                AgentInvocation.id == pending_invocation_id
            ).exists()
            if pending_invocation_id is not None
            else ~pending_statement.exists()
        )
        statement = (
            update(Conversation)
            .where(
                Conversation.id == conversation.id,
                Conversation.workspace_id == conversation.workspace_id,
                pending_condition,
                or_(
                    Conversation.agent_execution_claim_id.is_(None),
                    Conversation.agent_execution_claimed_at.is_(None),
                    Conversation.agent_execution_claimed_at
                    < claim_started_at - AGENT_EXECUTION_CLAIM_LEASE,
                ),
            )
            .values(
                agent_execution_claim_id=execution_claim_id,
                agent_execution_claimed_at=claim_started_at,
            )
            .execution_options(synchronize_session=False)
        )
        try:
            result = session.execute(statement)
            if result.rowcount != 1:
                session.rollback()
                raise AgentInvocationConflictError(
                    "conversation already has an active Agent execution"
                )
            session.commit()
            session.refresh(conversation)
        except AgentInvocationConflictError:
            raise
        except SQLAlchemyError as exc:
            session.rollback()
            raise WorkspacePersistenceError(
                "failed to claim conversation Agent execution"
            ) from exc

    def _renew_agent_execution_claim(
        self,
        session: Session,
        conversation_id: str,
        execution_claim_id: str,
    ) -> None:
        statement = (
            update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.agent_execution_claim_id == execution_claim_id,
            )
            .values(
                agent_execution_claimed_at=utc_now(),
            )
            .execution_options(synchronize_session=False)
        )
        try:
            result = session.execute(statement)
            if result.rowcount != 1:
                session.rollback()
                raise AgentInvocationConflictError("agent execution claim was lost")
            session.commit()
        except AgentInvocationConflictError:
            raise
        except SQLAlchemyError as exc:
            session.rollback()
            raise WorkspacePersistenceError(
                "failed to renew conversation Agent execution claim"
            ) from exc

    def _release_agent_execution(
        self,
        session: Session,
        workspace_id: str,
        conversation_id: str,
        execution_claim_id: str,
    ) -> None:
        statement = (
            update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.workspace_id == workspace_id,
                Conversation.agent_execution_claim_id == execution_claim_id,
            )
            .values(
                agent_execution_claim_id=None,
                agent_execution_claimed_at=None,
            )
            .execution_options(synchronize_session=False)
        )
        try:
            session.execute(statement)
            session.commit()
        except SQLAlchemyError as exc:
            session.rollback()
            raise WorkspacePersistenceError(
                "failed to release conversation Agent execution claim"
            ) from exc

    def _release_agent_execution_in_transaction(
        self,
        session: Session,
        conversation: Conversation,
        execution_claim_id: str,
        *,
        user_content: str | None = None,
    ) -> None:
        values: dict[str, Any] = {
            "agent_execution_claim_id": None,
            "agent_execution_claimed_at": None,
            "updated_at": utc_now(),
        }
        if user_content is not None:
            values["title"] = case(
                (
                    Conversation.title == DEFAULT_CONVERSATION_TITLE,
                    user_content[:AUTO_TITLE_LENGTH],
                ),
                else_=Conversation.title,
            )
        statement = (
            update(Conversation)
            .where(
                Conversation.id == conversation.id,
                Conversation.workspace_id == conversation.workspace_id,
                Conversation.agent_execution_claim_id == execution_claim_id,
            )
            .values(**values)
            .execution_options(synchronize_session=False)
        )
        try:
            result = session.execute(statement)
            if result.rowcount != 1:
                session.rollback()
                raise AgentInvocationConflictError("agent execution claim was lost")
        except AgentInvocationConflictError:
            raise
        except SQLAlchemyError as exc:
            session.rollback()
            raise WorkspacePersistenceError(
                "failed to release conversation Agent execution claim"
            ) from exc

    def _start_execution_claim_heartbeat(
        self,
        session: Session,
        conversation_id: str,
        execution_claim_id: str,
    ) -> asyncio.Task[None]:
        return asyncio.create_task(
            self._maintain_agent_execution_claim(
                session,
                conversation_id,
                execution_claim_id,
            )
        )

    async def _maintain_agent_execution_claim(
        self,
        session: Session,
        conversation_id: str,
        execution_claim_id: str,
    ) -> None:
        while True:
            await asyncio.sleep(
                AGENT_EXECUTION_CLAIM_HEARTBEAT_INTERVAL.total_seconds()
            )
            self._renew_agent_execution_claim(
                session,
                conversation_id,
                execution_claim_id,
            )

    async def _stop_execution_claim_heartbeat(
        self,
        heartbeat_task: asyncio.Task[None],
    ) -> None:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass

    def _get_invocation_record(
        self,
        session: Session,
        workspace_id: str,
        conversation_id: str,
        invocation_id: str,
    ) -> AgentInvocation:
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
        return invocation

    def _pending_input(self, agent_result) -> PendingClarification:
        interaction = agent_result.pending_interaction
        if interaction is None or interaction.resolution is not None:
            raise WorkspacePersistenceError("agent run does not contain pending input")
        return PendingClarification(
            **interaction.request.model_dump(),
            expires_at=utc_now() + CLARIFICATION_TIMEOUT,
        )

    def _metrics(
        self,
        *,
        max_steps: int,
        agent_result,
        total_latency_ms: int,
    ) -> WorkspaceAgentMetrics:
        sources = self._extract_sources(agent_result.steps)
        return WorkspaceAgentMetrics(
            max_steps=max_steps,
            llm_call_count=agent_result.llm_call_count,
            step_count=len(agent_result.steps),
            tool_call_count=sum(1 for step in agent_result.steps if step.action),
            failed_step_count=sum(1 for step in agent_result.steps if not step.ok),
            source_count=len(sources),
            max_steps_reached=agent_result.max_steps_reached,
            total_latency_ms=total_latency_ms,
        )

    def _extract_sources(self, steps: list[AgentStep]) -> list[RAGSource]:
        sources = []
        for step in steps:
            if step.tool_result is None:
                continue
            sources.extend(
                RAGSource.model_validate(source.model_dump(mode="json"))
                for source in step.tool_result.artifacts.sources
            )
        return sources

    def _elapsed_ms(self, started_at: float) -> int:
        return max(0, round((perf_counter() - started_at) * 1000))

    def _save_pending_invocation(
        self,
        *,
        session: Session,
        workspace_id: str,
        conversation: Conversation,
        user_content: str,
        agent_result,
        metrics: WorkspaceAgentMetrics,
        pending_input: PendingClarification,
        resume_state: AgentResumeState,
        started_at: datetime,
        execution_claim_id: str,
    ) -> str:
        user_message = ConversationMessage(
            conversation_id=conversation.id,
            role="user",
            content=user_content,
        )
        invocation = AgentInvocation(
            workspace_id=workspace_id,
            conversation_id=conversation.id,
            user_message_id=user_message.id,
            input_message=user_content,
            agent_mode=agent_result.agent_mode,
            status=AGENT_INVOCATION_STATUS_NEEDS_INPUT,
            provider=agent_result.provider,
            model=agent_result.model,
            started_at=started_at,
            pending_input=pending_input.model_dump(mode="json"),
            resume_state=resume_state.model_dump(mode="json"),
            **metrics.model_dump(),
        )
        user_message.metrics = self._message_metrics(
            metrics,
            invocation.id,
            agent_result.agent_mode,
        )
        try:
            session.add(user_message)
            session.flush()
        except SQLAlchemyError as exc:
            session.rollback()
            raise WorkspacePersistenceError(
                "failed to save pending agent user message"
            ) from exc
        # Fence the claim before pending invocation is eligible for autoflush. If a
        # newer execution has already paused this conversation, the partial unique
        # index must not turn the stale execution into a persistence error.
        self._release_agent_execution_in_transaction(
            session,
            conversation,
            execution_claim_id,
            user_content=user_content,
        )
        session.add(invocation)
        for step in agent_result.steps:
            session.add(self._step_record(invocation.id, step))
        self._commit_pending_agent_changes(session)
        return invocation.id

    def _save_completed_invocation(
        self,
        *,
        session: Session,
        workspace_id: str,
        conversation: Conversation,
        user_content: str,
        agent_result,
        metrics: WorkspaceAgentMetrics,
        started_at: datetime,
        ended_at: datetime,
        execution_claim_id: str,
    ) -> str:
        user_message = ConversationMessage(
            conversation_id=conversation.id,
            role="user",
            content=user_content,
        )
        sources = self._extract_sources(agent_result.steps)
        assistant_message = ConversationMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=agent_result.answer or "",
            sources=[source.model_dump(mode="json") for source in sources],
            provider=agent_result.provider,
            model=agent_result.model,
        )
        invocation = AgentInvocation(
            workspace_id=workspace_id,
            conversation_id=conversation.id,
            user_message_id=user_message.id,
            assistant_message_id=assistant_message.id,
            input_message=user_content,
            agent_mode=agent_result.agent_mode,
            status=self._invocation_status(metrics),
            provider=agent_result.provider,
            model=agent_result.model,
            started_at=started_at,
            ended_at=ended_at,
            **metrics.model_dump(),
        )
        assistant_message.metrics = self._message_metrics(
            metrics,
            invocation.id,
            agent_result.agent_mode,
        )
        try:
            session.add(user_message)
            session.add(assistant_message)
            session.flush()
        except SQLAlchemyError as exc:
            session.rollback()
            raise WorkspacePersistenceError(
                "failed to save agent conversation messages"
            ) from exc
        session.add(invocation)
        for step in agent_result.steps:
            session.add(self._step_record(invocation.id, step))
        self._release_agent_execution_in_transaction(
            session,
            conversation,
            execution_claim_id,
            user_content=user_content,
        )
        self._commit_agent_changes(session)
        return invocation.id

    def _finalize_invocation(
        self,
        *,
        session: Session,
        invocation: AgentInvocation,
        conversation: Conversation,
        agent_result,
        metrics: WorkspaceAgentMetrics,
        sources: list[RAGSource],
        ended_at: datetime,
        execution_claim_id: str,
    ) -> None:
        assistant_message = ConversationMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=agent_result.answer or "",
            sources=[source.model_dump(mode="json") for source in sources],
            provider=agent_result.provider or invocation.provider,
            model=agent_result.model or invocation.model,
        )
        assistant_message.metrics = self._message_metrics(
            metrics,
            invocation.id,
            invocation.agent_mode,
        )
        statement = (
            update(AgentInvocation)
            .where(
                AgentInvocation.id == invocation.id,
                AgentInvocation.status == AGENT_INVOCATION_STATUS_NEEDS_INPUT,
            )
            .values(
                assistant_message_id=assistant_message.id,
                status=self._invocation_status(metrics),
                provider=agent_result.provider or invocation.provider,
                model=agent_result.model or invocation.model,
                ended_at=ended_at,
                pending_input=None,
                resume_state=None,
                **metrics.model_dump(),
            )
            .execution_options(synchronize_session=False)
        )
        try:
            # The invocation references this message, so flush its INSERT before
            # the fenced UPDATE for databases that enforce foreign keys.
            session.add(assistant_message)
            session.flush()
            result = session.execute(statement)
            if result.rowcount != 1:
                session.rollback()
                raise AgentInvocationConflictError(
                    "agent invocation is no longer waiting for user input"
                )
        except AgentInvocationConflictError:
            raise
        except SQLAlchemyError as exc:
            session.rollback()
            raise WorkspacePersistenceError(
                "failed to finalize agent invocation"
            ) from exc
        self._upsert_steps(session, invocation.id, agent_result.steps)
        self._release_agent_execution_in_transaction(
            session,
            conversation,
            execution_claim_id,
        )
        self._commit_agent_changes(session)

    def _resolve_clarification(
        self,
        pending_input: PendingClarification,
        *,
        answer: str | None,
        skip: bool,
    ) -> ClarificationResolution:
        if utc_now() >= pending_input.expires_at:
            return ClarificationResolution(kind="timed_out")
        if skip:
            if answer is not None:
                raise ValueError("answer and skip cannot be sent together")
            return ClarificationResolution(kind="skipped")
        if answer is None:
            raise ValueError("answer is required unless skip is true")
        resolution = ClarificationResolution(kind="answered", answer=answer)
        if (
            pending_input.input_type == "choice"
            and resolution.answer not in pending_input.choices
        ):
            raise ValueError("answer must match one of the clarification choices")
        return resolution

    def _resolve_pending_step(
        self,
        steps: list[AgentStep],
        resolution: ClarificationResolution,
    ) -> tuple[list[AgentStep], str]:
        for index in range(len(steps) - 1, -1, -1):
            step = steps[index]
            tool_result = step.tool_result
            if (
                tool_result is None
                or tool_result.interaction is None
                or tool_result.interaction.resolution is not None
            ):
                continue
            observation = self._clarification_observation(resolution)
            resolved_result = tool_result.model_copy(
                update={
                    "content": observation,
                    "interaction": tool_result.interaction.model_copy(
                        update={"resolution": resolution}
                    ),
                }
            )
            resolved_step = step.model_copy(
                update={"observation": observation, "tool_result": resolved_result}
            )
            return [*steps[:index], resolved_step, *steps[index + 1 :]], observation
        raise WorkspacePersistenceError("pending clarification step is missing")

    def _clarification_observation(
        self,
        resolution: ClarificationResolution,
    ) -> str:
        if resolution.kind == "answered":
            return f"User answered clarification: {resolution.answer}"
        if resolution.kind == "skipped":
            return "User skipped the clarification request."
        return "Clarification request timed out without a user response."

    def _load_pending_input(self, invocation: AgentInvocation) -> PendingClarification:
        if invocation.pending_input is None:
            raise WorkspacePersistenceError(
                "pending invocation is missing pending_input"
            )
        try:
            return PendingClarification.model_validate(invocation.pending_input)
        except ValidationError as exc:
            raise WorkspacePersistenceError(
                "pending invocation has invalid input"
            ) from exc

    def _load_resume_state(self, invocation: AgentInvocation) -> AgentResumeState:
        if invocation.resume_state is None:
            raise WorkspacePersistenceError(
                "pending invocation is missing resume_state"
            )
        try:
            return AgentResumeState.model_validate(invocation.resume_state)
        except ValidationError as exc:
            raise WorkspacePersistenceError(
                "pending invocation has invalid resume_state"
            ) from exc

    def _steps_for_invocation(
        self,
        session: Session,
        invocation_id: str,
    ) -> list[AgentStep]:
        statement = (
            select(AgentStepRecord)
            .where(AgentStepRecord.invocation_id == invocation_id)
            .order_by(AgentStepRecord.step_index.asc())
        )
        return [
            self._step_from_record(record) for record in session.exec(statement).all()
        ]

    def _upsert_steps(
        self,
        session: Session,
        invocation_id: str,
        steps: list[AgentStep],
    ) -> None:
        statement = select(AgentStepRecord).where(
            AgentStepRecord.invocation_id == invocation_id
        )
        existing = {
            record.step_index: record for record in session.exec(statement).all()
        }
        for step in steps:
            record = existing.get(step.step_index)
            if record is None:
                session.add(self._step_record(invocation_id, step))
                continue
            self._copy_step_to_record(record, step)
            session.add(record)

    def _result_from_agent_run(
        self,
        *,
        conversation_id: str,
        invocation_id: str,
        agent_result,
        metrics: WorkspaceAgentMetrics,
        sources: list[RAGSource],
    ) -> WorkspaceAgentResult:
        return WorkspaceAgentResult(
            conversation_id=conversation_id,
            agent_invocation_id=invocation_id,
            message=agent_result.message,
            status=self._invocation_status(metrics),
            answer=agent_result.answer,
            steps=agent_result.steps,
            sources=sources,
            provider=agent_result.provider,
            model=agent_result.model,
            metrics=metrics,
        )

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
        return AgentStepRecord(
            invocation_id=invocation_id,
            step_index=step.step_index,
            llm_output=step.llm_output,
            action=step.action,
            action_input=step.action_input,
            observation=step.observation,
            ok=step.ok,
            error=step.error,
            tool_result=(
                None
                if step.tool_result is None
                else step.tool_result.model_dump(mode="json")
            ),
        )

    def _copy_step_to_record(self, record: AgentStepRecord, step: AgentStep) -> None:
        record.llm_output = step.llm_output
        record.action = step.action
        record.action_input = step.action_input
        record.observation = step.observation
        record.ok = step.ok
        record.error = step.error
        record.tool_result = (
            None
            if step.tool_result is None
            else step.tool_result.model_dump(mode="json")
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

    def _commit_agent_changes(self, session: Session) -> None:
        try:
            session.commit()
        except SQLAlchemyError as exc:
            session.rollback()
            raise WorkspacePersistenceError(
                "failed to save agent conversation messages"
            ) from exc

    def _commit_pending_agent_changes(self, session: Session) -> None:
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            if "agent_invocations.conversation_id" in str(exc):
                raise AgentInvocationConflictError(
                    "conversation already has an Agent invocation waiting for user input"
                ) from exc
            raise WorkspacePersistenceError(
                "failed to save pending agent invocation"
            ) from exc
        except SQLAlchemyError as exc:
            session.rollback()
            raise WorkspacePersistenceError(
                "failed to save pending agent invocation"
            ) from exc


agent_service = AgentService()
