"""Pricing tables and cost estimation utilities."""

from __future__ import annotations

from backend.app.core.config import ModelPrice, Settings
from backend.app.runner.models import ExecutionResult, TraceStep

# Default fallback prices per 1 million tokens (input, output) in USD
DEFAULT_MODEL_PRICES: dict[str, ModelPrice] = {
    "gpt-4o": ModelPrice(input_per_million_usd=2.50, output_per_million_usd=10.00),
    "gpt-4o-mini": ModelPrice(input_per_million_usd=0.15, output_per_million_usd=0.60),
    "gemini-1.5-pro": ModelPrice(input_per_million_usd=1.25, output_per_million_usd=5.00),
    "gemini-1.5-flash": ModelPrice(input_per_million_usd=0.075, output_per_million_usd=0.30),
    "gemini-2.0-flash": ModelPrice(input_per_million_usd=0.10, output_per_million_usd=0.40),
    "claude-3-5-sonnet": ModelPrice(input_per_million_usd=3.00, output_per_million_usd=15.00),
}


def resolve_model_price(settings: Settings, model_name: str | None) -> ModelPrice | None:
    """Resolve ModelPrice from settings (explicit table, primary/comp fallbacks, or defaults)."""
    if not model_name:
        model_name = settings.model_name

    if model_name in settings.model_prices:
        return settings.model_prices[model_name]

    if model_name == settings.model_name and (
        settings.primary_input_cost_per_million_usd is not None
        and settings.primary_output_cost_per_million_usd is not None
    ):
        return ModelPrice(
            input_per_million_usd=settings.primary_input_cost_per_million_usd,
            output_per_million_usd=settings.primary_output_cost_per_million_usd,
        )

    if model_name == settings.comparison_model and (
        settings.comparison_input_cost_per_million_usd is not None
        and settings.comparison_output_cost_per_million_usd is not None
    ):
        return ModelPrice(
            input_per_million_usd=settings.comparison_input_cost_per_million_usd,
            output_per_million_usd=settings.comparison_output_cost_per_million_usd,
        )

    if model_name in DEFAULT_MODEL_PRICES:
        return DEFAULT_MODEL_PRICES[model_name]
    for key, val in DEFAULT_MODEL_PRICES.items():
        if model_name and model_name.startswith(key):
            return val
    return None


def estimate_step_cost(
    step: TraceStep,
    price: ModelPrice | None,
) -> float | None:
    """Estimate USD cost of a trace step if not already recorded."""
    if step.cost_usd is not None:
        return step.cost_usd
    if price is None:
        return None
    if step.input_tokens is None and step.output_tokens is None:
        return None
    in_tokens = step.input_tokens or 0
    out_tokens = step.output_tokens or 0
    return (in_tokens * price.input_per_million_usd / 1_000_000) + (
        out_tokens * price.output_per_million_usd / 1_000_000
    )


def enrich_execution_costs(
    execution: ExecutionResult,
    price: ModelPrice | None,
) -> ExecutionResult:
    """Enrich trace steps with estimated cost if price is available and mark them."""
    if price is None:
        return execution
    updated_steps: list[TraceStep] = []
    for step in execution.steps:
        if step.kind in {"llm", "agent_summary"} and step.cost_usd is None:
            cost = estimate_step_cost(step, price)
            if cost is not None:
                step = step.model_copy(update={"cost_usd": cost})
        updated_steps.append(step)
    return execution.model_copy(update={"steps": updated_steps})
