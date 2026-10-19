"""Shared adapter contracts and response normalization."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Protocol

from backend.app.runner.models import AgentResult, TraceStep


class AgentAdapter(Protocol):
    """Adapter contract: execute one input and return a normalized trace result."""

    async def run(
        self,
        case_input: str,
        *,
        temperature: float = 0.0,
        seed: int | None = None,
        max_steps: int = 5,
    ) -> AgentResult: ...


def normalize_agent_result(value: Any) -> AgentResult:
    """Convert a target return value into the shared result model."""
    if isinstance(value, AgentResult):
        return value
    if isinstance(value, str):
        return AgentResult(final_answer=value)
    if not isinstance(value, Mapping):
        raise TypeError("Agent entry point must return a string or a result mapping.")
    answer = value.get("final_answer", value.get("output"))
    if not isinstance(answer, str):
        raise ValueError("Agent result mapping must contain string 'final_answer' or 'output'.")
    steps = value.get("steps", [])
    if not isinstance(steps, list):
        raise ValueError("Agent result 'steps' must be a list.")
    return AgentResult(
        final_answer=answer,
        steps=[TraceStep.model_validate(step) for step in steps],
        error=value.get("error"),
    )
