"""Tests for the public dependency-free tracing SDK."""

from pathlib import Path

import pytest

from agentprobe_sdk import (
    trace_agent,
    trace_handoff,
    trace_step,
    trace_tool,
    traced_agent,
    traced_tool,
)
from backend.app.adapters.python_module import _read_steps
from backend.app.runner.models import AgentResult, TraceStep
from backend.app.scorers.agent_routing import score_agent_routing, score_case_routing
from backend.suite.schema import ExpectedHandoff
from backend.suite.schema import TestCase as CaseDefinition


def test_trace_decorators_context_managers_and_handoffs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    trace_file = tmp_path / "events.jsonl"
    monkeypatch.setenv("AGENTPROBE_TRACE_FILE", str(trace_file))

    @traced_tool
    def lookup(order_id: str) -> dict[str, str]:
        return {"status": "shipped", "order": order_id}

    @traced_agent(name="router")
    def handle(order_id: str) -> dict[str, str]:
        with trace_agent("support") as agent_span:
            agent_span.input_tokens = 5
            with trace_tool("get_order_status", {"order_id": order_id}) as tool_span:
                result = lookup(order_id)
                tool_span.result = result
            agent_span.result = "routed"
            with trace_step("llm") as llm_span:
                llm_span.prompt = [{"role": "user", "content": order_id}]
                llm_span.response = "routed"
        trace_handoff("router", "support", result="case routed")
        return result

    assert handle("ORD-1001")["status"] == "shipped"
    steps = _read_steps(trace_file)
    assert [step.kind for step in steps] == [
        "agent",
        "agent",
        "tool",
        "tool",
        "llm",
        "agent_summary",
        "handoff",
        "agent_summary",
    ]
    assert steps[0].agent_name == "router"
    assert steps[1].agent_name == "support"
    assert steps[2].tool_name == "lookup"
    assert steps[3].tool_name == "get_order_status"
    assert steps[4].prompt == [{"role": "user", "content": "ORD-1001"}]
    assert steps[5].input_tokens == 5
    assert steps[6].handoff_to == "support"
    assert AgentResult(final_answer="done", steps=steps).tokens == 5
    detailed = AgentResult(
        final_answer="done",
        steps=[
            TraceStep(
                kind="llm",
                input_tokens=3,
                output_tokens=2,
                cost_usd=0.02,
                latency_ms=10,
            ),
            TraceStep(
                kind="agent_summary",
                input_tokens=300,
                output_tokens=200,
                cost_usd=1.0,
                latency_ms=20,
            ),
        ],
    )
    assert detailed.tokens == 5
    assert detailed.cost_usd == 0.02
    assert detailed.latency_ms == 10


def test_tracing_sdk_is_noop_without_transport_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENTPROBE_TRACE_FILE", raising=False)
    with trace_tool("lookup", {"id": "1"}) as span:
        span.result = "ok"


def test_agent_routing_scorer_reports_route_handoffs_and_first_failure() -> None:
    steps = [
        TraceStep(kind="agent", agent_name="router", latency_ms=1),
        TraceStep(
            kind="handoff",
            agent_name="router",
            handoff_to="billing",
            latency_ms=0,
        ),
        TraceStep(
            kind="tool",
            agent_name="billing",
            tool_name="refund",
            error="ValueError: denied",
            latency_ms=3,
        ),
    ]
    score = score_agent_routing(
        steps,
        expected_agents=["router", "billing"],
        expected_handoffs=[("router", "billing")],
        expected_first_failing_agent="billing",
    )
    assert score.passed
    assert score.first_failing_agent == "billing"
    assert score.observed_agents == ["router", "billing"]


def test_agent_routing_scorer_reports_mismatches() -> None:
    score = score_agent_routing(
        [TraceStep(kind="agent", agent_name="support", latency_ms=1)],
        expected_agents=["router", "support"],
        expected_handoffs=[("router", "support")],
        expected_first_failing_agent="billing",
    )
    assert not score.passed
    assert len(score.mismatches) == 3


def test_case_schema_route_expectations_are_used_by_scorer() -> None:
    case = CaseDefinition(
        id="routed",
        input="refund order",
        expected_agents=["router", "billing"],
        expected_handoffs=[ExpectedHandoff(from_agent="router", to_agent="billing")],
        expected_first_failing_agent="billing",
    )
    steps = [
        TraceStep(kind="agent", agent_name="router", latency_ms=1),
        TraceStep(
            kind="handoff",
            agent_name="router",
            handoff_to="billing",
            latency_ms=0,
        ),
        TraceStep(
            kind="agent_summary",
            agent_name="billing",
            error="RuntimeError: payment failed",
            latency_ms=1,
        ),
    ]
    assert score_case_routing(case, steps).passed
