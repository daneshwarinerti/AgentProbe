"""HTTP agent adapter with explicit request/response mappings."""

from __future__ import annotations

import time
from typing import Any

import httpx

from backend.app.runner.models import AgentResult, TraceStep


class HttpAdapter:
    """POST cases to an HTTP endpoint and extract the configured result fields."""

    def __init__(
        self,
        url: str,
        *,
        request_mapping: dict[str, str],
        response_mapping: dict[str, str],
        timeout_seconds: float = 30.0,
        headers: dict[str, str] | None = None,
        auth_header: str | None = None,
        auth_value: str | None = None,
    ) -> None:
        self.url = url
        self.request_mapping = request_mapping
        self.response_mapping = response_mapping
        self.timeout_seconds = timeout_seconds
        self.headers = headers or {}
        self.auth_header = auth_header
        self.auth_value = auth_value

    async def run(
        self,
        case_input: str,
        *,
        temperature: float = 0.0,
        seed: int | None = None,
        max_steps: int = 5,
    ) -> AgentResult:
        body = {
            key: case_input if source == "case_input" else source
            for key, source in self.request_mapping.items()
        }
        headers = dict(self.headers)
        if self.auth_header and self.auth_value:
            headers[self.auth_header] = self.auth_value
        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            response = await client.post(self.url, json=body, headers=headers)
            response.raise_for_status()
        elapsed = (time.perf_counter() - started) * 1000
        payload: Any = response.json()
        if not isinstance(payload, dict):
            raise ValueError("HTTP agent response must be a JSON object.")
        answer = _get_path(payload, self.response_mapping.get("final_answer", "final_answer"))
        if not isinstance(answer, str):
            raise ValueError("Mapped HTTP agent final_answer must be a string.")
        raw_steps = _get_path(payload, self.response_mapping.get("steps", "steps"), default=[])
        steps = [TraceStep.model_validate(item) for item in raw_steps] if raw_steps else []
        token_value = _get_path(
            payload, self.response_mapping.get("tokens", "tokens"), default=None
        )
        cost_value = _get_path(
            payload, self.response_mapping.get("cost_usd", "cost_usd"), default=None
        )
        if not steps:
            steps.append(
                TraceStep(
                    kind="agent",
                    result="output-only: HTTP response did not include mapped trace steps",
                    input_tokens=token_value,
                    cost_usd=cost_value,
                    latency_ms=elapsed,
                )
            )
        return AgentResult(final_answer=answer, steps=steps)


def _get_path(payload: Any, path: str, *, default: Any = ...) -> Any:
    value = payload
    for segment in path.split("."):
        if not isinstance(value, dict) or segment not in value:
            if default is not ...:
                return default
            raise ValueError(f"HTTP response is missing mapped field {path!r}.")
        value = value[segment]
    return value
