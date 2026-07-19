import asyncio

import pytest
from pydantic import ValidationError

from app.tools.clarifying_question import ClarifyingQuestionTool
from app.tools.interactions import ClarificationRequest
from app.tools.registry import ToolContext, ToolRegistry


def make_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(ClarifyingQuestionTool())
    return registry


def test_clarifying_tool_returns_non_artifact_interaction():
    result = asyncio.run(
        make_registry().run(
            "request_user_input",
            {
                "question": " Which report? ",
                "input_type": "choice",
                "choices": ["annual", "market"],
            },
            ToolContext(remaining_llm_calls=1),
        )
    )

    assert result.ok is True
    assert result.interaction is not None
    assert result.interaction.request.question == "Which report?"
    assert result.interaction.request.choices == ["annual", "market"]
    assert result.artifacts.sources == []
    assert result.artifacts.outputs == {}


@pytest.mark.parametrize(
    "input_data",
    [
        {"question": "", "input_type": "text"},
        {"question": "Which?", "input_type": "choice", "choices": ["one"]},
        {
            "question": "Which?",
            "input_type": "choice",
            "choices": ["one", "one"],
        },
        {"question": "Which?", "input_type": "text", "choices": ["one", "two"]},
    ],
)
def test_clarifying_question_input_contract_rejects_invalid_requests(input_data):
    with pytest.raises(ValidationError):
        ClarificationRequest.model_validate(input_data)


def test_clarifying_tool_does_not_pause_without_limit_or_remaining_budget():
    registry = make_registry()
    request = {"question": "Which?", "input_type": "text"}

    limit_result = asyncio.run(
        registry.run(
            "request_user_input",
            request,
            ToolContext(clarification_count=1, remaining_llm_calls=1),
        )
    )
    last_step_result = asyncio.run(
        registry.run(
            "request_user_input",
            request,
            ToolContext(remaining_llm_calls=0),
        )
    )

    assert limit_result.error == "clarification_limit_reached"
    assert limit_result.interaction is None
    assert last_step_result.error == "clarification_requires_remaining_step"
    assert last_step_result.interaction is None
