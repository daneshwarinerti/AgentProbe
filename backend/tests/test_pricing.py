"""Unit tests for model pricing and cost estimation."""

from backend.app.core.config import ModelPrice, Settings
from backend.app.pricing import enrich_execution_costs, estimate_step_cost, resolve_model_price
from backend.app.runner.models import ExecutionResult, TraceStep


def test_resolve_model_price_exact_and_prefix() -> None:
    settings = Settings()

    # Exact match from defaults
    price = resolve_model_price(settings, "gpt-4o")
    assert price is not None
    assert price.input_per_million_usd == 2.50
    assert price.output_per_million_usd == 10.00

    # Prefix match from defaults
    price_prefix = resolve_model_price(settings, "gpt-4o-2024-08-06")
    assert price_prefix is not None
    assert price_prefix.input_per_million_usd == 2.50

    # Unknown model with no fallback configured
    unknown = resolve_model_price(settings, "completely-unknown-custom-model")
    assert unknown is None


def test_estimate_step_cost() -> None:
    price = ModelPrice(input_per_million_usd=2.0, output_per_million_usd=8.0)
    step = TraceStep(kind="llm", latency_ms=5.0, input_tokens=1_000_000, output_tokens=500_000)
    cost = estimate_step_cost(step, price)
    assert cost == 2.0 + 4.0  # 6.0 USD


def test_enrich_execution_costs() -> None:
    price = ModelPrice(input_per_million_usd=1.0, output_per_million_usd=2.0)
    exec_result = ExecutionResult(
        case_id="case1",
        repeat_index=0,
        success=True,
        attempts=1,
        duration_ms=50.0,
        output="answer",
        steps=[
            TraceStep(kind="llm", latency_ms=10.0, input_tokens=10_000, output_tokens=5_000),
            TraceStep(kind="tool", latency_ms=2.0, tool_name="search", cost_usd=0.0),
        ],
    )
    enriched = enrich_execution_costs(exec_result, price)
    assert len(enriched.steps) == 2
    # step 0 cost = 10k * 1 / 1M + 5k * 2 / 1M = 0.01 + 0.01 = 0.02
    assert enriched.steps[0].cost_usd == 0.02
    # tool step remains unchanged
    assert enriched.steps[1].cost_usd == 0.0
