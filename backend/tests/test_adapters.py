"""Tests for configured target adapters and their failure boundaries."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.app.adapters.config import (
    AgentConfigError,
    load_agent_config,
    make_adapter,
)
from backend.app.adapters.git_repo import _CONTAINER_WORKER, GitRepoAdapter
from backend.app.adapters.http import HttpAdapter, _get_path
from backend.app.adapters.python_module import _WORKER, PythonModuleAdapter


def test_embedded_worker_programs_compile() -> None:
    compile(_WORKER, "python_module_worker", "exec")
    compile(_CONTAINER_WORKER, "git_repo_container_worker", "exec")


async def test_python_module_subprocess_returns_sdk_trace_and_sanitizes_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "agent.py").write_text(
        "import os\n"
        "from agentprobe_sdk import traced_agent, traced_tool\n"
        "@traced_tool\n"
        "def lookup(order_id):\n"
        "    return {'status': 'shipped'}\n"
        "@traced_agent(name='support')\n"
        "def run(case_input):\n"
        "    return {'final_answer': str(lookup(case_input)) + ':' + "
        "os.getenv('AGENTPROBE_HOST_SECRET', 'absent')}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENTPROBE_HOST_SECRET", "must-not-leak")
    adapter = PythonModuleAdapter(
        tmp_path,
        "agent:run",
        timeout_seconds=5,
        env={"AGENTPROBE_ALLOWED": "yes"},
    )
    result = await adapter.run("ORD-1001")
    assert result.final_answer == "{'status': 'shipped'}:absent"
    assert [step.kind for step in result.steps] == ["agent", "tool", "agent_summary"]
    assert result.steps[1].agent_name == "support"
    assert result.steps[1].args == {"order_id": "ORD-1001"}
    assert result.steps[1].result == {"status": "shipped"}


async def test_python_module_adapter_times_out_and_returns_limit_trace(tmp_path: Path) -> None:
    (tmp_path / "agent.py").write_text(
        "import time\ndef run(case_input):\n    time.sleep(2)\n    return 'late'\n",
        encoding="utf-8",
    )
    adapter = PythonModuleAdapter(tmp_path, "agent:run", timeout_seconds=0.05)
    result = await adapter.run("input")
    assert result.error == "Python agent exceeded 0.05s timeout."
    assert result.steps[-1].kind == "limit"
    assert result.steps[-1].error == result.error


async def test_python_module_adapter_preserves_trace_on_agent_crash(tmp_path: Path) -> None:
    (tmp_path / "agent.py").write_text(
        "from agentprobe_sdk import traced_tool\n"
        "@traced_tool\n"
        "def explode():\n"
        "    raise ValueError('broken')\n"
        "def run(case_input):\n"
        "    explode()\n",
        encoding="utf-8",
    )
    result = await PythonModuleAdapter(tmp_path, "agent:run").run("input")
    assert result.error is not None
    assert "ValueError: broken" in result.error
    assert result.steps[0].tool_name == "explode"
    assert result.steps[0].error == "ValueError: broken"


async def test_http_adapter_applies_request_and_response_mapping(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}

    class ClientStub:
        def __init__(self, *, timeout: float) -> None:
            seen["timeout"] = timeout

        async def __aenter__(self) -> Any:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def post(
            self, url: str, *, json: dict[str, Any], headers: dict[str, str]
        ) -> httpx.Response:
            seen.update({"url": url, "body": json, "headers": headers})
            return httpx.Response(
                200,
                json={
                    "result": {"text": "answer"},
                    "usage": {"total_tokens": 12, "cost": 0.01},
                },
                request=httpx.Request("POST", url),
            )

    monkeypatch.setattr("backend.app.adapters.http.httpx.AsyncClient", ClientStub)
    adapter = HttpAdapter(
        "https://agent.example.test/run",
        request_mapping={"prompt": "case_input"},
        response_mapping={
            "final_answer": "result.text",
            "tokens": "usage.total_tokens",
            "cost_usd": "usage.cost",
        },
        headers={"X-Workspace": "test"},
        auth_header="Authorization",
        auth_value="Bearer local-test-token",
    )
    result = await adapter.run("hello")
    assert result.final_answer == "answer"
    assert seen["body"] == {"prompt": "hello"}
    assert seen["headers"] == {
        "X-Workspace": "test",
        "Authorization": "Bearer local-test-token",
    }
    assert result.tokens == 12
    assert result.cost_usd == 0.01


def test_http_adapter_requires_mapped_answer() -> None:
    with pytest.raises(ValueError, match="missing mapped field"):
        _get_path({"response": {}}, "response.text")


def test_config_resolves_paths_and_explicit_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "agent-project"
    project.mkdir()
    (project / "agent.py").write_text("def run(value): return value", encoding="utf-8")
    config_path = tmp_path / "agentprobe.yaml"
    config_path.write_text(
        "target:\n"
        "  type: python_module\n"
        "  project_path: agent-project\n"
        "  entry_point: agent:run\n"
        "  env:\n"
        "    SERVICE_TOKEN: ${TEST_AGENT_TOKEN}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("TEST_AGENT_TOKEN", "secret-value")
    config = load_agent_config(config_path)
    adapter = make_adapter(config, config_path=config_path)
    assert isinstance(adapter, PythonModuleAdapter)
    assert adapter.project_path == project
    assert adapter.env == {"SERVICE_TOKEN": "secret-value"}


def test_config_reports_unset_env_and_duplicate_bad_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("MISSING_AGENT_TOKEN", raising=False)
    config_path = tmp_path / "agentprobe.yaml"
    config_path.write_text(
        "target:\n"
        "  type: python_module\n"
        "  project_path: .\n"
        "  entry_point: agent:run\n"
        "  env:\n"
        "    SERVICE_TOKEN: ${MISSING_AGENT_TOKEN}\n",
        encoding="utf-8",
    )
    with pytest.raises(AgentConfigError, match="MISSING_AGENT_TOKEN is not set"):
        load_agent_config(config_path)


@pytest.mark.parametrize(
    ("url", "auth_header", "auth_env", "message"),
    [
        ("https://user:password@example.test/run", None, None, "must not embed credentials"),
        ("https://example.test/run", "Authorization", None, "configure both"),
    ],
)
def test_http_config_rejects_embedded_or_unpaired_credentials(
    tmp_path: Path, url: str, auth_header: str | None, auth_env: str | None, message: str
) -> None:
    config_path = tmp_path / "agentprobe.yaml"
    auth_lines = ""
    if auth_header:
        auth_lines += f"  auth_header: {auth_header}\n"
    if auth_env:
        auth_lines += f"  auth_env: {auth_env}\n"
    config_path.write_text(
        f"target:\n  type: http\n  url: {url}\n" + auth_lines,
        encoding="utf-8",
    )
    with pytest.raises(AgentConfigError, match=message):
        load_agent_config(config_path)


def test_git_repo_adapter_fails_closed_without_allowlisted_network() -> None:
    with pytest.raises(ValueError, match="externally enforced egress allowlist"):
        GitRepoAdapter(
            "https://github.com/example/agent",
            "agent:run",
            docker_network=None,
        )
    with pytest.raises(ValueError, match="named custom Docker network"):
        GitRepoAdapter(
            "https://github.com/example/agent",
            "agent:run",
            docker_network="host",
        )


def test_git_repo_adapter_rejects_non_github_or_non_https_url() -> None:
    with pytest.raises(ValueError, match="HTTPS GitHub"):
        GitRepoAdapter(
            "https://example.com/agent.git",
            "agent:run",
            docker_network="restricted-egress",
        )
    with pytest.raises(ValueError, match="HTTPS GitHub"):
        GitRepoAdapter(
            "https://user:password@github.com/example/agent.git",
            "agent:run",
            docker_network="restricted-egress",
        )


def test_git_repo_adapter_supports_sandboxed_http_service_delegate() -> None:
    adapter = GitRepoAdapter(
        "https://github.com/example/agent.git",
        None,
        docker_network="restricted-egress",
        delegate_type="http",
        service_command=["python", "-m", "agent_service"],
        service_url="http://127.0.0.1:9000/evaluate",
        request_mapping={"prompt": "case_input"},
        response_mapping={"final_answer": "answer"},
    )
    assert adapter.delegate_type == "http"
    assert adapter.service_command == ["python", "-m", "agent_service"]
    with pytest.raises(ValueError, match="localhost HTTP"):
        GitRepoAdapter(
            "https://github.com/example/agent.git",
            None,
            docker_network="restricted-egress",
            delegate_type="http",
            service_command=["python", "-m", "agent_service"],
            service_url="https://127.0.0.1/evaluate",
        )


async def test_git_repo_adapter_runs_hardened_container_without_host_mounts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = [
        {
            "kind": "tool",
            "agent_name": "support",
            "tool_name": "lookup",
            "args": {"id": "1"},
            "result": "found",
            "latency_ms": 1.0,
        }
    ]
    response = {
        "__agentprobe_result": {"final_answer": "done"},
        "__agentprobe_trace": events,
    }
    process = SimpleNamespace(
        returncode=0,
        communicate=AsyncMock(return_value=(json.dumps(response).encode(), b"")),
    )
    launch = AsyncMock(return_value=process)
    monkeypatch.setattr(
        "backend.app.adapters.git_repo.asyncio.create_subprocess_exec",
        launch,
    )
    adapter = GitRepoAdapter(
        "https://github.com/example/agent.git",
        "agent:run",
        docker_network="restricted-egress",
        env={"AGENT_TOKEN": "explicit-value"},
    )
    result = await adapter.run("case")
    call = launch.await_args
    assert call is not None
    args = call.args
    assert "--network" in args
    assert args[args.index("--network") + 1] == "restricted-egress"
    assert "--read-only" in args
    assert "--cap-drop" in args
    assert "--user" in args
    assert not any(arg.startswith("--volume") or arg == "-v" for arg in args)
    assert args.count("--env") == 13
    passed_env = [args[index + 1] for index, arg in enumerate(args[:-1]) if arg == "--env"]
    assert "AGENTPROBE_HOST_SECRET" not in passed_env
    assert "AGENT_TOKEN" in call.kwargs["env"]
    assert "AGENTPROBE_HOST_SECRET" not in call.kwargs["env"]
    assert result.final_answer == "done"
    assert result.steps[0].agent_name == "support"
    assert result.steps[0].tool_name == "lookup"


async def test_git_repo_adapter_preserves_crash_trace_and_redacts_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = {
        "__agentprobe_result": {
            "final_answer": "",
            "error": "RuntimeError: explicit-value was rejected",
        },
        "__agentprobe_trace": [
            {
                "kind": "tool",
                "tool_name": "lookup",
                "error": "RuntimeError: tool failed",
                "latency_ms": 1.0,
            }
        ],
    }
    process = SimpleNamespace(
        returncode=0,
        communicate=AsyncMock(return_value=(json.dumps(response).encode(), b"")),
    )
    monkeypatch.setattr(
        "backend.app.adapters.git_repo.asyncio.create_subprocess_exec",
        AsyncMock(return_value=process),
    )
    result = await GitRepoAdapter(
        "https://github.com/example/agent.git",
        "agent:run",
        docker_network="restricted-egress",
        env={"AGENT_TOKEN": "explicit-value"},
    ).run("case")
    assert result.error == "RuntimeError: [REDACTED] was rejected"
    assert result.steps[0].tool_name == "lookup"
    assert result.steps[0].error == "RuntimeError: tool failed"
