"""Tests for concurrency, retries, repeat runs, timeouts, and crash handling."""

import asyncio
from collections.abc import Callable

from backend.app.runner.models import AgentResult, ExecutionResult, TraceStep
from backend.app.runner.run import EvaluationRunner, RunnerOptions
from backend.suite.schema import TestCase as CaseDefinition


class FakeAgent:
    def __init__(self, behavior: Callable[..., AgentResult | Exception] | None = None) -> None:
        self.behavior = behavior or (lambda *_args, **_kwargs: AgentResult(output="done"))
        self.calls = 0

    async def run(
        self,
        case_input: str,
        *,
        temperature: float = 0.0,
        seed: int | None = None,
        max_steps: int = 5,
    ) -> AgentResult:
        self.calls += 1
        result = self.behavior(case_input, temperature, seed, max_steps)
        if isinstance(result, Exception):
            raise result
        return result


def case(case_id: str = "case", user_input: str = "hello") -> CaseDefinition:
    return CaseDefinition(id=case_id, input=user_input, max_steps=4)


async def test_runner_repeats_cases_and_forwards_configuration() -> None:
    received: list[tuple[str, float, int | None, int]] = []

    class RecordingAgent(FakeAgent):
        async def run(
            self,
            case_input: str,
            *,
            temperature: float = 0.0,
            seed: int | None = None,
            max_steps: int = 5,
        ) -> AgentResult:
            received.append((case_input, temperature, seed, max_steps))
            return await super().run(
                case_input, temperature=temperature, seed=seed, max_steps=max_steps
            )

    agent = RecordingAgent()
    runner = EvaluationRunner(
        agent,
        RunnerOptions(repeats=2, temperature=0.3, seed=9, retries=0),
    )
    results = await runner.run_cases([case()])
    assert [result.repeat_index for result in results] == [0, 1]
    assert all(result.success for result in results)
    assert received == [("hello", 0.3, 9, 4), ("hello", 0.3, 9, 4)]


async def test_runner_retries_agent_crash_and_returns_success() -> None:
    class RecoveringAgent(FakeAgent):
        async def run(
            self,
            case_input: str,
            *,
            temperature: float = 0.0,
            seed: int | None = None,
            max_steps: int = 5,
        ) -> AgentResult:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("temporary")
            return AgentResult(output="recovered")

    recovering_agent = RecoveringAgent()
    results = await EvaluationRunner(
        recovering_agent,
        RunnerOptions(retries=1, retry_backoff_seconds=0),
    ).run_cases([case()])
    assert results[0].success
    assert results[0].attempts == 2
    assert results[0].output == "recovered"


async def test_runner_records_final_agent_failure_without_stopping_suite() -> None:
    agent = FakeAgent(
        lambda user_input, *_args, **_kwargs: (
            ValueError("bad input") if user_input == "broken" else AgentResult(output="good")
        )
    )
    results = await EvaluationRunner(
        agent,
        RunnerOptions(retries=1, retry_backoff_seconds=0),
    ).run_cases([case("broken", "broken"), case("good", "good")])
    assert not results[0].success
    assert results[0].error == "ValueError: bad input"
    assert results[0].attempts == 2
    assert results[0].steps[0].result == results[0].error
    assert results[1].success
    assert results[1].output == "good"


async def test_runner_times_out_and_retries() -> None:
    class SlowAgent(FakeAgent):
        async def run(
            self,
            case_input: str,
            *,
            temperature: float = 0.0,
            seed: int | None = None,
            max_steps: int = 5,
        ) -> AgentResult:
            self.calls += 1
            await asyncio.sleep(0.05)
            return AgentResult(output="late")

    agent = SlowAgent()
    results = await EvaluationRunner(
        agent,
        RunnerOptions(timeout_seconds=0.005, retries=1, retry_backoff_seconds=0),
    ).run_cases([case()])
    assert not results[0].success
    assert results[0].error is not None
    assert results[0].error.startswith("TimeoutError")
    assert agent.calls == 2


async def test_runner_bounds_concurrency() -> None:
    active = 0
    maximum_active = 0

    class SlowAgent(FakeAgent):
        async def run(
            self,
            case_input: str,
            *,
            temperature: float = 0.0,
            seed: int | None = None,
            max_steps: int = 5,
        ) -> AgentResult:
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return AgentResult(
                output="done",
                steps=[TraceStep(kind="tool", tool_name="demo", latency_ms=1.0)],
            )

    results = await EvaluationRunner(
        SlowAgent(), RunnerOptions(concurrency=2, retries=0)
    ).run_cases([case(str(index)) for index in range(5)])
    assert all(result.success for result in results)
    assert maximum_active == 2


async def test_runner_call_spacing() -> None:
    calls: list[float] = []

    class PacedAgent(FakeAgent):
        async def run(
            self,
            case_input: str,
            *,
            temperature: float = 0.0,
            seed: int | None = None,
            max_steps: int = 5,
        ) -> AgentResult:
            calls.append(asyncio.get_event_loop().time())
            return AgentResult(output="done")

    runner = EvaluationRunner(
        PacedAgent(),
        RunnerOptions(call_spacing_seconds=0.03, concurrency=1, retries=0),
    )
    results = await runner.run_cases([case("1"), case("2")])
    assert len(results) == 2
    assert len(calls) == 2
    # The second call should have occurred at least ~0.025s after the first
    assert (calls[1] - calls[0]) >= 0.025


async def test_runner_cancellation() -> None:
    cancel_event = asyncio.Event()

    class SleepAgent(FakeAgent):
        async def run(
            self,
            case_input: str,
            *,
            temperature: float = 0.0,
            seed: int | None = None,
            max_steps: int = 5,
        ) -> AgentResult:
            await asyncio.sleep(0.01)
            cancel_event.set()
            return AgentResult(output=case_input)

    runner = EvaluationRunner(
        SleepAgent(),
        RunnerOptions(concurrency=1, retries=0),
        cancel_event=cancel_event,
    )
    results = await runner.run_cases([case("1", "1"), case("2", "2"), case("3", "3")])
    assert len(results) == 3
    # Subsequent cases must be cancelled
    cancelled_results = [r for r in results if not r.success and "Cancelled" in (r.error or "")]
    assert len(cancelled_results) >= 1


async def test_runner_progress_callback() -> None:
    completed: list[tuple[int, int]] = []

    def on_progress(result: ExecutionResult, done: int, total: int) -> None:
        completed.append((done, total))

    runner = EvaluationRunner(
        FakeAgent(),
        RunnerOptions(concurrency=1, retries=0),
        on_case_completed=on_progress,
    )
    results = await runner.run_cases([case("1"), case("2")])
    assert len(results) == 2
    assert completed == [(1, 2), (2, 2)]


async def test_runner_pure_latency_excludes_pacing() -> None:
    class FastAgent(FakeAgent):
        async def run(
            self,
            case_input: str,
            *,
            temperature: float = 0.0,
            seed: int | None = None,
            max_steps: int = 5,
        ) -> AgentResult:
            await asyncio.sleep(0.01)  # 10ms execution
            return AgentResult(output="done")

    runner = EvaluationRunner(
        FastAgent(),
        RunnerOptions(call_spacing_seconds=0.2, concurrency=1, retries=0),
    )
    results = await runner.run_cases([case("1"), case("2")])
    assert len(results) == 2
    # Each duration_ms should be close to 10ms, definitely not including the 200ms call_spacing
    for r in results:
        assert r.duration_ms < 100.0
