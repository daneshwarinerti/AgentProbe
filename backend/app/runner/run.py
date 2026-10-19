"""Concurrent runner with timeouts, retries, call pacing, progress, and cancellation."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine, Sequence
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from backend.app.runner.models import AgentResult, ExecutionResult, TraceStep
from backend.suite.schema import TestCase

logger = logging.getLogger(__name__)


class Agent(Protocol):
    """Interface implemented by agents that can execute a structured case."""

    async def run(
        self,
        case_input: str,
        *,
        temperature: float = 0.0,
        seed: int | None = None,
        max_steps: int = 5,
    ) -> AgentResult: ...


class RunnerOptions(BaseModel):
    """Validated controls for a suite execution."""

    model_config = ConfigDict(extra="forbid")

    repeats: int = Field(default=1, ge=1)
    temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    seed: int | None = 17
    timeout_seconds: float = Field(default=30.0, gt=0.0)
    retries: int = Field(default=1, ge=0)
    retry_backoff_seconds: float = Field(default=0.25, ge=0.0)
    concurrency: int = Field(default=4, ge=1)
    call_spacing_seconds: float = Field(default=0.0, ge=0.0)
    model_name: str | None = None


class CallPacer:
    """Enforce minimum spacing between calls across concurrent runner tasks."""

    def __init__(self, spacing_seconds: float) -> None:
        self.spacing_seconds = spacing_seconds
        self.last_call_at: float | None = None
        self.lock = asyncio.Lock()

    async def pace(self) -> float:
        """Wait if needed to honor call spacing. Returns the delay elapsed in pacing."""
        if self.spacing_seconds <= 0.0:
            return 0.0
        async with self.lock:
            now = time.monotonic()
            wait_time = 0.0
            if self.last_call_at is not None:
                remaining = self.spacing_seconds - (now - self.last_call_at)
                if remaining > 0:
                    wait_time = remaining
                    await asyncio.sleep(remaining)
            self.last_call_at = time.monotonic()
            return wait_time


ProgressCallback = Callable[[ExecutionResult, int, int], Coroutine[Any, Any, None] | None]


class EvaluationRunner:
    """Execute cases concurrently while bounding each individual execution."""

    def __init__(
        self,
        agent: Agent,
        options: RunnerOptions | None = None,
        *,
        pacer: CallPacer | None = None,
        cancel_event: asyncio.Event | None = None,
        on_case_completed: ProgressCallback | None = None,
    ) -> None:
        self.agent = agent
        self.options = options or RunnerOptions()
        self.pacer = pacer or CallPacer(self.options.call_spacing_seconds)
        self.cancel_event = cancel_event or asyncio.Event()
        self.on_case_completed = on_case_completed

    async def run_cases(self, cases: Sequence[TestCase]) -> list[ExecutionResult]:
        """Run each case the configured number of times and preserve input order."""
        semaphore = asyncio.Semaphore(self.options.concurrency)
        total_tasks = len(cases) * self.options.repeats
        completed_count = 0
        counter_lock = asyncio.Lock()

        async def _run_and_notify(case: TestCase, repeat_index: int) -> ExecutionResult:
            nonlocal completed_count
            res = await self._run_one(case, repeat_index, semaphore)
            async with counter_lock:
                completed_count += 1
                curr_completed = completed_count
            if self.on_case_completed is not None:
                cb_res = self.on_case_completed(res, curr_completed, total_tasks)
                if asyncio.iscoroutine(cb_res):
                    await cb_res
            return res

        tasks = [
            _run_and_notify(case, repeat_index)
            for case in cases
            for repeat_index in range(self.options.repeats)
        ]
        return list(await asyncio.gather(*tasks))

    async def _run_one(
        self,
        case: TestCase,
        repeat_index: int,
        semaphore: asyncio.Semaphore,
    ) -> ExecutionResult:
        if self.cancel_event.is_set():
            return ExecutionResult(
                case_id=case.id,
                repeat_index=repeat_index,
                success=False,
                error="Cancelled: execution was cancelled by user.",
                steps=[
                    TraceStep(
                        kind="limit",
                        latency_ms=0.0,
                        result="Cancelled",
                        error="Cancelled: execution was cancelled by user.",
                    )
                ],
                attempts=1,
                duration_ms=0.0,
            )

        async with semaphore:
            if self.cancel_event.is_set():
                return ExecutionResult(
                    case_id=case.id,
                    repeat_index=repeat_index,
                    success=False,
                    error="Cancelled: execution was cancelled by user.",
                    steps=[
                        TraceStep(
                            kind="limit",
                            latency_ms=0.0,
                            result="Cancelled",
                            error="Cancelled: execution was cancelled by user.",
                        )
                    ],
                    attempts=1,
                    duration_ms=0.0,
                )

            pure_duration_ms = 0.0
            last_error = "Execution failed without an exception."
            last_steps: list[TraceStep] = []

            for attempt in range(1, self.options.retries + 2):
                if self.cancel_event.is_set():
                    last_error = "Cancelled: execution was cancelled by user."
                    break

                # Pacing before call (do not count pacing delay towards latency)
                await self.pacer.pace()

                attempt_start = time.perf_counter()
                try:
                    result = await asyncio.wait_for(
                        self.agent.run(
                            case.input,
                            temperature=self.options.temperature,
                            seed=self.options.seed,
                            max_steps=case.max_steps,
                        ),
                        timeout=self.options.timeout_seconds,
                    )
                    attempt_elapsed_ms = (time.perf_counter() - attempt_start) * 1000
                    pure_duration_ms += attempt_elapsed_ms
                    last_steps = result.steps
                    if result.error is not None:
                        raise RuntimeError(result.error)

                    return ExecutionResult(
                        case_id=case.id,
                        repeat_index=repeat_index,
                        success=True,
                        output=result.output,
                        steps=result.steps,
                        attempts=attempt,
                        duration_ms=pure_duration_ms,
                    )
                except Exception as exc:
                    attempt_elapsed_ms = (time.perf_counter() - attempt_start) * 1000
                    pure_duration_ms += attempt_elapsed_ms
                    last_error = f"{type(exc).__name__}: {exc}"

                    if attempt > self.options.retries or self.cancel_event.is_set():
                        logger.exception(
                            "Agent execution failed",
                            extra={
                                "case_id": case.id,
                                "repeat_index": repeat_index,
                                "attempts": attempt,
                            },
                        )
                        break

                    delay = self.options.retry_backoff_seconds * (2 ** (attempt - 1))
                    logger.warning(
                        "Retrying agent execution",
                        extra={
                            "case_id": case.id,
                            "repeat_index": repeat_index,
                            "attempt": attempt,
                            "error": last_error,
                            "retry_delay_seconds": delay,
                        },
                    )
                    if delay:
                        # Deliberate backoff is not added to execution duration
                        await asyncio.sleep(delay)

            return ExecutionResult(
                case_id=case.id,
                repeat_index=repeat_index,
                success=False,
                error=last_error,
                steps=last_steps
                or [
                    TraceStep(
                        kind="limit",
                        latency_ms=0.0,
                        result=last_error,
                        error=last_error,
                    )
                ],
                attempts=min(attempt, self.options.retries + 1),
                duration_ms=pure_duration_ms,
            )


__all__ = ["Agent", "CallPacer", "EvaluationRunner", "RunnerOptions"]
