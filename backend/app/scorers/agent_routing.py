"""Multi-agent route, handoff, and first-failure expectations."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from backend.app.runner.models import TraceStep
from backend.suite.schema import TestCase


class AgentRoutingScore(BaseModel):
    """Observed agent route compared with the expected route contract."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    observed_agents: list[str]
    observed_handoffs: list[tuple[str, str]]
    first_failing_agent: str | None
    mismatches: list[str]


def score_agent_routing(
    steps: list[TraceStep],
    *,
    expected_agents: list[str] | None = None,
    expected_handoffs: list[tuple[str, str]] | None = None,
    expected_first_failing_agent: str | None = None,
) -> AgentRoutingScore:
    """Score agent order, explicit handoffs, and the first observed agent failure."""
    observed_agents: list[str] = []
    observed_handoffs: list[tuple[str, str]] = []
    first_failing_agent: str | None = None
    for step in steps:
        if (
            step.kind == "agent"
            and step.agent_name
            and (not observed_agents or observed_agents[-1] != step.agent_name)
        ):
            observed_agents.append(step.agent_name)
        if step.kind == "handoff" and step.agent_name and step.handoff_to:
            observed_handoffs.append((step.agent_name, step.handoff_to))
            if not observed_agents or observed_agents[-1] != step.handoff_to:
                observed_agents.append(step.handoff_to)
        failed = step.error is not None or (
            isinstance(step.result, dict) and "error" in step.result
        )
        if failed and step.agent_name and first_failing_agent is None:
            first_failing_agent = step.agent_name

    mismatches: list[str] = []
    if expected_agents is not None and observed_agents != expected_agents:
        mismatches.append(
            f"Expected agent route {expected_agents!r}, observed {observed_agents!r}."
        )
    if expected_handoffs is not None and observed_handoffs != expected_handoffs:
        mismatches.append(
            f"Expected handoffs {expected_handoffs!r}, observed {observed_handoffs!r}."
        )
    if (
        expected_first_failing_agent is not None
        and first_failing_agent != expected_first_failing_agent
    ):
        mismatches.append(
            "Expected first failing agent "
            f"{expected_first_failing_agent!r}, observed {first_failing_agent!r}."
        )
    return AgentRoutingScore(
        passed=not mismatches,
        observed_agents=observed_agents,
        observed_handoffs=observed_handoffs,
        first_failing_agent=first_failing_agent,
        mismatches=mismatches,
    )


def score_case_routing(case: TestCase, steps: list[TraceStep]) -> AgentRoutingScore:
    """Score routing and handoffs using expectations declared on a case."""
    return score_agent_routing(
        steps,
        expected_agents=case.expected_agents or None,
        expected_handoffs=[
            (handoff.from_agent, handoff.to_agent) for handoff in case.expected_handoffs
        ]
        if case.expected_handoffs
        else None,
        expected_first_failing_agent=case.expected_first_failing_agent,
    )
