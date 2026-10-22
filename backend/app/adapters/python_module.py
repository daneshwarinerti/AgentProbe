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
try:
    for _sub in project.iterdir():
        if _sub.is_dir() and not _sub.name.startswith((".", "__")):
            sys.path.insert(0, str(_sub))
except Exception:
    pass

mod = importlib.import_module(module_name)
target = getattr(mod, function_name, None)

if target is None:
    for fallback_name in ("run", "main", "chat", "query", "agent", "reasoning_agent", "bot", "app", "executor"):
        candidate = getattr(mod, fallback_name, None)
        if candidate is not None:
            target = candidate
            break

if target is None:
    for attr_name in dir(mod):
        if attr_name.startswith("_"):
            continue
        val = getattr(mod, attr_name)
        if hasattr(val, "run") or hasattr(val, "invoke") or hasattr(val, "query") or hasattr(val, "kickoff"):
            target = val
            break

if target is None:
    raise AttributeError(f"Module '{module_name}' has no attribute '{function_name}' or discoverable agent.")

case_input = payload["case_input"]
input_mapping = payload.get("input_mapping") or {}

def _call_agent(tgt, inp, mapping):
    if mapping.get("mode") == "document_path":
        doc = Path(inp)
        doc = doc if doc.is_absolute() else project / doc
        doc = doc.resolve(strict=True)
        if not doc.is_relative_to(project) or not doc.is_file():
            raise ValueError("Document input must be a file inside the uploaded project.")
        param = mapping.get("parameter") or "document_path"
        return tgt(**{param: str(doc)})

    if mapping.get("mode") == "kwargs":
        try:
            data = json.loads(inp)
        except (json.JSONDecodeError, TypeError):
            data = {"text": inp}
        if not isinstance(data, dict):
            raise TypeError("Keyword-mapped input must be a JSON object or plain text.")
        def _get_val(source):
            v = data
            for seg in source.split("."):
                if not isinstance(v, dict) or seg not in v:
                    raise ValueError("Input mapping refers to a missing field: " + source)
                v = v[seg]
            return v
        kwargs = {k: _get_val(s) for k, s in mapping.get("fields", {}).items()}
        return tgt(**kwargs)

    if mapping.get("parameter"):
        return tgt(**{mapping["parameter"]: inp})

    if hasattr(tgt, "run") and callable(getattr(tgt, "run")) and not inspect.isclass(tgt):
        try:
            return tgt.run(inp)
        except TypeError:
            pass

    if hasattr(tgt, "invoke") and callable(getattr(tgt, "invoke")) and not inspect.isclass(tgt):
        try:
            return tgt.invoke(inp)
        except TypeError:
            pass

    if hasattr(tgt, "query") and callable(getattr(tgt, "query")) and not inspect.isclass(tgt):
        try:
            return tgt.query(inp)
        except TypeError:
            pass

    if hasattr(tgt, "kickoff") and callable(getattr(tgt, "kickoff")) and not inspect.isclass(tgt):
        try:
            return tgt.kickoff(inputs={"topic": inp} if isinstance(inp, str) else inp)
        except TypeError:
            pass

    if callable(tgt):
        try:
            sig = inspect.signature(tgt)
            params = list(sig.parameters.values())
        except (ValueError, TypeError):
            params = []

        if params:
            param_names = [p.name for p in params]
            for candidate in ("prompt", "query", "input", "user_input", "text", "message", "query_text", "question"):
                if candidate in param_names:
                    try:
                        return tgt(**{candidate: inp})
                    except TypeError:
                        break
            first = params[0]
            if first.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.VAR_POSITIONAL):
                return tgt(inp)

        try:
            return tgt(inp)
        except TypeError:
            return tgt()

    raise TypeError(f"Target {type(tgt)} is neither callable nor an agent object.")

result = _call_agent(target, case_input, input_mapping)
if inspect.isawaitable(result):
    result = asyncio.run(result)

if result is None:
    norm_result = {"final_answer": ""}
elif hasattr(result, "model_dump") and callable(result.model_dump):
    norm_result = result.model_dump()
elif hasattr(result, "dict") and callable(result.dict):
    norm_result = result.dict()
elif hasattr(result, "content") and not isinstance(result, type):
    c = result.content
    norm_result = c if isinstance(c, dict) else {"final_answer": str(c)}
elif hasattr(result, "raw") and not isinstance(result, type):
    norm_result = {"final_answer": str(result.raw)}
elif hasattr(result, "response") and not isinstance(result, type):
    norm_result = {"final_answer": str(result.response)}
elif isinstance(result, str):
    norm_result = {"final_answer": result}
elif isinstance(result, dict):
    norm_result = dict(result)
    if "final_answer" not in norm_result:
        for k in ("output", "answer", "response", "result", "text", "message"):
            if k in norm_result:
                norm_result["final_answer"] = str(norm_result[k])
                break
        else:
            norm_result["final_answer"] = json.dumps(norm_result, default=str)
else:
    norm_result = {"final_answer": str(result)}

output_mapping = payload.get("output_mapping")
if output_mapping:
    mapped = {}
    for output_name, source in output_mapping.items():
        val = norm_result
        for seg in source.split("."):
            if not isinstance(val, dict) or seg not in val:
                raise ValueError("Output mapping refers to a missing field: " + source)
            val = val[seg]
        mapped[output_name] = val
    norm_result = mapped

print(json.dumps(norm_result, ensure_ascii=True, default=str))
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

            raw_stdout = stdout.decode("utf-8", errors="replace").strip()

            result: Any = None

            try:

                result = json.loads(raw_stdout)

            except json.JSONDecodeError:

                for line in reversed(raw_stdout.splitlines()):

                    line = line.strip()

                    if line.startswith("{") and line.endswith("}"):

                        try:

                            result = json.loads(line)

                            break

                        except json.JSONDecodeError:

                            pass

            if result is None:

                cleaned_stdout = _redact(raw_stdout, self.env.values()).strip()

                if cleaned_stdout:

                    result = {"final_answer": cleaned_stdout}

                else:

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

    """Keep inherited environment access limited to process essentials and standard AI provider keys."""

    allowed = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT")

    base = {key: os.environ[key] for key in allowed if key in os.environ}

    for key, val in os.environ.items():

        k_upper = key.upper()

        if any(k_upper.startswith(p) for p in ("OPENAI_", "NEBIUS_", "ANTHROPIC_", "GEMINI_", "GOOGLE_", "GROQ_", "COHERE_", "MISTRAL_")):

            base[key] = val

    return {**base, **configured}





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

