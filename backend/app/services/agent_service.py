import asyncio
from datetime import datetime, timedelta
from time import perf_counter
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, ValidationError
from sqlmodel import Session

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
)
from app.models.conversation import (
    Conversation,
    ConversationMessage,
)
from app.models.workspace import utc_now
from app.services.chat_service import (
    ChatService,
    chat_service,
    tool_calling_chat_service,
)
from app.services.agent_invocation_store import (
    AgentInvocationStore,
    agent_invocation_store,
)
from app.services.exceptions import (
    AgentInvocationConflictError,
    ConversationNotFoundError,
    WorkspacePersistenceError,
)
from app.services.rag_service import RAGSource
from app.services.workspace_service import (
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
    create_default_tool_registry,
)

CLARIFICATION_TIMEOUT = timedelta(minutes=10)
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
        invocation_store: AgentInvocationStore | None = None,
    ):
        self.workspace = workspace or workspace_service
        self.invocation_store = invocation_store or agent_invocation_store
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
            self.invocation_store.require_no_pending_invocation(
                session,
                context.workspace.id,
                context.conversation.id,
            )
            claim_id = uuid4().hex
            self.invocation_store.claim_execution(
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
                invocation_id = self.invocation_store.save_pending(
                    session=session,
                    workspace_id=context.workspace.id,
                    conversation=context.conversation,
                    user_content=context.message,
                    agent_result=agent_result,
                    metrics=metrics.model_dump(mode="json"),
                    pending_input=pending_input.model_dump(mode="json"),
                    resume_state=AgentResumeState(
                        system_prompt=system_prompt,
                        history=[dict(item) for item in context.history],
                        temperature=context.workspace.temperature,
                        executor_state=agent_result.resume_state,
                    ).model_dump(mode="json"),
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
            invocation_id = self.invocation_store.save_completed(
                session=session,
                workspace_id=context.workspace.id,
                conversation=context.conversation,
                user_content=context.message,
                agent_result=agent_result,
                status=self._invocation_status(metrics),
                metrics=metrics.model_dump(mode="json"),
                sources=sources,
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
                    self.invocation_store.release_execution(
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
            self.invocation_store.claim_execution(
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
            steps = self.invocation_store.list_steps(session, invocation.id)
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
            self.invocation_store.finalize(
                session=session,
                invocation=invocation,
                conversation=conversation,
                agent_result=agent_result,
                status=self._invocation_status(metrics),
                metrics=metrics.model_dump(mode="json"),
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
                    self.invocation_store.release_execution(
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
        payload["steps"] = self.invocation_store.list_steps(session, invocation.id)
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
            self.invocation_store.renew_execution_claim(
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
        return self.invocation_store.get_invocation_record(
            session,
            workspace_id,
            conversation_id,
            invocation_id,
        )

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

    def _invocation_status(self, metrics: WorkspaceAgentMetrics) -> str:
        if metrics.max_steps_reached:
            return AGENT_INVOCATION_STATUS_MAX_STEPS_REACHED
        return AGENT_INVOCATION_STATUS_COMPLETED


agent_service = AgentService()
