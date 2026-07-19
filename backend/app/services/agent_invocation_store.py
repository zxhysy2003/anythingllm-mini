from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, or_, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlmodel import Session, select

from app.core.agent_executor import AgentRunResult, AgentStep
from app.models.agent import (
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
from app.services.exceptions import (
    AgentInvocationConflictError,
    AgentInvocationNotFoundError,
    WorkspacePersistenceError,
)
from app.services.rag_service import RAGSource
from app.services.workspace_service import AUTO_TITLE_LENGTH
from app.tools.registry import ToolResult

AGENT_EXECUTION_CLAIM_LEASE = timedelta(minutes=15)


class AgentInvocationStore:
    """Persist Agent invocation lifecycle state and execution claims."""

    def require_no_pending_invocation(
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

    def claim_execution(
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

    def renew_execution_claim(
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
            .values(agent_execution_claimed_at=utc_now())
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

    def release_execution(
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

    def get_invocation_record(
        self,
        session: Session,
        workspace_id: str,
        conversation_id: str,
        invocation_id: str,
    ) -> AgentInvocation:
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

    def list_steps(
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

    def save_pending(
        self,
        *,
        session: Session,
        workspace_id: str,
        conversation: Conversation,
        user_content: str,
        agent_result: AgentRunResult,
        metrics: dict[str, Any],
        pending_input: dict[str, Any],
        resume_state: dict[str, Any],
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
            pending_input=pending_input,
            resume_state=resume_state,
            **metrics,
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
        self._release_execution_in_transaction(
            session,
            conversation,
            execution_claim_id,
            user_content=user_content,
        )
        session.add(invocation)
        for step in agent_result.steps:
            session.add(self._step_record(invocation.id, step))
        self._commit_pending_changes(session)
        return invocation.id

    def save_completed(
        self,
        *,
        session: Session,
        workspace_id: str,
        conversation: Conversation,
        user_content: str,
        agent_result: AgentRunResult,
        status: str,
        metrics: dict[str, Any],
        sources: list[RAGSource],
        started_at: datetime,
        ended_at: datetime,
        execution_claim_id: str,
    ) -> str:
        user_message = ConversationMessage(
            conversation_id=conversation.id,
            role="user",
            content=user_content,
        )
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
            status=status,
            provider=agent_result.provider,
            model=agent_result.model,
            started_at=started_at,
            ended_at=ended_at,
            **metrics,
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
        self._release_execution_in_transaction(
            session,
            conversation,
            execution_claim_id,
            user_content=user_content,
        )
        self._commit_changes(session)
        return invocation.id

    def finalize(
        self,
        *,
        session: Session,
        invocation: AgentInvocation,
        conversation: Conversation,
        agent_result: AgentRunResult,
        status: str,
        metrics: dict[str, Any],
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
                status=status,
                provider=agent_result.provider or invocation.provider,
                model=agent_result.model or invocation.model,
                ended_at=ended_at,
                pending_input=None,
                resume_state=None,
                **metrics,
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
        self._release_execution_in_transaction(
            session,
            conversation,
            execution_claim_id,
        )
        self._commit_changes(session)

    def _release_execution_in_transaction(
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

    def _message_metrics(
        self,
        metrics: dict[str, Any],
        agent_invocation_id: str,
        agent_mode: str,
    ) -> dict[str, Any]:
        return {
            **metrics,
            "agent_mode": agent_mode,
            "agent_invocation_id": agent_invocation_id,
        }

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

    def _commit_changes(self, session: Session) -> None:
        try:
            session.commit()
        except SQLAlchemyError as exc:
            session.rollback()
            raise WorkspacePersistenceError(
                "failed to save agent conversation messages"
            ) from exc

    def _commit_pending_changes(self, session: Session) -> None:
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


agent_invocation_store = AgentInvocationStore()
