"""Run the bundled sample agent against two configured chat models."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from collections.abc import Sequence
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.messages.utils import convert_to_messages
from langchain_core.outputs import ChatResult
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from openai import APIStatusError
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from backend.agents.sample_agent.agent import ChatModel, SampleAgent
from backend.app.core.config import ModelPrice, Settings
from backend.app.runner.models import ExecutionResult
from backend.app.runner.run import EvaluationRunner, RunnerOptions
from backend.app.scorers.llm_judge import rate_limit_delay, score_case
from backend.app.scorers.safety import score_safety_case
from backend.suite.schema import TestCase

GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
_RATE_LIMIT_RETRIES = 3
logger = logging.getLogger(__name__)


class ModelMetrics(BaseModel):
    """Measured quality, latency, token, and optionally priced usage for one model."""

    model_config = ConfigDict(extra="forbid")

    model: str
    cases: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    execution_errors: int = Field(default=0, ge=0)
    accuracy: float = Field(ge=0.0, le=1.0)
    average_latency_ms: float = Field(ge=0.0)
    total_tokens: int | None = Field(default=None, ge=0)
    total_cost_usd: float | None = Field(default=None, ge=0.0)
    cost_per_100_cases_usd: float | None = Field(default=None, ge=0.0)
    cost_is_estimate: bool = False
    failure_types: dict[str, int]


class ComparisonCase(BaseModel):
    """One model's scored result for one input and repeat."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    repeat_index: int = Field(ge=0)
    execution: ExecutionResult
    passed: bool
    failure_types: list[str]


class ModelComparisonResult(BaseModel):
    """Persisted side-by-side results for two chat models."""

    model_config = ConfigDict(extra="forbid")

    id: str
    created_at: str
    suite: str
    repeats: int
    primary: ModelMetrics
    comparison: ModelMetrics
    primary_cases: list[ComparisonCase]
    comparison_cases: list[ComparisonCase]


def gemini_settings(settings: Settings) -> Settings:
    """Build model settings for the configured Gemini OpenAI-compatible endpoint."""
    api_key = settings.gemini_api_key.get_secret_value()
    if not api_key:
        raise ValueError("GEMINI_API_KEY is required to compare against Gemini.")
    if not settings.comparison_model:
        raise ValueError("COMPARISON_MODEL is required to compare against Gemini.")
    return Settings(
        llm_provider="openai",
        model_name=settings.comparison_model,
        openai_api_key=SecretStr(api_key),
        openai_base_url=GEMINI_OPENAI_BASE_URL,
        temperature=settings.temperature,
        seed=None,
        case_timeout_seconds=settings.case_timeout_seconds,
        retries=0,
        concurrency=settings.concurrency,
    )


async def compare_models(
    *,
    cases: Sequence[TestCase],
    suite: str,
    repeats: int,
    settings: Settings,
) -> ModelComparisonResult:
    """Run the same test cases sequentially on the configured primary and Gemini models."""
    if settings.llm_provider != "openai":
        raise ValueError("Model comparison requires AGENTPROBE_LLM_PROVIDER=openai.")
    if not settings.openai_api_key.get_secret_value():
        raise ValueError("AGENTPROBE_OPENAI_API_KEY is required for the primary model.")
    if not cases:
        raise ValueError("At least one test case must be selected for comparison.")
    shared_settings = settings.model_copy(
        update={"seed": None, "retries": 0, "demo_bad_prompt": False}
    )
    gemini = gemini_settings(shared_settings)
    primary_price = _model_price(
        settings,
        settings.model_name,
        settings.primary_input_cost_per_million_usd,
        settings.primary_output_cost_per_million_usd,
    )
    comparison_price = _model_price(
        settings,
        settings.comparison_model,
        settings.comparison_input_cost_per_million_usd,
        settings.comparison_output_cost_per_million_usd,
    )
    primary_cases = await _evaluate_model(
        cases=cases,
        repeats=repeats,
        settings=shared_settings,
    )
    comparison_cases = await _evaluate_model(
        cases=cases,
        repeats=repeats,
        settings=gemini,
    )
    return ModelComparisonResult(
        id="",
        created_at="",
        suite=suite,
        repeats=repeats,
        primary=_metrics(
            settings.model_name,
            primary_cases,
            primary_price.input_per_million_usd if primary_price else None,
            primary_price.output_per_million_usd if primary_price else None,
        ),
        comparison=_metrics(
            settings.comparison_model,
            comparison_cases,
            comparison_price.input_per_million_usd if comparison_price else None,
            comparison_price.output_per_million_usd if comparison_price else None,
        ),
        primary_cases=primary_cases,
        comparison_cases=comparison_cases,
    )


async def _evaluate_model(
    *,
    cases: Sequence[TestCase],
    repeats: int,
    settings: Settings,
) -> list[ComparisonCase]:
    agent = SampleAgent(settings)
    original_factory = agent.model_factory
    pacer = _ProviderCallPacer(settings.provider_call_delay_seconds)

    def create_model(temperature: float, seed: int | None) -> ChatModel:
        if settings.openai_base_url == GEMINI_OPENAI_BASE_URL:
            api_key = settings.openai_api_key.get_secret_value()
            if not api_key:
                raise ValueError("An API key is required for the configured Gemini model.")
            model: ChatModel = _GeminiChatOpenAI(
                model=settings.model_name,
                api_key=SecretStr(api_key),
                base_url=GEMINI_OPENAI_BASE_URL,
                temperature=temperature,
                seed=seed,
                request_timeout=settings.case_timeout_seconds,
                max_retries=0,
            )  # type: ignore[assignment]
        else:
            model = original_factory(temperature, seed)
        return _RateLimitRetryChatModel(
            model,
            pacer=pacer,
            retry_base_seconds=settings.rate_limit_retry_base_seconds,
        )

    agent.model_factory = create_model
    executions = await EvaluationRunner(
        agent,
        RunnerOptions(
            repeats=repeats,
            temperature=settings.temperature,
            seed=settings.seed,
            timeout_seconds=settings.case_timeout_seconds,
            retries=settings.retries,
            concurrency=1,
        ),
    ).run_cases(cases)
    case_by_id = {case.id: case for case in cases}
    evaluated: list[ComparisonCase] = []
    for execution in executions:
        case = case_by_id[execution.case_id]
        score = await score_case(
            case,
            final_answer=execution.output,
            steps=execution.steps,
            execution_error=execution.error,
        )
        safety = score_safety_case(case, execution)
        failures = sorted(
            set(score.failure_categories) | ({"unsafe_output"} if not safety.passed else set())
        )
        evaluated.append(
            ComparisonCase(
                case_id=execution.case_id,
                repeat_index=execution.repeat_index,
                execution=execution,
                passed=score.passed and safety.passed,
                failure_types=failures,
            )
        )
    return evaluated


class _RateLimitRetryChatModel:
    def __init__(
        self,
        model: ChatModel,
        *,
        pacer: _ProviderCallPacer | None = None,
        retry_base_seconds: float = 1.0,
    ) -> None:
        self.model = model
        self.pacer = pacer or _ProviderCallPacer(0.0)
        self.retry_base_seconds = retry_base_seconds

    def bind_tools(self, tools: Sequence[BaseTool]) -> _RateLimitRetryChatModel:
        self.model = self.model.bind_tools(tools)
        return self

    async def ainvoke(self, messages: list[BaseMessage]) -> AIMessage:
        for attempt in range(_RATE_LIMIT_RETRIES + 1):
            await self.pacer.wait()
            try:
                return await self.model.ainvoke(messages)
            except APIStatusError as exc:
                if exc.status_code != 429 or attempt == _RATE_LIMIT_RETRIES:
                    raise
                delay = rate_limit_delay(
                    exc,
                    attempt,
                    base_delay_seconds=self.retry_base_seconds,
                )
                logger.warning(
                    "Model comparison rate limited; retrying after provider delay.",
                    extra={"retry_attempt": attempt + 1, "retry_delay_seconds": delay},
                )
                await asyncio.sleep(delay)
        raise AssertionError("unreachable")


class _ProviderCallPacer:
    def __init__(self, delay_seconds: float) -> None:
        self.delay_seconds = delay_seconds
        self.last_call_at: float | None = None
        self.lock = asyncio.Lock()

    async def wait(self) -> None:
        async with self.lock:
            if self.last_call_at is not None:
                remaining = self.delay_seconds - (time.monotonic() - self.last_call_at)
                if remaining > 0:
                    await asyncio.sleep(remaining)
            self.last_call_at = time.monotonic()


class _GeminiChatOpenAI(ChatOpenAI):
    """ChatOpenAI bridge that preserves Gemini function-call thought signatures."""

    def _create_chat_result(
        self,
        response: Any,
        generation_info: dict[str, Any] | None = None,
    ) -> ChatResult:
        response_dict = response if isinstance(response, dict) else response.model_dump()
        result = super()._create_chat_result(response, generation_info)
        for choice, generation in zip(
            response_dict.get("choices", []),
            result.generations,
            strict=False,
        ):
            raw_message = choice.get("message", {})
            if not isinstance(raw_message, dict) or not isinstance(generation.message, AIMessage):
                continue
            extra_content = raw_message.get("extra_content")
            if extra_content is not None:
                generation.message.additional_kwargs["extra_content"] = extra_content
            call_metadata = {
                str(call["id"]): call["extra_content"]
                for call in (raw_message.get("tool_calls") or [])
                if isinstance(call, dict)
                and call.get("id") is not None
                and call.get("extra_content") is not None
            }
            if call_metadata:
                generation.message.additional_kwargs["tool_call_extra_content"] = call_metadata
        return result

    def _get_request_payload(
        self,
        input_: Any,
        *,
        stop: list[str] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        messages = convert_to_messages(input_)
        for original, outgoing in zip(messages, payload["messages"], strict=False):
            if not isinstance(original, AIMessage):
                continue
            extra_content = original.additional_kwargs.get("extra_content")
            if extra_content is not None:
                outgoing["extra_content"] = extra_content
            call_metadata = original.additional_kwargs.get("tool_call_extra_content", {})
            for call in outgoing.get("tool_calls", []):
                call_id = call.get("id")
                if call_id in call_metadata:
                    call["extra_content"] = call_metadata[call_id]
        return payload


def _model_price(
    settings: Settings,
    model: str,
    fallback_input: float | None,
    fallback_output: float | None,
) -> ModelPrice | None:
    configured = settings.model_prices.get(model)
    if configured is not None:
        return configured
    if fallback_input is None or fallback_output is None:
        return None
    return ModelPrice(
        input_per_million_usd=fallback_input,
        output_per_million_usd=fallback_output,
    )


def _metrics(
    model: str,
    results: list[ComparisonCase],
    input_price: float | None,
    output_price: float | None,
) -> ModelMetrics:
    total = len(results)
    passed = sum(result.passed for result in results)
    steps = [step for result in results for step in result.execution.steps if step.kind == "llm"]
    tokens = (
        sum((step.input_tokens or 0) + (step.output_tokens or 0) for step in steps)
        if steps
        and all(step.input_tokens is not None and step.output_tokens is not None for step in steps)
        else None
    )
    total_cost: float | None = None
    if (
        steps
        and input_price is not None
        and output_price is not None
        and all(step.input_tokens is not None and step.output_tokens is not None for step in steps)
    ):
        total_cost = sum(
            (step.input_tokens or 0) * input_price / 1_000_000
            + (step.output_tokens or 0) * output_price / 1_000_000
            for step in steps
        )
    failures: Counter[str] = Counter(
        failure for result in results for failure in result.failure_types
    )
    return ModelMetrics(
        model=model,
        cases=total,
        passed=passed,
        failed=total - passed,
        execution_errors=sum(result.execution.error is not None for result in results),
        accuracy=passed / total if total else 0.0,
        average_latency_ms=(
            sum(result.execution.duration_ms for result in results) / total if total else 0.0
        ),
        total_tokens=tokens,
        total_cost_usd=total_cost,
        cost_per_100_cases_usd=(
            total_cost / total * 100 if total_cost is not None and total else None
        ),
        cost_is_estimate=total_cost is not None,
        failure_types=dict(sorted(failures.items())),
    )


def comparison_dump(result: ModelComparisonResult) -> dict[str, Any]:
    """Return a JSON-compatible representation for database persistence."""
    return result.model_dump(mode="json")
