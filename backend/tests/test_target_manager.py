"""Safety and behavior tests for uploaded local targets."""

import io
import stat
import zipfile
from pathlib import Path

import pytest

from backend.app.adapters.python_module import PythonModuleAdapter
from backend.app.runner.models import AgentResult, TraceStep
from backend.app.target_manager import (
    InputMapping,
    dry_run_target,
    extract_project_archive,
    inspect_project,
    redact_target_result,
    validate_target_environment,
)


def _zip_bytes(files: dict[str, str]) -> io.BytesIO:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zipped:
        for name, content in files.items():
            zipped.writestr(name, content)
    output.seek(0)
    return output


def test_extract_archive_strips_common_root_and_skips_sensitive_files(tmp_path: Path) -> None:
    archive = _zip_bytes(
        {
            "customer-agent/agent.py": "def run(value): return value",
            "customer-agent/.env": "PASSWORD=private",
            "customer-agent/credentials-prod.json": "{}",
            "customer-agent/cert.pem": "private",
            "customer-agent/.venv/ignored.py": "ignored",
        }
    )

    project = extract_project_archive(
        archive, tmp_path / "targets", "00000000-0000-0000-0000-000000000001"
    )

    assert (project / "agent.py").is_file()
    assert not (project / ".env").exists()
    assert not (project / "credentials-prod.json").exists()
    assert not (project / "cert.pem").exists()
    assert not (project / ".venv").exists()


@pytest.mark.parametrize("name", ["../escape.py", "C:/escape.py", "folder\\..\\escape.py"])
def test_extract_archive_rejects_zip_slip_paths(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="Unsafe zip path"):
        extract_project_archive(
            _zip_bytes({name: "unsafe"}),
            tmp_path / "targets",
            "00000000-0000-0000-0000-000000000001",
        )


def test_extract_archive_rejects_symlinks(tmp_path: Path) -> None:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as zipped:
        link = zipfile.ZipInfo("project/link.py")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zipped.writestr(link, "../../outside.py")
    output.seek(0)

    with pytest.raises(ValueError, match="symlink rejected"):
        extract_project_archive(
            output, tmp_path / "targets", "00000000-0000-0000-0000-000000000001"
        )


def test_extract_archive_enforces_archive_size_file_count_and_expansion_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("backend.app.target_manager.MAX_ARCHIVE_FILES", 1)
    with pytest.raises(ValueError, match="more than 1 files"):
        extract_project_archive(
            _zip_bytes({"first.py": "1", "second.py": "2"}),
            tmp_path / "files",
            "00000000-0000-0000-0000-000000000001",
        )

    monkeypatch.setattr("backend.app.target_manager.MAX_ARCHIVE_FILES", 10)
    monkeypatch.setattr("backend.app.target_manager.MAX_EXTRACTED_BYTES", 4)
    with pytest.raises(ValueError, match="100 MB limit"):
        extract_project_archive(
            _zip_bytes({"large.py": "12345"}),
            tmp_path / "expanded",
            "00000000-0000-0000-0000-000000000002",
        )

    monkeypatch.setattr("backend.app.target_manager.MAX_ARCHIVE_BYTES", 10)
    with pytest.raises(ValueError, match="Uploaded zip exceeds"):
        extract_project_archive(
            _zip_bytes({"file.py": "content"}),
            tmp_path / "archive",
            "00000000-0000-0000-0000-000000000003",
        )


def test_inspection_uses_static_heuristics_without_importing_target(tmp_path: Path) -> None:
    (tmp_path / "agent.py").write_text(
        "from langgraph.graph import StateGraph\n"
        "workflow = StateGraph(dict)\n"
        "raise RuntimeError('must not import')\n",
        encoding="utf-8",
    )

    result = inspect_project(tmp_path)

    assert result.frameworks == ["LangGraph"]
    assert result.candidates[0].name == "workflow"
    assert result.candidates[0].confidence == "low"
    assert "heuristics" in result.notice


def test_redaction_removes_configured_values_from_trace_fields() -> None:
    result = AgentResult(
        final_answer="token-value",
        steps=[
            TraceStep(
                kind="tool",
                tool_name="send",
                args={"authorization": "token-value"},
                result={"message": "token-value"},
                latency_ms=0.0,
            )
        ],
    )

    redacted = redact_target_result(result, ["token-value"])

    assert redacted.final_answer == "[REDACTED]"
    assert redacted.steps[0].args == {"authorization": "[REDACTED]"}
    assert redacted.steps[0].result == {"message": "[REDACTED]"}


def test_target_environment_allows_project_keys_but_reserves_trace_path() -> None:
    assert validate_target_environment({"AGENTPROBE_API_KEY": "project-key"}) == {
        "AGENTPROBE_API_KEY": "project-key"
    }
    with pytest.raises(ValueError, match="AGENTPROBE_TRACE_FILE"):
        validate_target_environment({"agentprobe_trace_file": "outside.jsonl"})


async def test_uploaded_target_uses_private_venv_mapping_and_sdk_tracing(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    (project / "agent.py").write_text(
        "from agentprobe_sdk import traced_agent\n"
        "@traced_agent(name='document helper')\n"
        "def run(query):\n"
        "    return {'answer': {'text': 'received: ' + query}}\n",
        encoding="utf-8",
    )
    mapping = InputMapping(mode="kwargs", fields={"query": "text"})

    result = await dry_run_target(
        project_path=project,
        venv_path=tmp_path / "targets" / ".venvs" / "target-one",
        entry_point="agent:run",
        env={"TOKEN": "private-value"},
        input_mapping=mapping,
        output_mapping={"final_answer": "answer.text"},
        sample_input='{"text":"hello"}',
        timeout_seconds=5,
    )

    assert result.error is None
    assert result.final_answer == "received: hello"
    assert any(step.agent_name == "document helper" for step in result.steps)
    assert (
        tmp_path / "targets" / ".venvs" / "target-one" / "Scripts" / "python.exe"
    ).is_file() or (tmp_path / "targets" / ".venvs" / "target-one" / "bin" / "python").is_file()


async def test_python_adapter_maps_document_path_and_rejects_outside_paths(
    tmp_path: Path,
) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "faq.txt").write_text("Local return policy", encoding="utf-8")
    (tmp_path / "agent.py").write_text(
        "from pathlib import Path\n"
        "def run(document): return Path(document).read_text(encoding='utf-8')\n",
        encoding="utf-8",
    )
    adapter = PythonModuleAdapter(
        tmp_path,
        "agent:run",
        input_mapping={"mode": "document_path", "parameter": "document"},
    )

    answer = await adapter.run("docs/faq.txt")
    rejected = await adapter.run("../outside.txt")

    assert answer.final_answer == "Local return policy"
    assert rejected.error is not None
