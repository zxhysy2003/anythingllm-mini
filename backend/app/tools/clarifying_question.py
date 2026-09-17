from pydantic import BaseModel

from app.tools.interactions import ClarificationRequest, ToolInteraction
from app.tools.registry import ToolContext, ToolResult

REQUEST_USER_INPUT_TOOL_NAME = "request_user_input"
MAX_CLARIFICATION_QUESTIONS = 1


class ClarifyingQuestionTool:
    name = REQUEST_USER_INPUT_TOOL_NAME
    description = (
        "Ask the user one structured clarification when required information is "
        "missing. Use text for free-form input or choice for a bounded selection."
    )
    input_model = ClarificationRequest
    risk_level = "low"
    side_effects = False
    requires_confirmation = False
    allowed_in_agent_modes = None

    async def run(
        self,
        input_data: BaseModel,
        context: ToolContext,
    ) -> ToolResult:
        request = ClarificationRequest.model_validate(input_data)
        if context.clarification_count >= MAX_CLARIFICATION_QUESTIONS:
            return ToolResult(
                ok=False,
                content="Clarification limit reached; continue without another question.",
                error="clarification_limit_reached",
                error_details={"max_questions": MAX_CLARIFICATION_QUESTIONS},
            )
        if context.remaining_llm_calls is not None and context.remaining_llm_calls < 1:
            return ToolResult(
                ok=False,
                content=(
                    "Clarification requires one remaining Agent step to use the "
                    "user response."
                ),
                error="clarification_requires_remaining_step",
            )
        return ToolResult(
            ok=True,
            content="Clarification requested. Waiting for the user response.",
            interaction=ToolInteraction(kind="clarification", request=request),
        )
