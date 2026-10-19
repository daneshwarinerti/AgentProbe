"""Execute a trusted local Python agent entry point in a bounded subprocess."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from backend.app.adapters.base import normalize_agent_result
from backend.app.runner.models import AgentResult, TraceStep

_WORKER = r"""
import asyncio
import importlib
import inspect
import json
import os
import sys
from pathlib import Path

project = Path(sys.argv[1]).resolve()
entry_point = sys.argv[2]
trace_path = Path(os.environ["AGENTPROBE_TRACE_FILE"])
sdk_path = trace_path.with_name("agentprobe_sdk.py")
payload = json.load(sys.stdin)
sdk_path.write_text(payload.pop("sdk_source"), encoding="utf-8")
sys.path.insert(0, str(sdk_path.parent))
module_name, function_name = entry_point.split(":", maxsplit=1)
sys.path.insert(0, str(project))
function = getattr(importlib.import_module(module_name), function_name)
case_input = payload["case_input"]
input_mapping = payload["input_mapping"]
if input_mapping.get("mode") == "document_path":
    document = Path(case_input)
    document = document if document.is_absolute() else project / document
    document = document.resolve(strict=True)
    if not document.is_relative_to(project) or not document.is_file():
        raise ValueError("Document input must be a file inside the uploaded project.")
    result = function(**{input_mapping["parameter"]: str(document)})
elif input_mapping.get("mode") == "kwargs":
    try:
        input_data = json.loads(case_input)
    except json.JSONDecodeError:
        input_data = {"text": case_input}
    if not isinstance(input_data, dict):
        raise TypeError("Keyword-mapped input must be a JSON object or plain text.")

    def get_input_value(source):
        value = input_data
        for segment in source.split("."):
            if not isinstance(value, dict) or segment not in value:
                raise ValueError("Input mapping refers to a missing field: " + source)
            value = value[segment]
        return value

    arguments = {
        name: get_input_value(source)
        for name, source in input_mapping["fields"].items()
    }
    for name, source in input_mapping["fields"].items():
        if source == "document_path":
            document = Path(arguments[name])
            document = document if document.is_absolute() else project / document
            document = document.resolve(strict=True)
            if not document.is_relative_to(project) or not document.is_file():
                raise ValueError("Document input must be a file inside the uploaded project.")
            arguments[name] = str(document)
    result = function(**arguments)
elif input_mapping.get("parameter"):
    result = function(**{input_mapping["parameter"]: case_input})
else:
    result = function(case_input)
if inspect.isawaitable(result):
    result = asyncio.run(result)
if isinstance(result, str):
    result = {"final_answer": result}
if not isinstance(result, dict):
    raise TypeError("Agent function must return a string or a result mapping.")
output_mapping = payload["output_mapping"]
if output_mapping:
    mapped = {}
    for output_name, source in output_mapping.items():
        value = result
        for segment in source.split("."):
            if not isinstance(value, dict) or segment not in value:
                raise ValueError("Output mapping refers to a missing field: " + source)
            value = value[segment]
        mapped[output_name] = value
    result = mapped
print(json.dumps(result, ensure_ascii=True, default=str))
"""


class PythonModuleAdapter:
    """Run a trusted project module in a separate Python process.

    This adapter is process-isolated but not a security sandbox. Use GitRepoAdapter
    for untrusted code.
    """

    def __init__(
        self,
        project_path: Path,
        entry_point: str,
        *,
        timeout_seconds: float = 30.0,
        env: dict[str, str] | None = None,
        python_executable: Path | None = None,
        input_mapping: dict[str, Any] | None = None,
        output_mapping: dict[str, str] | None = None,
    ) -> None:
        self.project_path = project_path.resolve(strict=True)
        if not self.project_path.is_dir():
            raise ValueError(f"Python agent project path is not a directory: {project_path}")
        self.entry_point = entry_point
        self.timeout_seconds = timeout_seconds
        self.env = env or {}
        self.input_mapping = input_mapping or {"mode": "text"}
        self.output_mapping = output_mapping or {}
        self.python_executable = python_executable or self._project_python()
        if ":" not in entry_point or not all(entry_point.split(":", maxsplit=1)):
            raise ValueError("Python entry point must use the 'module:function' format.")

    def _project_python(self) -> Path:
        candidates = [
            self.project_path / ".venv" / "Scripts" / "python.exe",
            self.project_path / ".venv" / "bin" / "python",
        ]
        return next((path for path in candidates if path.is_file()), Path(sys.executable))

    async def run(
        self,
        case_input: str,
        *,
        temperature: float = 0.0,
        seed: int | None = None,
        max_steps: int = 5,
    ) -> AgentResult:
        started = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="agentprobe-trace-") as trace_directory:
            trace_path = Path(trace_directory) / "steps.jsonl"
            environment = _isolated_environment(self.env)
            environment["AGENTPROBE_TRACE_FILE"] = str(trace_path)
            payload = json.dumps(
                {
                    "case_input": case_input,
                    "input_mapping": self.input_mapping,
                    "output_mapping": self.output_mapping,
                    "sdk_source": (
                        Path(__file__).resolve().parents[3] / "agentprobe_sdk.py"
                    ).read_text(encoding="utf-8"),
                }
            ).encode("utf-8")
            args = [
                str(self.python_executable),
                "-c",
                _WORKER,
                str(self.project_path),
                self.entry_point,
            ]
            try:
                returncode, stdout, stderr = await asyncio.to_thread(
                    _run_worker_sync,
                    args,
                    payload,
                    self.project_path,
                    environment,
                    self.timeout_seconds,
                )
            except TimeoutError:
                error = f"Python agent exceeded {self.timeout_seconds:g}s timeout."
                steps = _read_steps(trace_path)
                steps.append(
                    TraceStep(
                        kind="limit",
                        error=error,
                        latency_ms=(time.perf_counter() - started) * 1000,
                    )
                )
                return AgentResult(final_answer="", steps=steps, error=error)
            except asyncio.CancelledError:
                raise

            if returncode != 0:
                details = _redact(
                    stderr.decode("utf-8", errors="replace").strip(), self.env.values()
                )
                error = f"Python agent exited with code {returncode}: {details}"
                return AgentResult(
                    final_answer="",
                    steps=_read_steps(trace_path),
                    error=error,
                )
            try:
                result: Any = json.loads(stdout.decode("utf-8"))
            except json.JSONDecodeError:
                details = _redact(
                    stderr.decode("utf-8", errors="replace").strip(), self.env.values()
                )
                error = "Python agent stdout must contain one JSON result object." + (
                    f" stderr: {details}" if details else ""
                )
                return AgentResult(
                    final_answer="",
                    steps=_read_steps(trace_path),
                    error=error,
                )
            steps = _read_steps(trace_path)
            normalized = normalize_agent_result(result)
            combined_steps = [*steps, *normalized.steps]
            if not combined_steps:
                combined_steps = [
                    TraceStep(
                        kind="agent",
                        result="output-only: target did not emit trace events",
                        latency_ms=(time.perf_counter() - started) * 1000,
                    )
                ]
            return AgentResult(
                final_answer=normalized.final_answer,
                steps=combined_steps,
                error=normalized.error,
            )


def _isolated_environment(configured: dict[str, str]) -> dict[str, str]:
    """Keep inherited environment access limited to process essentials."""
    allowed = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT")
    return {**{key: os.environ[key] for key in allowed if key in os.environ}, **configured}


def _read_steps(path: Path) -> list[TraceStep]:
    if not path.exists():
        return []
    steps: list[TraceStep] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        try:
            steps.append(TraceStep.model_validate(json.loads(line)))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"Invalid tracing SDK event on line {line_number}: {exc}") from exc
    return steps


def _redact(text: str, secret_values: Iterable[str]) -> str:
    """Remove explicitly configured secret values from subprocess diagnostics."""
    for value in secret_values:
        if value:
            text = text.replace(value, "[REDACTED]")
    return text


def _run_worker_sync(
    args: list[str],
    payload: bytes,
    cwd: Path | str,
    env: dict[str, str],
    timeout: float,
) -> tuple[int, bytes, bytes]:
    proc = subprocess.Popen(
        args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
    )
    try:
        stdout, stderr = proc.communicate(input=payload, timeout=timeout)
        return proc.returncode, stdout, stderr
    except subprocess.TimeoutExpired:
        proc.kill()
        stdout, stderr = proc.communicate()
        raise TimeoutError from None
