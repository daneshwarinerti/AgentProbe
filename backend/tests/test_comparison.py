"""Unit tests for paired model evaluation and metric aggregation."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Sequence

import httpx
import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool
from openai import APIStatusError
from pydantic import SecretStr

from backend.app import comparison
from backend.app.comparison import (
    GEMINI_OPENAI_BASE_URL,
    ModelComparisonResult,
    _GeminiChatOpenAI,
    _ProviderCallPacer,
    _RateLimitRetryChatModel,
    compare_models,
)
from backend.app.core.config import Settings
from backend.app.runner.models import AgentResult, TraceStep
from backend.suite.schema import TestCase as SuiteCase


class FakeModelAgent:
    def __init__(self, settings: Settings) -> None:
        self.model_name = settings.model_name
        self.model_factory = lambda _temperature, _seed: FakeRateLimitedModel()

    async def run(
        self,
        _case_input: str,
        *,
        temperature: float = 0.0,
        seed: int | None = None,
        max_steps: int = 5,
    ) -> AgentResult:
        answer = "expected response" if self.model_name == "primary-model" else "different answer"
        return AgentResult(
            final_answer=answer,
            steps=[
                TraceStep(
                    kind="llm",
                    input_tokens=10,
                    output_tokens=5,
                    latency_ms=12.0,
                )
            ],
        )


class FakeRateLimitedModel:
    def __init__(self) -> None:
        self.attempts = 0

    def bind_tools(self, _tools: Sequence[BaseTool]) -> FakeRateLimitedModel:
        return self

    async def ainvoke(self, _messages: list[BaseMessage]) -> AIMessage:
        self.attempts += 1
        if self.attempts == 1:
            response = httpx.Response(
                429,
                request=httpx.Request("POST", "https://provider.invalid"),
                headers={"retry-after": "0.1"},
            )
            raise APIStatusError("rate limited", response=response, body=None)
        return AIMessage(content="recovered")


class TwiceRateLimitedModel(FakeRateLimitedModel):
    async def ainvoke(self, _messages: list[BaseMessage]) -> AIMessage:
        self.attempts += 1
        if self.attempts < 3:
            response = httpx.Response(
                429,
                request=httpx.Request("POST", "https://provider.invalid"),
            )
            raise APIStatusError("rate limited", response=response, body=None)
        return AIMessage(content="recovered")


@pytest.mark.asyncio
async def test_comparison_runs_identical_cases_and_computes_priced_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(comparison, "SampleAgent", FakeModelAgent)
    settings = Settings(
        llm_provider="openai",
        model_name="primary-model",
        comparison_model="gemini-model",
        openai_api_key=SecretStr("primary-test-key"),
        gemini_api_key=SecretStr("gemini-test-key"),
        primary_input_cost_per_million_usd=1.0,
        primary_output_cost_per_million_usd=2.0,
        comparison_input_cost_per_million_usd=1.0,
        comparison_output_cost_per_million_usd=2.0,
    )
    cases: Sequence[SuiteCase] = [
        SuiteCase(
            id="expected-response",
            input="Return the expected response.",
            expected_answer_contains=["expected"],
        )
    ]

    result = await compare_models(cases=cases, suite="test", repeats=1, settings=settings)

    assert isinstance(result, ModelComparisonResult)
    assert result.primary.cases == result.comparison.cases == 1
    assert result.primary.accuracy == 1.0
    assert result.comparison.accuracy == 0.0
    assert result.primary.total_tokens == result.comparison.total_tokens == 15
    assert result.primary.cost_per_100_cases_usd == pytest.approx(0.002)
    assert result.comparison.cost_per_100_cases_usd == pytest.approx(0.002)
    assert result.primary.cost_is_estimate
    assert result.comparison.cost_is_estimate
    assert result.primary_cases[0].case_id == result.comparison_cases[0].case_id
    assert result.primary_cases[0].passed
    assert not result.comparison_cases[0].passed


@pytest.mark.asyncio
async def test_comparison_retries_rate_limit_using_provider_delay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delays: list[float] = []

    async def record_delay(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", record_delay)
    model = FakeRateLimitedModel()

    response = await _RateLimitRetryChatModel(model).ainvoke([])

    assert response.content == "recovered"
    assert model.attempts == 2
    assert delays == [1.0]


@pytest.mark.asyncio
async def test_comparison_retry_backoff_is_exponential(monkeypatch: pytest.MonkeyPatch) -> None:
    delays: list[float] = []

    async def record_delay(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(asyncio, "sleep", record_delay)

    response = await _RateLimitRetryChatModel(TwiceRateLimitedModel()).ainvoke([])

    assert response.content == "recovered"
    assert delays == [1.0, 2.0]


def test_gemini_preserves_thought_signature_in_followup_tool_request() -> None:
    model = _GeminiChatOpenAI(
        model="gemini-test",
        api_key=SecretStr("not-a-real-key"),
        base_url=GEMINI_OPENAI_BASE_URL,
    )
    result = model._create_chat_result(
        {
            "model": "gemini-test",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "call-1",
                                "type": "function",
                                "function": {
                                    "name": "search_faq",
                                    "arguments": '{"query":"returns"}',
                                },
                                "extra_content": {"google": {"thought_signature": "opaque"}},
                            }
                        ],
                    }
                }
            ],
        }
    )
    assistant = result.generations[0].message
    assert isinstance(assistant, AIMessage)
    payload = model._get_request_payload(
        [
            HumanMessage(content="What is the return policy?"),
            assistant,
            ToolMessage(content="30 days", tool_call_id="call-1", name="search_faq"),
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "search_faq",
                    "parameters": {
                        "type": "object",
                        "properties": {"query": {"type": "string"}},
                    },
                },
            }
        ],
    )

    assert payload["messages"][1]["tool_calls"][0]["extra_content"] == {
        "google": {"thought_signature": "opaque"}
    }
    assert payload["messages"][2]["role"] == "tool"


def test_gemini_accepts_null_tool_calls_in_final_response() -> None:
    model = _GeminiChatOpenAI(
        model="gemini-test",
        api_key=SecretStr("not-a-real-key"),
        base_url=GEMINI_OPENAI_BASE_URL,
    )

    result = model._create_chat_result(
        {
            "model": "gemini-test",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "The password reset link is on the sign-in page.",
                        "tool_calls": None,
                    }
                }
            ],
        }
    )

    assert result.generations[0].message.content == (
        "The password reset link is on the sign-in page."
    )


@pytest.mark.asyncio
async def test_provider_pacer_enforces_configured_minimum_call_spacing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = [10.0]
    waits: list[float] = []

    def monotonic() -> float:
        return now[0]

    async def advance_clock(delay: float) -> None:
        waits.append(delay)
        now[0] += delay

    monkeypatch.setattr(time, "monotonic", monotonic)
    monkeypatch.setattr(asyncio, "sleep", advance_clock)
    pacer = _ProviderCallPacer(0.5)

    await pacer.wait()
    await pacer.wait()

    assert waits == [0.5]


def test_comparison_cost_is_unknown_without_configured_model_rates() -> None:
    from backend.app.comparison import ComparisonCase, _metrics
    from backend.app.runner.models import ExecutionResult

    item = ComparisonCase(
        case_id="one",
        repeat_index=0,
        execution=ExecutionResult(
            case_id="one",
            repeat_index=0,
            success=True,
            output="ok",
            attempts=1,
            duration_ms=20.0,
            steps=[TraceStep(kind="llm", input_tokens=3, output_tokens=4, latency_ms=15.0)],
        ),
        passed=True,
        failure_types=[],
    )
    metrics = _metrics("test-model", [item], None, None)

    assert metrics.total_tokens == 7
    assert metrics.total_cost_usd is None
    assert metrics.cost_per_100_cases_usd is None


def test_model_price_table_loads_by_model_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "AGENTPROBE_MODEL_PRICES",
        json.dumps(
            {
                "primary-model": {
                    "input_per_million_usd": 0.0,
                    "output_per_million_usd": 0.0,
                }
            }
        ),
    )

    settings = Settings()

    assert settings.model_prices["primary-model"].input_per_million_usd == 0.0
    assert settings.model_prices["primary-model"].output_per_million_usd == 0.0
