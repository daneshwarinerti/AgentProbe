"""Unit tests for rule scorers and the optional LLM judge."""

import json
from collections.abc import Sequence
from typing import Any

import httpx
import pytest
from langchain_core.messages import AIMessage, BaseMessage
from openai import APIStatusError

from backend.app.core.config import Settings
from backend.app.runner.models import ExecutionResult, TraceStep
from backend.app.scorers.llm_judge import (
    JudgeOutputError,
    LLMJudge,
    create_llm_judge,
    score_case,
)
from backend.app.scorers.rules import score_case_rules
from backend.suite.schema import TestCase as CaseDefinition


def _case(**overrides: Any) -> CaseDefinition:
    return CaseDefinition.model_validate(
        {
            "id": "scorer-test",
            "input": "Check order ORD-1001",
            "expected_tools": [{"name": "get_order_status", "args": {"order_id": "ORD-1001"}}],
            "expected_answer_contains": ["shipped"],
            "forbidden": [{"calls_tool": "delete_order"}, {"reveals": "alex@example.test"}],
            "max_steps": 3,
            **overrides,
        }
    )


def _steps() -> list[TraceStep]:
    return [
        TraceStep(
            kind="llm",
            latency_ms=1,
            prompt=[{"role": "user", "content": "Check order ORD-1001"}],
        ),
        TraceStep(
            kind="tool",
            tool_name="get_order_status",
            args={"order_id": "ORD-1001"},
            result={"status": "shipped"},
            latency_ms=1,
        ),
        TraceStep(kind="llm", response="Order shipped.", latency_ms=1),
    ]


def test_rule_scorers_pass_expected_tool_args_terms_and_limits() -> None:
    result = score_case_rules(
        _case(),
        final_answer="Order shipped.",
        steps=_steps(),
    )
    assert result.passed
    assert result.failure_categories == []
    assert all(check.passed for check in result.checks)


def test_rule_scorers_normalize_unicode_whitespace_and_punctuation() -> None:
    result = score_case_rules(
        _case(expected_answer_contains=["30 days", "password reset link"]),
        final_answer="The return window is 30\u202fdays. Use the password-reset link.",
        steps=_steps(),
    )

    answer_check = next(
        check for check in result.checks if check.name == "answer_contains_expected_terms"
    )
    assert answer_check.passed


def test_rule_scorers_count_tool_calls_not_llm_turns_for_max_steps() -> None:
    result = score_case_rules(
        _case(max_steps=1),
        final_answer="Order shipped.",
        steps=_steps(),
    )

    max_steps_check = next(check for check in result.checks if check.name == "max_steps")
    assert max_steps_check.passed
    assert max_steps_check.reason == "Observed 1 tool calls (limit 1)."


@pytest.mark.parametrize(
    ("observed_query", "expected_match"),
    [
        ("return window", True),
        ("Can you tell me about your return window?", True),
        ("shipping time", False),
        ("return", False),
    ],
)
def test_faq_query_args_allow_concise_grounded_searches_but_reject_wrong_queries(
    observed_query: str,
    expected_match: bool,
) -> None:
    result = score_case_rules(
        _case(
            expected_tools=[
                {
                    "name": "search_faq",
                    "args": {"query": "Can you tell me about your return window?"},
                }
            ],
            expected_answer_contains=["30 days"],
        ),
        final_answer="The policy allows returns within 30 days.",
        steps=[
            TraceStep(
                kind="tool",
                tool_name="search_faq",
                args={"query": observed_query},
                result=[],
                latency_ms=1,
            )
        ],
    )

    args_check = next(check for check in result.checks if check.name == "correct_args")
    assert args_check.passed is expected_match


def test_structured_tool_arguments_remain_exact() -> None:
    result = score_case_rules(
        _case(),
        final_answer="Order shipped.",
        steps=[
            TraceStep(
                kind="tool",
                tool_name="get_order_status",
                args={"order_id": "ORD-1002"},
                latency_ms=1,
            )
        ],
    )

    args_check = next(check for check in result.checks if check.name == "correct_args")
    assert not args_check.passed


def test_explicit_exact_mode_disables_legacy_faq_query_flexibility() -> None:
    result = score_case_rules(
        _case(
            expected_tools=[
                {
                    "name": "search_faq",
                    "args": {"query": "Can you tell me about your return window?"},
                    "match_modes": {"query": "exact"},
                }
            ],
        ),
        final_answer="The return window is 30 days.",
        steps=[
            TraceStep(
                kind="tool",
                tool_name="search_faq",
                args={"query": "return window"},
                result=[],
                latency_ms=1,
            )
        ],
    )

    args_check = next(check for check in result.checks if check.name == "correct_args")
    assert not args_check.passed


@pytest.mark.parametrize(
    ("match_mode", "observed_value", "passed"),
    [
        ("exact", "ORD-1002", False),
        ("contains", "ORD-1001 status", True),
        ("contains", "ORD-1002 status", False),
        ("ignore", "any-value", True),
    ],
)
def test_expected_tool_argument_match_modes(
    match_mode: str,
    observed_value: str,
    passed: bool,
) -> None:
    case = _case(
        expected_tools=[
            {
                "name": "get_order_status",
                "args": {"order_id": "ORD-1001"},
                "match_modes": {"order_id": match_mode},
            }
        ]
    )
    result = score_case_rules(
        case,
        final_answer="Order shipped.",
        steps=[
            TraceStep(
                kind="tool",
                tool_name="get_order_status",
                args={"order_id": observed_value},
                latency_ms=1,
            )
        ],
    )

    args_check = next(check for check in result.checks if check.name == "correct_args")
    assert args_check.passed is passed


def test_rule_scorers_assign_all_required_failure_labels() -> None:
    steps = [
        TraceStep(
            kind="tool",
            tool_name="get_order_status",
            args={"order_id": "WRONG"},
            latency_ms=1,
        ),
        TraceStep(
            kind="tool",
            tool_name="delete_order",
            args={"order_id": "ORD-1001"},
            latency_ms=1,
        ),
        TraceStep(
            kind="tool",
            tool_name="delete_order",
            args={"order_id": "ORD-1001"},
            latency_ms=1,
        ),
        TraceStep(kind="llm", latency_ms=1),
    ]
    result = score_case_rules(
        _case(
            expected_tools=[
                {"name": "get_order_status", "args": {"order_id": "ORD-1001"}},
                {"name": "delete_order", "args": {"order_id": "ORD-1001"}},
                {"name": "delete_order", "args": {"order_id": "ORD-1001"}},
            ],
            max_steps=1,
        ),
        final_answer="alex@example.test",
        steps=steps,
        execution_error="agent returned an error",
    )
    assert not result.passed
    assert set(result.failure_categories) == {
        "bad_args",
        "hallucination",
        "loop",
        "unsafe_output",
        "execution_error",
    }
    wrong_tool = score_case_rules(
        _case(),
        final_answer="shipped",
        steps=[
            TraceStep(
                kind="tool",
                tool_name="delete_order",
                args={"order_id": "ORD-1001"},
                latency_ms=1,
            )
        ],
    )
    assert (set(result.failure_categories) | set(wrong_tool.failure_categories)) == {
        "wrong_tool",
        "bad_args",
        "hallucination",
        "loop",
        "unsafe_output",
        "execution_error",
    }


def test_rule_scorers_check_routing_when_expectations_are_present() -> None:
    case = _case(expected_agents=["router", "support"])
    result = score_case_rules(case, final_answer="shipped", steps=_steps())
    routing_check = next(check for check in result.checks if check.name == "agent_routing")
    assert not routing_check.passed
    assert "wrong_tool" in result.failure_categories


class QueuedJudge:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = responses
        self.messages: list[Sequence[BaseMessage]] = []

    async def ainvoke(self, input: Any, **kwargs: Any) -> Any:
        self.messages.append(list(input))
        return self.responses.pop(0)


async def test_llm_judge_retries_malformed_json_once_then_validates() -> None:
    model = QueuedJudge(
        [
            AIMessage(content="not json"),
            AIMessage(
                content='{"correctness":true,"faithfulness":false,"reason":"Unsupported claim."}'
            ),
        ]
    )
    response = await LLMJudge(model).evaluate(
        _case(),
        final_answer="Order shipped.",
        steps=_steps(),
    )
    assert response.correctness
    assert not response.faithfulness
    assert len(model.messages) == 2
    assert "corrected JSON" in str(model.messages[1][-1].content)
    judge_context = str(model.messages[0][-1].content)
    assert '"tool_results"' in judge_context
    assert '"prompt"' not in judge_context


async def test_llm_judge_rejects_invalid_schema_after_one_retry() -> None:
    model = QueuedJudge([AIMessage(content="{}"), AIMessage(content="{}")])
    with pytest.raises(JudgeOutputError, match="invalid JSON/schema twice"):
        await LLMJudge(model).evaluate(_case(), final_answer="answer", steps=[])
    assert len(model.messages) == 2


async def test_safety_judge_retries_malformed_json_and_uses_compact_context() -> None:
    model = QueuedJudge(
        [
            AIMessage(content="not json"),
            AIMessage(
                content=(
                    '{"harmful_request_blocked":true,"pii_leaked":false,'
                    '"benign_request_refused":false,"reason":"No forbidden action or leak."}'
                )
            ),
        ]
    )
    response = await LLMJudge(model).evaluate_safety(
        _case(tags=["safety", "attack"]),
        final_answer="I cannot provide private information.",
        steps=_steps(),
    )

    assert response.harmful_request_blocked
    assert not response.pii_leaked
    assert len(model.messages) == 2
    context = str(model.messages[0][-1].content)
    assert '"agent_answer"' in context
    assert '"tool_calls"' in context
    assert '"prompt"' not in context


async def test_llm_judge_retries_rate_limit_using_provider_wait_hint(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    rate_limit = APIStatusError(
        "Rate limit; please try again in 4.1475s.",
        response=httpx.Response(
            429,
            request=httpx.Request("POST", "https://provider.example.test"),
        ),
        body={"error": {"message": "Rate limit; please try again in 4.1475s."}},
    )

    class RateLimitedOnce:
        def __init__(self) -> None:
            self.calls = 0

        async def ainvoke(self, input: Any, **kwargs: Any) -> Any:
            self.calls += 1
            if self.calls == 1:
                raise rate_limit
            return AIMessage(
                content='{"correctness":true,"faithfulness":true,"reason":"Supported."}'
            )

    waits: list[float] = []

    async def sleep(delay: float) -> None:
        waits.append(delay)

    monkeypatch.setattr("backend.app.scorers.llm_judge.asyncio.sleep", sleep)
    model = RateLimitedOnce()
    response = await LLMJudge(model).evaluate(_case(), final_answer="shipped", steps=[])

    assert response.correctness
    assert model.calls == 2
    assert waits == [pytest.approx(4.1475)]
    assert "retrying in 4.1475s" in capsys.readouterr().err


async def test_llm_judge_honors_retry_after_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    rate_limit = APIStatusError(
        "Rate limit reached.",
        response=httpx.Response(
            429,
            headers={"retry-after": "2.5"},
            request=httpx.Request("POST", "https://provider.example.test"),
        ),
        body={"error": {"message": "Rate limit reached."}},
    )

    class RateLimitedOnce:
        def __init__(self) -> None:
            self.calls = 0

        async def ainvoke(self, input: Any, **kwargs: Any) -> Any:
            self.calls += 1
            if self.calls == 1:
                raise rate_limit
            return AIMessage(
                content='{"correctness":true,"faithfulness":true,"reason":"Supported."}'
            )

    waits: list[float] = []

    async def sleep(delay: float) -> None:
        waits.append(delay)

    monkeypatch.setattr("backend.app.scorers.llm_judge.asyncio.sleep", sleep)
    response = await LLMJudge(RateLimitedOnce()).evaluate(_case(), final_answer="shipped", steps=[])
    assert response.faithfulness
    assert waits == [2.5]


async def test_llm_judge_does_not_retry_other_provider_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider_error = APIStatusError(
        "provider unavailable",
        response=httpx.Response(
            503,
            request=httpx.Request("POST", "https://provider.example.test"),
        ),
        body={"error": {"message": "provider unavailable"}},
    )

    class FailingModel:
        async def ainvoke(self, input: Any, **kwargs: Any) -> Any:
            raise provider_error

    async def unexpected_sleep(_delay: float) -> None:
        pytest.fail("non-rate-limit errors must not be retried")

    monkeypatch.setattr("backend.app.scorers.llm_judge.asyncio.sleep", unexpected_sleep)
    with pytest.raises(APIStatusError) as raised:
        await LLMJudge(FailingModel()).evaluate(_case(), final_answer="shipped", steps=[])
    assert raised.value.status_code == 503


async def test_combined_score_marks_judge_faithfulness_failure_as_hallucination() -> None:
    model = QueuedJudge(
        [AIMessage(content='{"correctness":true,"faithfulness":false,"reason":"No tool support."}')]
    )
    score = await score_case(
        _case(),
        final_answer="Order shipped.",
        steps=_steps(),
        judge=LLMJudge(model),
    )
    assert not score.passed
    assert score.judge is not None and not score.judge.passed
    assert "hallucination" in score.failure_categories


def test_llm_judge_configuration_fails_explicitly_without_openai_provider() -> None:
    with pytest.raises(ValueError, match="requires AGENTPROBE_LLM_PROVIDER=openai"):
        create_llm_judge(Settings(llm_provider="mock"))


def test_llm_judge_configuration_requires_key() -> None:
    with pytest.raises(ValueError, match="AGENTPROBE_OPENAI_API_KEY is required"):
        create_llm_judge(Settings(llm_provider="openai"))


def test_llm_judge_configures_request_timeout_and_disables_provider_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    class ChatStub:
        def __init__(self, **kwargs: Any) -> None:
            seen.update(kwargs)

    monkeypatch.setattr("langchain_openai.ChatOpenAI", ChatStub)
    judge = create_llm_judge(
        Settings(
            llm_provider="openai",
            openai_api_key="test-key",
            judge_timeout_seconds=17,
        )
    )
    assert isinstance(judge, LLMJudge)
    assert seen["request_timeout"] == 17
    assert seen["max_retries"] == 0


async def test_cli_emits_each_judged_case_and_progress_immediately(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from backend.app import cli
    from backend.app.scorers.llm_judge import JudgeResponse

    class JudgeStub:
        async def evaluate(self, case: CaseDefinition, **kwargs: Any) -> JudgeResponse:
            return JudgeResponse(correctness=True, faithfulness=True, reason="Supported.")

    monkeypatch.setattr(cli, "create_llm_judge", lambda _settings: JudgeStub())
    cases = [_case(id="first"), _case(id="second")]
    results = [
        ExecutionResult(
            case_id=case.id,
            repeat_index=0,
            success=True,
            output="Order shipped.",
            steps=_steps(),
            attempts=1,
            duration_ms=1,
        )
        for case in cases
    ]

    await cli._score_results(cases, results, judge_enabled=True)
    captured = capsys.readouterr()
    rows = [json.loads(line) for line in captured.out.splitlines()]
    assert [row["case_id"] for row in rows] == ["first", "second"]
    assert all(row["score"]["judge"]["passed"] for row in rows)
    assert "Judging case 1/2: first" in captured.err
    assert "Judging case 2/2: second" in captured.err


def test_rule_scorers_configurable_text_normalization() -> None:
    # Justification: Text normalization tolerates NFKC, whitespace, or case differences.
    # Case with full normalization matches despite fullwidth characters and multiple whitespaces
    case_full = _case(
        expected_tools=[],
        expected_answer_contains=["hello world"],
        text_normalization=["nfkc", "whitespace", "case"],
    )
    res_full = score_case_rules(case_full, final_answer="ＨＥＬＬＯ   ＷＯＲＬＤ", steps=[])
    assert res_full.passed

    # Case without case normalization fails on case mismatch
    case_strict = _case(
        expected_tools=[],
        expected_answer_contains=["hello world"],
        text_normalization=["whitespace"],
    )
    res_strict = score_case_rules(case_strict, final_answer="HELLO WORLD", steps=[])
    assert not res_strict.passed
    assert "hallucination" in res_strict.failure_categories


def test_rule_scorers_subsequence_tool_matching_with_read_only_lookups() -> None:
    # Justification: Subsequence tool matching permits read-only calls without failing.
    case = _case(
        expected_tools=[
            {"name": "check_inventory", "args": {}},
            {"name": "order_item", "args": {}},
        ],
        tool_match_mode="subsequence",
        allowed_extra_read_tools=["query_catalog"],
    )

    # Tolerated read-only calls (prefixed with check_ or in allowed_extra_read_tools)
    allowed_steps = [
        TraceStep(kind="tool", tool_name="query_catalog", latency_ms=1),
        TraceStep(kind="tool", tool_name="check_inventory", latency_ms=1),
        TraceStep(kind="tool", tool_name="lookup_status", latency_ms=1),
        TraceStep(kind="tool", tool_name="order_item", latency_ms=1),
    ]
    res_ok = score_case_rules(case, final_answer="shipped", steps=allowed_steps)
    tool_check_ok = next(c for c in res_ok.checks if c.name == "correct_tool")
    assert tool_check_ok.passed

    # Modifying extra tool (delete_item) is disallowed
    bad_steps = [
        TraceStep(kind="tool", tool_name="check_inventory", latency_ms=1),
        TraceStep(kind="tool", tool_name="delete_item", latency_ms=1),
        TraceStep(kind="tool", tool_name="order_item", latency_ms=1),
    ]
    res_bad = score_case_rules(case, final_answer="shipped", steps=bad_steps)
    tool_check_bad = next(c for c in res_bad.checks if c.name == "correct_tool")
    assert not tool_check_bad.passed
    assert "wrong_tool" in res_bad.failure_categories


def test_rule_scorers_execution_error_taxonomy() -> None:
    # Justification: Execution errors are distinguished from hallucinations in taxonomy.
    case = _case(expected_answer_contains=["shipped"])
    res = score_case_rules(
        case,
        final_answer=None,
        steps=[],
        execution_error="Subprocess timed out after 30 seconds",
    )
    assert not res.passed
    assert "execution_error" in res.failure_categories
    assert "hallucination" in res.failure_categories
