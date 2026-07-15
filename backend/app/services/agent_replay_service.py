import json
from pathlib import Path
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    model_validator,
)
from sqlmodel import Session, select

from app.core.agent_executor import MAX_AGENT_STEPS
from app.core.agent_loop import parse_agent_output
from app.core.agent_modes import AGENT_MODE_REACT_TEXT
from app.models.agent import (
    AGENT_INVOCATION_STATUS_COMPLETED,
    AGENT_INVOCATION_STATUS_MAX_STEPS_REACHED,
    AgentInvocation,
    AgentStepRecord,
)
from app.models.conversation import ConversationMessage
from app.tools.artifacts import ToolSourceArtifact
from app.tools.registry import ToolRegistry, ToolResult

AGENT_REPLAY_FIXTURE_SCHEMA_VERSION = 1
REPLAY_CHECK_PASSED = "passed"
REPLAY_CHECK_FAILED = "failed"
REPLAY_CHECK_NOT_APPLICABLE = "not_applicable"
ReplayCheckStatus = Literal[
    "passed",
    "failed",
    "not_applicable",
]
ReplayAgentMode = Literal["react_text", "native_tool_calling"]
ReplayInvocationStatus = Literal["completed", "max_steps_reached"]


class AgentReplayError(Exception):
    """Base error for local Agent replay export and validation."""


class AgentReplayExportError(AgentReplayError):
    """Raised when persisted Agent data cannot become a replay fixture."""


class AgentReplayFixtureError(AgentReplayError):
    """Raised when a fixture file is malformed or cannot be written."""


class AgentReplayMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_steps: int = Field(ge=1, le=MAX_AGENT_STEPS)
    llm_call_count: int = Field(ge=0)
    step_count: int = Field(ge=0)
    tool_call_count: int = Field(ge=0)
    failed_step_count: int = Field(ge=0)
    source_count: int = Field(ge=0)
    max_steps_reached: bool
    total_latency_ms: int = Field(ge=0)


class AgentReplayInvocation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_mode: ReplayAgentMode
    status: ReplayInvocationStatus
    provider: str | None = None
    model: str | None = None
    input_message: str
    answer: str


class AgentReplayStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_index: int = Field(ge=1)
    llm_output: str
    action: str | None = None
    action_input: dict[str, JsonValue] = Field(default_factory=dict)
    observation: str | None = None
    ok: bool
    error: str | None = None
    tool_result: ToolResult | None = None

    @model_validator(mode="after")
    def validate_persisted_step_shape(self) -> "AgentReplayStep":
        if self.action is None:
            if self.ok or self.error is None:
                raise ValueError(
                    "action-less replay steps must represent a failed parser result"
                )
            if self.tool_result is not None:
                raise ValueError("action-less replay steps cannot contain a ToolResult")
        return self


class AgentReplayFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = AGENT_REPLAY_FIXTURE_SCHEMA_VERSION
    invocation: AgentReplayInvocation
    metrics: AgentReplayMetrics
    steps: list[AgentReplayStep]
    sources: list[ToolSourceArtifact]


class AgentReplayCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    status: ReplayCheckStatus
    message: str
    step_index: int | None = None


class AgentReplayReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = AGENT_REPLAY_FIXTURE_SCHEMA_VERSION
    agent_mode: ReplayAgentMode
    passed: bool
    checks: list[AgentReplayCheck]


class AgentReplayService:
    """Export persisted invocations and replay deterministic Agent boundaries."""

    def export_invocation(
        self,
        session: Session,
        invocation_id: str,
    ) -> AgentReplayFixture:
        invocation = session.get(AgentInvocation, invocation_id)
        if invocation is None:
            raise AgentReplayExportError(f"agent invocation not found: {invocation_id}")

        assistant_message = session.get(
            ConversationMessage,
            invocation.assistant_message_id,
        )
        if assistant_message is None:
            raise AgentReplayExportError(
                "agent invocation assistant message is missing: "
                f"{invocation.assistant_message_id}"
            )

        records = session.exec(
            select(AgentStepRecord)
            .where(AgentStepRecord.invocation_id == invocation.id)
            .order_by(AgentStepRecord.step_index.asc())
        ).all()
        try:
            fixture = AgentReplayFixture(
                invocation=AgentReplayInvocation(
                    agent_mode=invocation.agent_mode,
                    status=invocation.status,
                    provider=invocation.provider,
                    model=invocation.model,
                    input_message=invocation.input_message,
                    answer=assistant_message.content,
                ),
                metrics=AgentReplayMetrics(
                    max_steps=invocation.max_steps,
                    llm_call_count=invocation.llm_call_count,
                    step_count=invocation.step_count,
                    tool_call_count=invocation.tool_call_count,
                    failed_step_count=invocation.failed_step_count,
                    source_count=invocation.source_count,
                    max_steps_reached=invocation.max_steps_reached,
                    total_latency_ms=invocation.total_latency_ms,
                ),
                steps=[self._fixture_step(record) for record in records],
                sources=[
                    ToolSourceArtifact.model_validate(source)
                    for source in assistant_message.sources
                ],
            )
        except ValidationError as exc:
            raise AgentReplayExportError(
                "persisted agent invocation does not match the replay fixture contract"
            ) from exc
        return self._anonymize_document_ids(fixture)

    def replay_fixture(
        self,
        fixture: AgentReplayFixture,
        tool_registry: ToolRegistry,
    ) -> AgentReplayReport:
        checks: list[AgentReplayCheck] = []

        if fixture.invocation.agent_mode == AGENT_MODE_REACT_TEXT:
            checks.extend(self._replay_react_parser(fixture.steps))
        else:
            checks.append(
                AgentReplayCheck(
                    name="parser",
                    status=REPLAY_CHECK_NOT_APPLICABLE,
                    message="native_tool_calling does not use the react_text parser",
                )
            )

        for step in fixture.steps:
            # Parser failures have no action; native call failures can retain an action
            # but fail before ToolRegistry, so neither path has a ToolResult to replay.
            if step.tool_result is None:
                if step.action is not None:
                    checks.append(
                        AgentReplayCheck(
                            name="registry_validation",
                            status=REPLAY_CHECK_NOT_APPLICABLE,
                            message=(
                                "ToolRegistry was not reached for this recorded "
                                "step."
                            ),
                            step_index=step.step_index,
                        )
                    )
                continue
            checks.append(self._tool_result_contract_check(step))
            checks.append(self._registry_validation_check(step, tool_registry))

        checks.append(self._invocation_status_check(fixture))
        checks.append(self._source_snapshot_check(fixture))
        checks.append(self._derived_metrics_check(fixture))
        return AgentReplayReport(
            agent_mode=fixture.invocation.agent_mode,
            passed=all(check.status != REPLAY_CHECK_FAILED for check in checks),
            checks=checks,
        )

    def write_fixture(
        self,
        fixture: AgentReplayFixture,
        output_path: Path,
        *,
        overwrite: bool = False,
    ) -> None:
        if output_path.exists() and not overwrite:
            raise AgentReplayFixtureError(
                f"refusing to overwrite existing fixture: {output_path}"
            )
        if not output_path.parent.is_dir():
            raise AgentReplayFixtureError(
                f"fixture output directory does not exist: {output_path.parent}"
            )
        try:
            output_path.write_text(
                f"{fixture.model_dump_json(indent=2)}\n",
                encoding="utf-8",
            )
        except OSError as exc:
            raise AgentReplayFixtureError(
                f"failed to write replay fixture: {output_path}"
            ) from exc

    def load_fixture(self, fixture_path: Path) -> AgentReplayFixture:
        try:
            raw_fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AgentReplayFixtureError(
                f"failed to read replay fixture: {fixture_path}"
            ) from exc
        try:
            return AgentReplayFixture.model_validate(raw_fixture)
        except ValidationError as exc:
            raise AgentReplayFixtureError(
                f"invalid replay fixture: {fixture_path}"
            ) from exc

    def _fixture_step(self, record: AgentStepRecord) -> AgentReplayStep:
        tool_result = None
        if record.tool_result is not None:
            tool_result = ToolResult.model_validate(record.tool_result)
        return AgentReplayStep(
            step_index=record.step_index,
            llm_output=record.llm_output,
            action=record.action,
            action_input=record.action_input,
            observation=record.observation,
            ok=record.ok,
            error=record.error,
            tool_result=tool_result,
        )

    def _anonymize_document_ids(
        self,
        fixture: AgentReplayFixture,
    ) -> AgentReplayFixture:
        aliases: dict[str, str] = {}

        def anonymize_source(source: ToolSourceArtifact) -> ToolSourceArtifact:
            alias = aliases.setdefault(
                source.document_id,
                f"document_{len(aliases) + 1}",
            )
            return source.model_copy(update={"document_id": alias})

        anonymized_steps = []
        for step in fixture.steps:
            tool_result = step.tool_result
            if tool_result is not None and tool_result.artifacts.sources:
                artifacts = tool_result.artifacts.model_copy(
                    update={
                        "sources": [
                            anonymize_source(source)
                            for source in tool_result.artifacts.sources
                        ]
                    }
                )
                tool_result = tool_result.model_copy(update={"artifacts": artifacts})
            anonymized_steps.append(
                step.model_copy(update={"tool_result": tool_result})
            )

        return fixture.model_copy(
            update={
                "steps": anonymized_steps,
                "sources": [anonymize_source(source) for source in fixture.sources],
            }
        )

    def _replay_react_parser(
        self,
        steps: list[AgentReplayStep],
    ) -> list[AgentReplayCheck]:
        checks = []
        for step in steps:
            parsed = parse_agent_output(step.llm_output)
            if step.action is None:
                matches = (
                    not parsed.is_final
                    and parsed.error is not None
                    and parsed.error == step.error
                )
                expected = f"parser error {step.error!r}"
                actual = (
                    "final answer"
                    if parsed.is_final
                    else f"parser error {parsed.error!r}"
                )
            else:
                matches = (
                    parsed.error is None
                    and not parsed.is_final
                    and parsed.action == step.action
                    and parsed.action_input == step.action_input
                )
                expected = {
                    "action": step.action,
                    "action_input": step.action_input,
                }
                actual = {
                    "action": parsed.action,
                    "action_input": parsed.action_input,
                    "error": parsed.error,
                }
            checks.append(
                AgentReplayCheck(
                    name="parser",
                    status=(REPLAY_CHECK_PASSED if matches else REPLAY_CHECK_FAILED),
                    message=(
                        "Recorded parser result still matches."
                        if matches
                        else f"Expected {expected}; got {actual}."
                    ),
                    step_index=step.step_index,
                )
            )
        return checks

    def _tool_result_contract_check(
        self,
        step: AgentReplayStep,
    ) -> AgentReplayCheck:
        if step.tool_result is None:
            return AgentReplayCheck(
                name="tool_result_contract",
                status=REPLAY_CHECK_FAILED,
                message="A ToolResult contract check requires a recorded ToolResult.",
                step_index=step.step_index,
            )
        result = step.tool_result
        matches = (
            step.ok == result.ok
            and step.error == result.error
            and step.observation == result.content
        )
        return AgentReplayCheck(
            name="tool_result_contract",
            status=REPLAY_CHECK_PASSED if matches else REPLAY_CHECK_FAILED,
            message=(
                "Recorded step and ToolResult agree."
                if matches
                else "Step fields no longer match the recorded ToolResult."
            ),
            step_index=step.step_index,
        )

    def _registry_validation_check(
        self,
        step: AgentReplayStep,
        tool_registry: ToolRegistry,
    ) -> AgentReplayCheck:
        if step.tool_result is None or step.action is None:
            return AgentReplayCheck(
                name="registry_validation",
                status=REPLAY_CHECK_FAILED,
                message="Registry validation requires a recorded tool action and ToolResult.",
                step_index=step.step_index,
            )
        recorded_error = step.tool_result.error
        try:
            tool = tool_registry.get(step.action)
        except KeyError:
            matches = (
                not step.ok
                and step.error == "unknown_tool"
                and recorded_error == "unknown_tool"
            )
            return AgentReplayCheck(
                name="registry_validation",
                status=REPLAY_CHECK_PASSED if matches else REPLAY_CHECK_FAILED,
                message=(
                    "Unknown tool failure still matches the registry boundary."
                    if matches
                    else "Tool is now missing but the recorded step was not an "
                    "unknown_tool failure."
                ),
                step_index=step.step_index,
            )

        try:
            tool.input_model.model_validate(step.action_input)
        except ValidationError:
            matches = (
                not step.ok
                and step.error == "invalid_tool_input"
                and recorded_error == "invalid_tool_input"
            )
            return AgentReplayCheck(
                name="registry_validation",
                status=REPLAY_CHECK_PASSED if matches else REPLAY_CHECK_FAILED,
                message=(
                    "Invalid input failure still matches the registry boundary."
                    if matches
                    else "Tool input is now invalid but the recorded step was not "
                    "an invalid_tool_input failure."
                ),
                step_index=step.step_index,
            )

        if recorded_error in {"unknown_tool", "invalid_tool_input"}:
            return AgentReplayCheck(
                name="registry_validation",
                status=REPLAY_CHECK_FAILED,
                message=(
                    "Tool lookup and input validation now succeed, but the recorded "
                    f"failure was {recorded_error}."
                ),
                step_index=step.step_index,
            )
        return AgentReplayCheck(
            name="registry_validation",
            status=REPLAY_CHECK_PASSED,
            message=(
                "Tool lookup and input validation succeed; tool execution and policy "
                "are intentionally not replayed."
            ),
            step_index=step.step_index,
        )

    def _invocation_status_check(
        self,
        fixture: AgentReplayFixture,
    ) -> AgentReplayCheck:
        expected_status = (
            AGENT_INVOCATION_STATUS_MAX_STEPS_REACHED
            if fixture.metrics.max_steps_reached
            else AGENT_INVOCATION_STATUS_COMPLETED
        )
        matches = fixture.invocation.status == expected_status
        return AgentReplayCheck(
            name="invocation_status",
            status=REPLAY_CHECK_PASSED if matches else REPLAY_CHECK_FAILED,
            message=(
                "Invocation status matches max_steps_reached."
                if matches
                else (
                    f"Expected invocation status {expected_status!r} from "
                    f"max_steps_reached={fixture.metrics.max_steps_reached}; got "
                    f"{fixture.invocation.status!r}."
                )
            ),
        )

    def _source_snapshot_check(
        self,
        fixture: AgentReplayFixture,
    ) -> AgentReplayCheck:
        artifact_sources = [
            source.model_dump(mode="json")
            for step in fixture.steps
            if step.tool_result is not None
            for source in step.tool_result.artifacts.sources
        ]
        snapshot_sources = [
            source.model_dump(mode="json") for source in fixture.sources
        ]
        matches = artifact_sources == snapshot_sources
        return AgentReplayCheck(
            name="source_snapshot",
            status=REPLAY_CHECK_PASSED if matches else REPLAY_CHECK_FAILED,
            message=(
                "Assistant source snapshot matches ToolResult artifacts."
                if matches
                else "Assistant source snapshot no longer matches ToolResult artifacts."
            ),
        )

    def _derived_metrics_check(
        self,
        fixture: AgentReplayFixture,
    ) -> AgentReplayCheck:
        derived_metrics = {
            "step_count": len(fixture.steps),
            "tool_call_count": sum(1 for step in fixture.steps if step.action),
            "failed_step_count": sum(1 for step in fixture.steps if not step.ok),
            "source_count": len(fixture.sources),
        }
        recorded_metrics = {
            name: getattr(fixture.metrics, name) for name in derived_metrics
        }
        matches = derived_metrics == recorded_metrics
        return AgentReplayCheck(
            name="derived_metrics",
            status=REPLAY_CHECK_PASSED if matches else REPLAY_CHECK_FAILED,
            message=(
                "Derived step and source metrics still match."
                if matches
                else f"Expected {recorded_metrics}; derived {derived_metrics}."
            ),
        )


agent_replay_service = AgentReplayService()
