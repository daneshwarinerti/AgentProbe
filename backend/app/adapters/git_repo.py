"""Run GitHub agent projects inside a policy-restricted Docker container."""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from backend.app.adapters.base import normalize_agent_result
from backend.app.runner.models import AgentResult, TraceStep

_GITHUB_URL = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?$")
_DOCKER_NETWORK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_CONTAINER_WORKER = r"""
import asyncio
import importlib
import inspect
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

workspace = Path("/tmp/agentprobe-project")
sdk_path = Path("/tmp/agentprobe-sdk")
sdk_path.mkdir(parents=True, exist_ok=True)
payload = json.load(sys.stdin)
(sdk_path / "agentprobe_sdk.py").write_text(payload["sdk_source"], encoding="utf-8")
sys.path.insert(0, str(sdk_path))
subprocess.run(
    ["git", "clone", "--depth", "1", os.environ["AGENTPROBE_REPO_URL"], str(workspace)],
    check=True,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
    text=True,
)
requirements = workspace / "requirements.txt"
if requirements.is_file():
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
         "--no-input", "--target", "/tmp/agentprobe-deps", "-r", str(requirements)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    sys.path.insert(0, "/tmp/agentprobe-deps")
elif (workspace / "pyproject.toml").is_file() or (workspace / "setup.py").is_file():
    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
         "--no-input", "--target", "/tmp/agentprobe-deps", str(workspace)],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    sys.path.insert(0, "/tmp/agentprobe-deps")
sys.path.insert(0, str(workspace))
service = None
try:
    if os.environ["AGENTPROBE_DELEGATE"] == "python_module":
        module_name, function_name = os.environ["AGENTPROBE_ENTRY_POINT"].split(":", maxsplit=1)
        function = getattr(importlib.import_module(module_name), function_name)
        result = function(payload["case_input"])
        if inspect.isawaitable(result):
            result = asyncio.run(result)
        if isinstance(result, str):
            result = {"final_answer": result}
        if not isinstance(result, dict):
            raise TypeError("Agent function must return a string or a result mapping.")
    else:
        service_command = json.loads(os.environ["AGENTPROBE_SERVICE_COMMAND"])
        service = subprocess.Popen(
            service_command,
            cwd=workspace,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env={
                **os.environ,
                "PYTHONPATH": os.pathsep.join(
                    [
                        str(sdk_path),
                        "/tmp/agentprobe-deps",
                        str(workspace),
                        os.environ.get("PYTHONPATH", ""),
                    ]
                ),
            },
        )
        service_url = os.environ["AGENTPROBE_SERVICE_URL"]
        parsed = urlsplit(service_url)
        deadline = time.monotonic() + float(os.environ["AGENTPROBE_STARTUP_TIMEOUT"])
        while True:
            if service.poll() is not None:
                raise RuntimeError("HTTP agent service exited before becoming ready.")
            try:
                with socket.create_connection((parsed.hostname, parsed.port or 80), timeout=0.2):
                    break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("HTTP agent service did not become ready in time.")
                time.sleep(0.1)
        body = {
            key: payload["case_input"] if source == "case_input" else source
            for key, source in json.loads(os.environ["AGENTPROBE_REQUEST_MAPPING"]).items()
        }
        headers = {"Content-Type": "application/json"}
        headers.update(json.loads(os.environ["AGENTPROBE_HTTP_HEADERS"]))
        auth_header = os.environ.get("AGENTPROBE_AUTH_HEADER")
        auth_value = os.environ.get("AGENTPROBE_AUTH_VALUE")
        if auth_header and auth_value:
            headers[auth_header] = auth_value
        request = urllib.request.Request(
            service_url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        request_started = time.monotonic()
        with urllib.request.urlopen(
            request,
            timeout=float(os.environ["AGENTPROBE_HTTP_TIMEOUT"]),
        ) as response:
            response_payload = json.loads(response.read().decode("utf-8"))
        request_elapsed = (time.monotonic() - request_started) * 1000
        if not isinstance(response_payload, dict):
            raise ValueError("HTTP agent response must be a JSON object.")
        response_mapping = json.loads(os.environ["AGENTPROBE_RESPONSE_MAPPING"])
        def get_path(path, default=None):
            value = response_payload
            for segment in path.split("."):
                if not isinstance(value, dict) or segment not in value:
                    return default
                value = value[segment]
            return value
        answer = get_path(response_mapping.get("final_answer", "final_answer"))
        if not isinstance(answer, str):
            raise ValueError("HTTP agent response is missing a string final answer.")
        result = {
            "final_answer": answer,
            "steps": get_path(response_mapping.get("steps", "steps"), []),
        }
        if not result["steps"]:
            result["steps"] = [{
                "kind": "agent",
                "result": "output-only: HTTP response did not include mapped trace steps",
                "input_tokens": get_path(response_mapping.get("tokens", "tokens")),
                "cost_usd": get_path(response_mapping.get("cost_usd", "cost_usd")),
                "latency_ms": request_elapsed,
            }]
except Exception as exc:
    result = {
        "final_answer": "",
        "error": f"{type(exc).__name__}: {exc}",
    }
    if not os.path.exists(os.environ["AGENTPROBE_TRACE_FILE"]):
        result["steps"] = [{
            "kind": "agent",
            "error": result["error"],
            "latency_ms": 0.0,
        }]
finally:
    if service is not None and service.poll() is None:
        service.terminate()
        try:
            service.wait(timeout=2)
        except subprocess.TimeoutExpired:
            service.kill()
            service.wait()
trace_path = Path(os.environ["AGENTPROBE_TRACE_FILE"])
events = []
if trace_path.is_file():
    events = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
print(json.dumps(
    {"__agentprobe_result": result, "__agentprobe_trace": events},
    ensure_ascii=True,
    default=str,
))
"""


class GitRepoAdapter:
    """Clone/install/execute an untrusted GitHub project in a hardened container.

    The configured Docker network must be externally managed with an enforced
    GitHub/PyPI egress allowlist. Docker network names do not enforce that policy.
    """

    def __init__(
        self,
        repo_url: str,
        entry_point: str | None,
        *,
        docker_network: str | None,
        delegate_type: str = "python_module",
        docker_image: str = "agentprobe-sandbox:latest",
        timeout_seconds: float = 300.0,
        startup_timeout_seconds: float = 30.0,
        env: dict[str, str] | None = None,
        service_command: list[str] | None = None,
        service_url: str = "http://127.0.0.1:8000/run",
        request_mapping: dict[str, str] | None = None,
        response_mapping: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        auth_header: str | None = None,
        auth_value: str | None = None,
        http_timeout_seconds: float = 30.0,
        docker_executable: str = "docker",
    ) -> None:
        if not _GITHUB_URL.fullmatch(repo_url):
            raise ValueError("Git repository URL must be an HTTPS GitHub repository URL.")
        if not docker_network:
            raise ValueError(
                "GitRepoAdapter requires a caller-provided Docker network with an "
                "externally enforced egress allowlist."
            )
        if not _DOCKER_NETWORK.fullmatch(docker_network) or docker_network.lower() in {
            "bridge",
            "host",
            "none",
        }:
            raise ValueError(
                "GitRepoAdapter requires a named custom Docker network; built-in networks "
                "cannot be used as an egress allowlist."
            )
        if delegate_type not in {"python_module", "http"}:
            raise ValueError("Git repo delegate_type must be 'python_module' or 'http'.")
        if delegate_type == "python_module" and (not entry_point or ":" not in entry_point):
            raise ValueError("Git repository entry point must use the 'module:function' format.")
        if delegate_type == "http":
            parsed_url = urlsplit(service_url)
            try:
                _ = parsed_url.port
            except ValueError as exc:
                raise ValueError("Git repo HTTP delegate URL has an invalid port.") from exc
            if (
                parsed_url.scheme != "http"
                or parsed_url.hostname not in {"127.0.0.1", "localhost"}
                or parsed_url.username is not None
                or parsed_url.password is not None
            ):
                raise ValueError("Git repo HTTP delegate URL must use localhost HTTP.")
            if not service_command:
                raise ValueError("Git repo HTTP delegate requires a service_command argument list.")
        reserved = {"AGENTPROBE_REPO_URL", "AGENTPROBE_ENTRY_POINT", "AGENTPROBE_TRACE_FILE"}
        if reserved & (env or {}).keys():
            names = ", ".join(sorted(reserved & (env or {}).keys()))
            raise ValueError(f"Git target env cannot override adapter variables: {names}.")
        self.repo_url = repo_url
        self.entry_point = entry_point
        self.delegate_type = delegate_type
        self.docker_network = docker_network
        self.docker_image = docker_image
        self.timeout_seconds = timeout_seconds
        self.startup_timeout_seconds = startup_timeout_seconds
        self.env = env or {}
        self.service_command = service_command or []
        self.service_url = service_url
        self.request_mapping = request_mapping or {"input": "case_input"}
        self.response_mapping = response_mapping or {"final_answer": "final_answer"}
        self.headers = headers or {}
        self.auth_header = auth_header
        self.auth_value = auth_value
        self.http_timeout_seconds = http_timeout_seconds
        self.docker_executable = docker_executable

    async def run(
        self,
        case_input: str,
        *,
        temperature: float = 0.0,
        seed: int | None = None,
        max_steps: int = 5,
    ) -> AgentResult:
        started = time.perf_counter()
        container_name = f"agentprobe-{uuid4().hex[:12]}"
        args = [
            self.docker_executable,
            "run",
            "--rm",
            "--interactive",
            "--network",
            self.docker_network,
            "--name",
            container_name,
            "--memory",
            "1g",
            "--cpus",
            "1",
            "--pids-limit",
            "128",
            "--read-only",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=1g",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--user",
            "65532:65532",
            "--env",
            "AGENTPROBE_REPO_URL",
            "--env",
            "AGENTPROBE_ENTRY_POINT",
            "--env",
            "AGENTPROBE_TRACE_FILE=/tmp/agentprobe-trace.jsonl",
        ]
        for key in self.env:
            args.extend(["--env", key])
        internal_environment = {
            "AGENTPROBE_DELEGATE": self.delegate_type,
            "AGENTPROBE_SERVICE_COMMAND": json.dumps(self.service_command),
            "AGENTPROBE_SERVICE_URL": self.service_url,
            "AGENTPROBE_STARTUP_TIMEOUT": str(self.startup_timeout_seconds),
            "AGENTPROBE_REQUEST_MAPPING": json.dumps(self.request_mapping),
            "AGENTPROBE_RESPONSE_MAPPING": json.dumps(self.response_mapping),
            "AGENTPROBE_HTTP_HEADERS": json.dumps(self.headers),
            "AGENTPROBE_HTTP_TIMEOUT": str(self.http_timeout_seconds),
        }
        if self.auth_header:
            internal_environment["AGENTPROBE_AUTH_HEADER"] = self.auth_header
        if self.auth_value:
            internal_environment["AGENTPROBE_AUTH_VALUE"] = self.auth_value
        for key in internal_environment:
            args.extend(["--env", key])
        args.extend(
            [
                "--env",
                "PIP_NO_CACHE_DIR=1",
                "--entrypoint",
                "python",
                self.docker_image,
                "-c",
                _CONTAINER_WORKER,
            ]
        )
        environment = _docker_environment(self.env)
        environment["AGENTPROBE_REPO_URL"] = self.repo_url
        if self.entry_point:
            environment["AGENTPROBE_ENTRY_POINT"] = self.entry_point
        environment.update(internal_environment)
        sdk_source = (Path(__file__).resolve().parents[3] / "agentprobe_sdk.py").read_text(
            encoding="utf-8"
        )
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=environment,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(
                    json.dumps({"case_input": case_input, "sdk_source": sdk_source}).encode("utf-8")
                ),
                timeout=self.timeout_seconds,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
            await _stop_container(self.docker_executable, container_name)
            raise TimeoutError(
                f"Git repository agent exceeded {self.timeout_seconds:g}s timeout."
            ) from None
        except asyncio.CancelledError:
            process.kill()
            await process.wait()
            await _stop_container(self.docker_executable, container_name)
            raise
        if process.returncode != 0:
            detail = _redact(
                stderr.decode("utf-8", errors="replace").strip(),
                [*self.env.values(), self.auth_value or ""],
            )
            raise RuntimeError(
                f"Git repository container exited with code {process.returncode}: {detail}"
            )
        try:
            payload: Any = json.loads(stdout.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError("Git repository agent stdout must contain a JSON result.") from exc
        result = normalize_agent_result(payload["__agentprobe_result"])
        steps = [TraceStep.model_validate(event) for event in payload["__agentprobe_trace"]]
        combined_steps = [*steps, *result.steps]
        secret_values = [*self.env.values(), self.auth_value or ""]
        error = _redact(result.error, secret_values) if result.error else None
        if not combined_steps:
            combined_steps = [
                TraceStep(
                    kind="agent",
                    error=error,
                    result="output-only: target did not emit trace events"
                    if error is None
                    else None,
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
            ]
        return AgentResult(
            final_answer=result.final_answer,
            steps=combined_steps,
            error=error,
        )


async def _stop_container(docker_executable: str, container_name: str) -> None:
    cleanup = await asyncio.create_subprocess_exec(
        docker_executable,
        "stop",
        "--time",
        "1",
        container_name,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await cleanup.communicate()
    if cleanup.returncode != 0:
        detail = stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Failed to stop timed-out container {container_name}: {detail}")


def _docker_environment(configured: dict[str, str]) -> dict[str, str]:
    allowed = ("PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATHEXT")
    return {**{key: os.environ[key] for key in allowed if key in os.environ}, **configured}


def _redact(text: str, secret_values: list[str]) -> str:
    for value in secret_values:
        if value:
            text = text.replace(value, "[REDACTED]")
    return text
