import json
from pathlib import Path

import pytest

from app.models.agent import (
    AGENT_INVOCATION_STATUS_NEEDS_INPUT,
    AgentInvocation,
    AgentStepRecord,
)
from app.models.conversation import ConversationMessage
from app.models.workspace import utc_now
from app.services.agent_replay_service import (
    AgentReplayExportError,
    AgentReplayFixtureError,
    AgentReplayService,
)
from app.tools.artifacts import ToolArtifacts, ToolSourceArtifact
from app.tools.calculator import CalculatorTool
from app.tools.interactions import ClarificationRequest, ToolInteraction
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
        "react_clarification_answered.json",
        "react_clarification_skipped.json",
        "react_clarification_timed_out.json",
        "native_clarification_answered.json",
        "react_document_summary_complete.json",
        "native_document_summary_partial_limit.json",
        "react_document_summary_partial_failure.json",
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
        display_filename="guide.txt",
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


def test_replay_rejects_invalid_clarification_choice_resolution():
    fixture = load_fixture("react_clarification_answered.json")
    step = fixture.steps[0]
    tool_result = step.tool_result.model_copy(
        update={
            "interaction": ToolInteraction(
                kind="clarification",
                request=ClarificationRequest.model_validate(step.action_input),
                resolution={"kind": "answered", "answer": "unlisted"},
            )
        }
    )
    invalid_fixture = fixture.model_copy(
        update={"steps": [step.model_copy(update={"tool_result": tool_result})]}
    )

    report = AgentReplayService().replay_fixture(
        invalid_fixture,
        create_default_tool_registry(),
    )

    assert report.passed is False
    assert any(
        check.name == "clarification" and check.status == "failed"
        for check in report.checks
    )


def test_export_anonymizes_document_ids_and_omits_persistence_metadata(session):
    source = ToolSourceArtifact(
        document_id="real-document-id",
        display_filename="guide.txt",
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

    anonymized_document_id = f"{1:032x}"
    assert fixture.sources[0].document_id == anonymized_document_id
    assert (
        fixture.steps[0].tool_result.artifacts.sources[0].document_id
        == anonymized_document_id
    )
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


def test_export_anonymizes_summary_document_ids_in_all_fixture_locations(session):
    document_id = f"{1:032x}"
    source = ToolSourceArtifact(
        document_id=document_id,
        display_filename="guide.txt",
        chunk_index=0,
        text=f"Workspace summary source text for {document_id}.",
        score=None,
    )
    assistant = ConversationMessage(
        id="summary-assistant-message-id",
        conversation_id="summary-conversation-id",
        role="assistant",
        content=f"Summary for document {document_id}.",
        sources=[source.model_dump(mode="json")],
    )
    invocation = AgentInvocation(
        id="summary-invocation-id",
        workspace_id="summary-workspace-id",
        conversation_id="summary-conversation-id",
        user_message_id="summary-user-message-id",
        assistant_message_id=assistant.id,
        input_message=f"Summarize document {document_id}.",
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
        content=f"Summary of guide.txt for document {document_id}.",
        artifacts=ToolArtifacts(
            sources=[source],
            outputs={
                "action": "summarize",
                "document_id": document_id,
            },
        ),
    )
    step = AgentStepRecord(
        invocation_id=invocation.id,
        step_index=1,
        llm_output=(
            "Action: workspace_document_summary\n"
            "Action Input: "
            f'{{"action": "summarize", "document_id": "{document_id}"}}'
        ),
        action="workspace_document_summary",
        action_input={
            "action": "summarize",
            "document_id": document_id,
        },
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
    anonymized_document_id = fixture.sources[0].document_id
    exported_step = fixture.steps[0]

    assert len(anonymized_document_id) == 32
    assert anonymized_document_id != document_id
    assert exported_step.action_input["document_id"] == anonymized_document_id
    assert (
        exported_step.tool_result.artifacts.outputs["document_id"]
        == anonymized_document_id
    )
    assert anonymized_document_id in exported_step.llm_output
    assert anonymized_document_id in exported_step.observation
    assert anonymized_document_id in exported_step.tool_result.content
    assert anonymized_document_id in fixture.invocation.input_message
    assert anonymized_document_id in fixture.invocation.answer
    assert document_id not in serialized
    assert (
        AgentReplayService()
        .replay_fixture(
            fixture,
            create_default_tool_registry(),
        )
        .passed
        is True
    )


def test_anonymization_updates_clarification_interaction_and_replays():
    document_id = f"{7:032x}"
    anonymized_document_id = f"{1:032x}"
    selected_choice = f"annual report from {document_id}"
    request = ClarificationRequest(
        question=f"Which report from {document_id} should I prepare?",
        input_type="choice",
        choices=[selected_choice, "market report"],
    )
    source = ToolSourceArtifact(
        document_id=document_id,
        display_filename="guide.txt",
        chunk_index=0,
        text=f"Reporting instructions from {document_id}.",
        score=0.91,
    )
    fixture = load_fixture("react_clarification_answered.json")
    step = fixture.steps[0]
    tool_result = step.tool_result.model_copy(
        update={
            "content": f"User answered clarification: {selected_choice}",
            "artifacts": ToolArtifacts(sources=[source]),
            "interaction": ToolInteraction(
                kind="clarification",
                request=request,
                resolution={"kind": "answered", "answer": selected_choice},
            ),
        }
    )
    raw_fixture = fixture.model_copy(
        update={
            "invocation": fixture.invocation.model_copy(
                update={
                    "input_message": f"Prepare a report from {document_id}.",
                    "answer": f"Preparing {selected_choice}.",
                }
            ),
            "metrics": fixture.metrics.model_copy(update={"source_count": 1}),
            "steps": [
                step.model_copy(
                    update={
                        "llm_output": (
                            "Action: request_user_input\n"
                            "Action Input: "
                            f"{json.dumps(request.model_dump(mode='json'))}"
                        ),
                        "action_input": request.model_dump(mode="json"),
                        "observation": tool_result.content,
                        "tool_result": tool_result,
                    }
                )
            ],
            "sources": [source],
        }
    )

    anonymized_fixture = AgentReplayService()._anonymize_document_ids(raw_fixture)
    interaction = anonymized_fixture.steps[0].tool_result.interaction
    serialized = anonymized_fixture.model_dump_json()

    assert interaction.request.question == (
        f"Which report from {anonymized_document_id} should I prepare?"
    )
    assert interaction.request.choices[0] == (
        f"annual report from {anonymized_document_id}"
    )
    assert interaction.resolution.answer == (
        f"annual report from {anonymized_document_id}"
    )
    assert document_id not in serialized
    assert (
        AgentReplayService()
        .replay_fixture(
            anonymized_fixture,
            create_default_tool_registry(),
        )
        .passed
        is True
    )


def test_export_and_replay_support_pending_clarification(session):
    source = ToolSourceArtifact(
        document_id="pending-source-document-id",
        display_filename="guide.txt",
        chunk_index=0,
        text="The workspace guide says annual reports use the standard format.",
        score=0.91,
    )
    request = ClarificationRequest(
        question="Which report should I prepare?",
        input_type="choice",
        choices=["annual", "market"],
    )
    invocation = AgentInvocation(
        id="pending-invocation-id",
        workspace_id="workspace-id",
        conversation_id="conversation-id",
        user_message_id="user-message-id",
        input_message="Prepare the report.",
        agent_mode="react_text",
        status=AGENT_INVOCATION_STATUS_NEEDS_INPUT,
        provider="fake",
        model="fake-agent-model",
        max_steps=3,
        llm_call_count=2,
        step_count=2,
        tool_call_count=2,
        failed_step_count=0,
        source_count=1,
        max_steps_reached=False,
        total_latency_ms=7,
        started_at=utc_now(),
        pending_input={
            **request.model_dump(mode="json"),
            "expires_at": "2026-07-15T00:10:00+00:00",
        },
        resume_state={"system_prompt": "agent", "history": [], "temperature": None},
    )
    search_result = ToolResult(
        ok=True,
        content="Found relevant workspace document context.",
        artifacts=ToolArtifacts(sources=[source]),
    )
    search_step = AgentStepRecord(
        invocation_id=invocation.id,
        step_index=1,
        llm_output=(
            "Action: workspace_document_search\n"
            'Action Input: {"question": "report format"}'
        ),
        action="workspace_document_search",
        action_input={"question": "report format"},
        observation=search_result.content,
        ok=True,
        tool_result=search_result.model_dump(mode="json"),
    )
    clarification_result = ToolResult(
        ok=True,
        content="Clarification requested. Waiting for the user response.",
        interaction=ToolInteraction(kind="clarification", request=request),
    )
    clarification_step = AgentStepRecord(
        invocation_id=invocation.id,
        step_index=2,
        llm_output=(
            "Action: request_user_input\n"
            'Action Input: {"question": "Which report should I prepare?", '
            '"input_type": "choice", "choices": ["annual", "market"]}'
        ),
        action="request_user_input",
        action_input=request.model_dump(mode="json"),
        observation=clarification_result.content,
        ok=True,
        tool_result=clarification_result.model_dump(mode="json"),
    )
    session.add(invocation)
    session.add(search_step)
    session.add(clarification_step)
    session.commit()

    fixture = AgentReplayService().export_invocation(session, invocation.id)
    report = AgentReplayService().replay_fixture(
        fixture,
        create_default_tool_registry(),
    )

    assert fixture.invocation.status == AGENT_INVOCATION_STATUS_NEEDS_INPUT
    assert fixture.invocation.answer is None
    anonymized_document_id = f"{1:032x}"
    assert fixture.sources[0].document_id == anonymized_document_id
    assert (
        fixture.steps[0].tool_result.artifacts.sources[0].document_id
        == anonymized_document_id
    )
    assert fixture.steps[1].tool_result.interaction.resolution is None
    assert report.passed is True


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
    payload["schema_version"] = 1
    output_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(AgentReplayFixtureError, match="invalid replay fixture"):
        service.load_fixture(output_path)

    payload["schema_version"] = 2
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
