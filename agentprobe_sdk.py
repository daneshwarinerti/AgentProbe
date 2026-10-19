"""Dependency-free tracing SDK importable by agent projects as ``agentprobe_sdk``."""

from __future__ import annotations

import contextvars
import functools
import inspect
import json
import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Literal, ParamSpec, TypeVar, overload

P = ParamSpec("P")
R = TypeVar("R")
_CURRENT_AGENT: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "agentprobe_current_agent", default=None
)
_WRITE_LOCK = threading.Lock()


@dataclass
class TraceSpan:
    """Mutable values yielded by a tracing context manager."""

    result: Any = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    error: str | None = None
    prompt: list[dict[str, Any]] | None = None
    response: Any = None


@contextmanager
def trace_agent(agent_name: str) -> Iterator[TraceSpan]:
    """Set the active agent name and emit ordered start/summary events."""
    token = _CURRENT_AGENT.set(agent_name)
    started = time.perf_counter()
    span = TraceSpan()
    _emit({"kind": "agent", "agent_name": agent_name, "latency_ms": 0.0})
    try:
        yield span
    except Exception as exc:
        span.error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        _emit(
            {
                "kind": "agent_summary",
                "agent_name": agent_name,
                "result": span.result,
                "input_tokens": span.input_tokens,
                "output_tokens": span.output_tokens,
                "cost_usd": span.cost_usd,
                "error": span.error,
                "latency_ms": (time.perf_counter() - started) * 1000,
            }
        )
        _CURRENT_AGENT.reset(token)


@contextmanager
def trace_step(
    kind: Literal["llm", "tool", "limit"],
    *,
    agent_name: str | None = None,
    tool_name: str | None = None,
    args: dict[str, Any] | None = None,
    handoff_to: str | None = None,
) -> Iterator[TraceSpan]:
    """Trace one event; exceptions are emitted and re-raised."""
    span = TraceSpan()
    started = time.perf_counter()
    error: str | None = None
    try:
        yield span
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
        span.error = error
        raise
    finally:
        _emit(
            {
                "kind": kind,
                "agent_name": agent_name or _CURRENT_AGENT.get(),
                "tool_name": tool_name,
                "args": args,
                "handoff_to": handoff_to,
                "result": span.result,
                "input_tokens": span.input_tokens,
                "output_tokens": span.output_tokens,
                "cost_usd": span.cost_usd,
                "error": span.error or error,
                "prompt": span.prompt,
                "response": span.response,
                "latency_ms": (time.perf_counter() - started) * 1000,
            }
        )


def trace_tool(tool_name: str, args: dict[str, Any] | None = None) -> Any:
    """Create a tool-call tracing context manager."""
    return trace_step("tool", tool_name=tool_name, args=args)


def trace_handoff(from_agent: str, to_agent: str, *, result: Any = None) -> None:
    """Record a handoff event between named agents."""
    _emit(
        {
            "kind": "handoff",
            "agent_name": from_agent,
            "handoff_to": to_agent,
            "result": result,
            "latency_ms": 0.0,
        }
    )


@overload
def traced_agent(function: Callable[P, R]) -> Callable[P, R]: ...


@overload
def traced_agent(*, name: str) -> Callable[[Callable[P, R]], Callable[P, R]]: ...


def traced_agent(function: Callable[P, R] | None = None, *, name: str | None = None) -> Any:
    """Decorate a sync or async agent function with a named agent span."""

    def decorate(target: Callable[P, R]) -> Callable[P, R]:
        agent_name = name or target.__name__
        if inspect.iscoroutinefunction(target):

            @functools.wraps(target)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
                with trace_agent(agent_name) as span:
                    result = await target(*args, **kwargs)
                    span.result = _result_for_trace(result)
                    return result

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(target)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            with trace_agent(agent_name) as span:
                result = target(*args, **kwargs)
                span.result = _result_for_trace(result)
                return result

        return sync_wrapper

    return decorate(function) if function is not None else decorate


@overload
def traced_tool(function: Callable[P, R]) -> Callable[P, R]: ...


@overload
def traced_tool(*, name: str) -> Callable[[Callable[P, R]], Callable[P, R]]: ...


def traced_tool(function: Callable[P, R] | None = None, *, name: str | None = None) -> Any:
    """Decorate a sync or async tool and capture its bound inputs and return value."""

    def decorate(target: Callable[P, R]) -> Callable[P, R]:
        tool_name = name or target.__name__
        signature = inspect.signature(target)

        def get_args(args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
            bound = signature.bind_partial(*args, **kwargs)
            return {key: value for key, value in bound.arguments.items() if key != "self"}

        if inspect.iscoroutinefunction(target):

            @functools.wraps(target)
            async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
                with trace_tool(tool_name, get_args(args, kwargs)) as span:
                    result = await target(*args, **kwargs)
                    span.result = _result_for_trace(result)
                    return result

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(target)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            with trace_tool(tool_name, get_args(args, kwargs)) as span:
                result = target(*args, **kwargs)
                span.result = _result_for_trace(result)
                return result

        return sync_wrapper

    return decorate(function) if function is not None else decorate


def _emit(event: dict[str, Any]) -> None:
    trace_path = os.environ.get("AGENTPROBE_TRACE_FILE")
    if trace_path is None:
        return
    compact = {key: value for key, value in event.items() if value is not None}
    with _WRITE_LOCK, open(trace_path, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(compact, ensure_ascii=True, default=str) + "\n")


def _result_for_trace(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool, type(None), dict, list)):
        return value
    return repr(value)
