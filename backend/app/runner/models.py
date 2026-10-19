"""Typed models used by agent execution and trace recording."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


class TraceStep(BaseModel):
    """A single model, tool, or runner event produced by an agent execution."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["llm", "tool", "limit", "handoff", "agent", "agent_summary"]
    agent_name: str | None = None
    handoff_to: str | None = None
    error: str | None = None
    prompt: list[dict[str, Any]] | None = None
    response: Any | None = None
    tool_name: str | None = None
    args: dict[str, Any] | None = None
    result: Any | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    latency_ms: float = Field(ge=0)
    cost_usd: float | None = Field(default=None, ge=0)


class AgentResult(BaseModel):
    """Output and ordered trace steps returned by an agent."""

    final_answer: str = Field(validation_alias=AliasChoices("final_answer", "output"))
    steps: list[TraceStep] = Field(default_factory=list)
    error: str | None = None
    tokens: int | None = Field(default=None, ge=0)
    cost_usd: float | None = Field(default=None, ge=0)
    latency_ms: float = Field(default=0.0, ge=0)

    @property
    def output(self) -> str:
        """Backward-compatible name used by the Phase 1 runner."""
        return self.final_answer

    @model_validator(mode="after")
    def aggregate_trace_metrics(self) -> AgentResult:
        """Fill aggregate metrics from detailed calls or agent-level summaries."""
        model_steps = [step for step in self.steps if step.kind == "llm"]
        if self.tokens is None:
            if model_steps and all(
                step.input_tokens is not None and step.output_tokens is not None
                for step in model_steps
            ):
                self.tokens = sum(
                    (step.input_tokens or 0) + (step.output_tokens or 0) for step in model_steps
                )
            else:
                summary_steps = [step for step in self.steps if step.kind == "agent_summary"]
                if summary_steps:
                    summary_tokens = [
                        value
                        for step in summary_steps
                        for value in (step.input_tokens, step.output_tokens)
                        if value is not None
                    ]
                    if summary_tokens:
                        self.tokens = sum(summary_tokens)
                elif not model_steps:
                    reported_tokens = [
                        value
                        for step in self.steps
                        for value in (step.input_tokens, step.output_tokens)
                        if value is not None
                    ]
                    if reported_tokens:
                        self.tokens = sum(reported_tokens)

        model_costs = [step.cost_usd for step in model_steps if step.cost_usd is not None]
        cost_steps = model_steps if model_costs else self.steps
        reported_costs = [step.cost_usd for step in cost_steps if step.cost_usd is not None]
        if self.cost_usd is None and reported_costs:
            if not model_costs or all(step.cost_usd is not None for step in model_steps):
                self.cost_usd = sum(reported_costs)

        leaf_steps = [step for step in self.steps if step.kind in {"llm", "tool"}]
        candidates = leaf_steps or self.steps
        if self.latency_ms == 0.0 and candidates:
            self.latency_ms = sum(step.latency_ms for step in candidates)
        return self


class ExecutionResult(BaseModel):
    """Outcome of one attempt series for one case repeat."""

    case_id: str
    repeat_index: int = Field(ge=0)
    success: bool
    output: str | None = None
    error: str | None = None
    steps: list[TraceStep] = Field(default_factory=list)
    attempts: int = Field(ge=1)
    duration_ms: float = Field(ge=0)
