from typing import Literal, Protocol

from pydantic import BaseModel, Field

ToolProgressPhase = Literal["loading", "summarizing", "reducing"]


class ToolProgress(BaseModel):
    phase: ToolProgressPhase
    completed_units: int = Field(ge=0)
    total_units: int = Field(ge=0)
    message: str = Field(min_length=1, max_length=500)


class ToolProgressReporter(Protocol):
    async def report(self, progress: ToolProgress) -> None: ...


async def report_tool_progress(
    reporter: ToolProgressReporter | None,
    *,
    phase: ToolProgressPhase,
    completed_units: int,
    total_units: int,
    message: str,
) -> None:
    if reporter is None:
        return
    await reporter.report(
        ToolProgress(
            phase=phase,
            completed_units=completed_units,
            total_units=total_units,
            message=message,
        )
    )
