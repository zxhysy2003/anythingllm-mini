import json
from pathlib import Path

import pytest

from app.models.agent import AgentInvocation, AgentStepRecord
from app.models.conversation import ConversationMessage
from app.models.workspace import utc_now
from app.services.agent_replay_service import (
    AgentReplayExportError,
    AgentReplayFixtureError,
    AgentReplayService,
)
from app.tools.artifacts import ToolArtifacts, ToolSourceArtifact
from app.tools.calculator import CalculatorTool
from app.tools.registry import ToolRegistry, ToolResult, create_default_tool_registry

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "agent_replay"


class NonExecutingCalculator(CalculatorTool):
    def __init__(self):
        self.run_called = False

    async def run(self, input_data, context):
        del input_data, context
        self.run_called = True
        raise AssertionError("replay must not execute tools")


def load_fixture(name: str):
    return AgentReplayService().load_fixture(FIXTURE_DIR / name)


def make_registry(*tools):
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return registry


@pytest.mark.parametrize(
    "fixture_name",
    [
        "react_calculator_success.json",
        "react_parser_failure.json",
        "native_calculator_success.json",
    ],
)
def test_synthetic_agent_replay_fixtures_pass(fixture_name):
    service = AgentReplayService()

    report = service.replay_fixture(
        load_fixture(fixture_name),
        create_default_tool_registry(),
    )

    assert report.passed is True


def test_replay_does_not_execute_tools():
    service = AgentReplayService()
    calculator = NonExecutingCalculator()

    report = service.replay_fixture(
        load_fixture("react_calculator_success.json"),
        make_registry(calculator),
    )

    assert report.passed is True
    assert calculator.run_called is False


def test_native_replay_marks_parser_not_applicable():
    report = AgentReplayService().replay_fixture(
        load_fixture("native_calculator_success.json"),
        create_default_tool_registry(),
    )

    assert report.checks[0].name == "parser"
    assert report.checks[0].status == "not_applicable"


def test_replay_rejects_final_answer_as_a_persisted_step():
    fixture = load_fixture("react_calculator_success.json")
    final_answer_step = fixture.steps[0].model_copy(
        update={
            "llm_output": "Final Answer: this cannot be an AgentStep",
            "action": None,
            "action_input": {},
            "observation": None,
            "ok": True,
            "error": None,
            "tool_result": None,
        }
    )
    invalid_fixture = fixture.model_copy(
        update={
            "steps": [final_answer_step],
            "metrics": fixture.metrics.model_copy(
                update={"tool_call_count": 0, "failed_step_count": 0}
            ),
        }
    )

    report = AgentReplayService().replay_fixture(
        invalid_fixture,
        create_default_tool_registry(),
    )

    assert report.passed is False
    assert any(
        check.name == "parser" and check.status == "failed" for check in report.checks
    )


def test_replay_checks_status_against_max_steps_reached():
    fixture = load_fixture("react_calculator_success.json")
    inconsistent_fixture = fixture.model_copy(
        update={
            "metrics": fixture.metrics.model_copy(update={"max_steps_reached": True})
        }
    )

    report = AgentReplayService().replay_fixture(
        inconsistent_fixture,
        create_default_tool_registry(),
    )

    assert report.passed is False
    assert any(
        check.name == "invocation_status" and check.status == "failed"
        for check in report.checks
    )


def test_replay_reports_parser_registry_source_and_metric_mismatches():
    service = AgentReplayService()
    fixture = load_fixture("react_calculator_success.json")
    step = fixture.steps[0]
    source = ToolSourceArtifact(
        document_id="document_1",
        original_filename="guide.txt",
        chunk_index=0,
        text="Safe fixture source.",
        score=0.9,
    )
    tool_result = step.tool_result.model_copy(
        update={
            "artifacts": ToolArtifacts(
                sources=[source],
                outputs=step.tool_result.artifacts.outputs,
            )
        }
    )
    source_fixture = fixture.model_copy(
        update={
            "steps": [step.model_copy(update={"tool_result": tool_result})],
            "sources": [source],
            "metrics": fixture.metrics.model_copy(update={"source_count": 1}),
        }
    )

    parser_mismatch = source_fixture.model_copy(
        update={
            "steps": [
                source_fixture.steps[0].model_copy(
                    update={"llm_output": "Final Answer: not a tool call"}
                )
            ]
        }
    )
    registry_mismatch = source_fixture.model_copy(
        update={
            "steps": [
                source_fixture.steps[0].model_copy(
                    update={
                        "llm_output": "Action: calculator\nAction Input: {}",
                        "action_input": {},
                    }
                )
            ]
        }
    )
    source_mismatch = source_fixture.model_copy(update={"sources": []})
    metrics_mismatch = source_fixture.model_copy(
        update={"metrics": source_fixture.metrics.model_copy(update={"step_count": 2})}
    )

    parser_report = service.replay_fixture(
        parser_mismatch,
        create_default_tool_registry(),
    )
    registry_report = service.replay_fixture(
        registry_mismatch,
        create_default_tool_registry(),
    )
    source_report = service.replay_fixture(
        source_mismatch,
        create_default_tool_registry(),
    )
    metrics_report = service.replay_fixture(
        metrics_mismatch,
        create_default_tool_registry(),
    )

    assert parser_report.passed is False
    assert any(
        check.name == "parser" and check.status == "failed"
        for check in parser_report.checks
    )
    assert registry_report.passed is False
    assert any(
        check.name == "registry_validation" and check.status == "failed"
        for check in registry_report.checks
    )
    assert source_report.passed is False
    assert any(
        check.name == "source_snapshot" and check.status == "failed"
        for check in source_report.checks
    )
    assert metrics_report.passed is False
    assert any(
        check.name == "derived_metrics" and check.status == "failed"
        for check in metrics_report.checks
    )


@pytest.mark.parametrize(
    ("error", "action_input", "expected_message"),
    [
        ("unknown_tool", {"value": "x"}, "Unknown tool"),
        ("invalid_tool_input", {}, "Invalid input"),
    ],
)
def test_replay_preserves_registry_failure_boundaries(
    error,
    action_input,
    expected_message,
):
    fixture = load_fixture("react_calculator_success.json")
    step = fixture.steps[0]
    action = "missing_tool" if error == "unknown_tool" else "calculator"
    tool_result = ToolResult(
        ok=False,
        content=expected_message,
        error=error,
    )
    failure_step = step.model_copy(
        update={
            "llm_output": (
                f"Action: {action}\n"
                f"Action Input: {json.dumps(action_input, sort_keys=True)}"
            ),
            "action": action,
            "action_input": action_input,
            "observation": expected_message,
            "ok": False,
            "error": error,
            "tool_result": tool_result,
        }
    )
    failure_fixture = fixture.model_copy(
        update={
            "steps": [failure_step],
            "metrics": fixture.metrics.model_copy(update={"failed_step_count": 1}),
        }
    )

    report = AgentReplayService().replay_fixture(
        failure_fixture,
        make_registry(CalculatorTool()),
    )

    assert report.passed is True


def test_export_anonymizes_document_ids_and_omits_persistence_metadata(session):
    source = ToolSourceArtifact(
        document_id="real-document-id",
        original_filename="guide.txt",
        chunk_index=0,
        text="Workspace source text.",
        score=0.91,
    )
    assistant = ConversationMessage(
        id="assistant-message-id",
        conversation_id="conversation-id",
        role="assistant",
        content="I found a source.",
        sources=[source.model_dump(mode="json")],
    )
    invocation = AgentInvocation(
        id="invocation-id",
        workspace_id="workspace-id",
        conversation_id="conversation-id",
        user_message_id="user-message-id",
        assistant_message_id=assistant.id,
        input_message="Find the guide.",
        agent_mode="react_text",
        status="completed",
        provider="fake",
        model="fake-agent-model",
        max_steps=5,
        llm_call_count=2,
        step_count=1,
        tool_call_count=1,
        failed_step_count=0,
        source_count=1,
        max_steps_reached=False,
        total_latency_ms=15,
        started_at=utc_now(),
        ended_at=utc_now(),
    )
    result = ToolResult(
        ok=True,
        content="Found relevant workspace document context.",
        artifacts=ToolArtifacts(sources=[source]),
    )
    step = AgentStepRecord(
        invocation_id=invocation.id,
        step_index=1,
        llm_output='Action: workspace_document_search\nAction Input: {"question": "guide"}',
        action="workspace_document_search",
        action_input={"question": "guide"},
        observation=result.content,
        ok=True,
        tool_result=result.model_dump(mode="json"),
    )
    session.add(assistant)
    session.add(invocation)
    session.add(step)
    session.commit()

    fixture = AgentReplayService().export_invocation(session, invocation.id)
    payload = fixture.model_dump(mode="json")
    serialized = json.dumps(payload, sort_keys=True)

    assert fixture.sources[0].document_id == "document_1"
    assert fixture.steps[0].tool_result.artifacts.sources[0].document_id == "document_1"
    assert fixture.invocation.input_message == "Find the guide."
    assert fixture.invocation.answer == "I found a source."
    assert (
        AgentReplayService()
        .replay_fixture(
            fixture,
            create_default_tool_registry(),
        )
        .passed
        is True
    )
    for value in (
        invocation.id,
        invocation.workspace_id,
        invocation.conversation_id,
        invocation.user_message_id,
        invocation.assistant_message_id,
        "real-document-id",
    ):
        assert value not in serialized
    assert "created_at" not in payload
    assert "started_at" not in payload
    assert "ended_at" not in payload


def test_export_rejects_missing_invocation(session):
    with pytest.raises(AgentReplayExportError, match="agent invocation not found"):
        AgentReplayService().export_invocation(session, "missing-invocation")


def test_fixture_files_are_strict_and_not_overwritten(tmp_path):
    service = AgentReplayService()
    fixture = load_fixture("react_calculator_success.json")
    output_path = tmp_path / "fixture.json"

    service.write_fixture(fixture, output_path)

    with pytest.raises(AgentReplayFixtureError, match="refusing to overwrite"):
        service.write_fixture(fixture, output_path)

    service.write_fixture(fixture, output_path, overwrite=True)
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    payload["unexpected"] = True
    output_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(AgentReplayFixtureError, match="invalid replay fixture"):
        service.load_fixture(output_path)

    payload.pop("unexpected")
    payload["schema_version"] = 2
    output_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(AgentReplayFixtureError, match="invalid replay fixture"):
        service.load_fixture(output_path)

    payload["schema_version"] = 1
    payload["steps"][0].update(
        {
            "llm_output": "Final Answer: invalid persisted step",
            "action": None,
            "action_input": {},
            "observation": None,
            "ok": True,
            "error": None,
            "tool_result": None,
        }
    )
    output_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(AgentReplayFixtureError, match="invalid replay fixture"):
        service.load_fixture(output_path)
