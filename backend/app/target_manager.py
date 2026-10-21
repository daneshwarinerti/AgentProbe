"""Local project upload, inspection, and isolated Python execution helpers."""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, BinaryIO, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.app.adapters.python_module import PythonModuleAdapter
from backend.app.runner.models import AgentResult

MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_EXTRACTED_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_FILES = 10_000
_ENTRY_POINT = re.compile(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*$")
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SKIPPED_DIRECTORIES = {".git", ".venv", "venv", "__pycache__", "node_modules"}


class InputMapping(BaseModel):
    """Map a string or structured test input to a Python function signature."""

    model_config = ConfigDict(extra="forbid")

    mode: str = Field(default="text", pattern=r"^(text|kwargs|document_path)$")
    parameter: str | None = Field(default=None, pattern=r"^[A-Za-z_]\w*$")
    fields: dict[str, str] = Field(default_factory=dict)

    @field_validator("fields")
    @classmethod
    def validate_fields(cls, value: dict[str, str]) -> dict[str, str]:
        for name, source in value.items():
            if not _ENV_NAME.fullmatch(name):
                raise ValueError(f"Invalid Python argument name: {name!r}.")
            if not source or any(not _ENV_NAME.fullmatch(part) for part in source.split(".")):
                raise ValueError(f"Invalid input source path: {source!r}.")
        return value

    @field_validator("parameter")
    @classmethod
    def validate_parameter_name(cls, value: str | None) -> str | None:
        if value is not None and not _ENV_NAME.fullmatch(value):
            raise ValueError("Input parameter must be a valid Python identifier.")
        return value

    def validate_mapping(self) -> InputMapping:
        if self.mode == "kwargs" and not self.fields:
            raise ValueError("Keyword input mapping requires at least one field.")
        if self.mode == "kwargs" and self.parameter is not None:
            raise ValueError("Input mapping parameter is not valid in kwargs mode.")
        if self.mode == "document_path" and not self.parameter:
            raise ValueError("Document-path input mapping requires a parameter name.")
        if self.mode != "kwargs" and self.fields:
            raise ValueError("Input mapping fields are only valid in kwargs mode.")
        return self


class AgentCandidate(BaseModel):
    """A non-authoritative agent name suggestion from static source inspection."""

    model_config = ConfigDict(extra="forbid")

    name: str
    framework: str
    confidence: Literal["low", "medium"] = "low"
    evidence: list[str] = Field(default_factory=list)


class InspectionResult(BaseModel):
    """Framework evidence and candidate names found in uploaded Python files."""

    model_config = ConfigDict(extra="forbid")

    frameworks: list[str]
    candidates: list[AgentCandidate]
    notice: str = "Static heuristics only; verify candidates and entry points manually."


def validate_entry_point(value: str) -> str:
    """Validate a dotted Python module and function entry point."""
    if not _ENTRY_POINT.fullmatch(value):
        raise ValueError("Entry point must use the 'package.module:function' format.")
    return value


def validate_target_environment(values: dict[str, str]) -> dict[str, str]:
    """Validate environment names and reserve the AgentProbe trace-file variable."""
    for name, value in values.items():
        if not _ENV_NAME.fullmatch(name):
            raise ValueError(f"Invalid environment variable name: {name!r}.")
        if name.upper() == "AGENTPROBE_TRACE_FILE":
            raise ValueError(
                "Environment variable AGENTPROBE_TRACE_FILE is reserved by AgentProbe."
            )
        if not isinstance(value, str) or "\x00" in value:
            raise ValueError(f"Environment value for {name!r} must be a string without NUL.")
    return values


def extract_project_archive(archive: BinaryIO, targets_root: Path, target_id: str) -> Path:
    """Safely extract an uploaded zip into a fresh target directory."""
    try:
        target_id = str(UUID(target_id))
    except ValueError as exc:
        raise ValueError("Target id must be a UUID.") from exc
    targets_root.parent.mkdir(parents=True, exist_ok=True)
    if targets_root.is_symlink() or targets_root.resolve().parent != targets_root.parent.resolve():
        raise ValueError("Targets directory must not be a linked directory.")
    targets_root.mkdir(parents=True, exist_ok=True)
    destination = targets_root / target_id
    if destination.exists():
        raise ValueError("Target directory already exists.")

    with tempfile.TemporaryDirectory(prefix="upload-", dir=targets_root) as temporary:
        temporary_root = Path(temporary)
        archive_path = temporary_root / "upload.zip"
        size = 0
        with archive_path.open("wb") as output:
            while chunk := archive.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_ARCHIVE_BYTES:
                    raise ValueError("Uploaded zip exceeds the 100 MB limit.")
                output.write(chunk)

        try:
            with zipfile.ZipFile(archive_path) as zipped:
                entries = _validate_zip_entries(zipped.infolist())
                extracted_bytes = sum(info.file_size for info, _, skip in entries if not skip)
                if extracted_bytes > MAX_EXTRACTED_BYTES:
                    raise ValueError("Extracted project exceeds the 100 MB limit.")
                file_count = sum(not info.is_dir() for info, _, _ in entries)
                if file_count > MAX_ARCHIVE_FILES:
                    raise ValueError(f"Zip contains more than {MAX_ARCHIVE_FILES} files.")
                content_root = temporary_root / "project"
                content_root.mkdir()
                prefix = _common_root(entries)
                actual_extracted_bytes = 0
                for info, relative_path, skip in entries:
                    if skip or info.is_dir():
                        continue
                    path = relative_path.parts[1:] if prefix else relative_path.parts
                    if not path:
                        continue
                    destination_path = content_root.joinpath(*path)
                    resolved = destination_path.resolve()
                    if not resolved.is_relative_to(content_root.resolve()):
                        raise ValueError("Zip entry resolves outside the extraction directory.")
                    destination_path.parent.mkdir(parents=True, exist_ok=True)
                    with zipped.open(info) as source, destination_path.open("xb") as output:
                        while chunk := source.read(1024 * 1024):
                            actual_extracted_bytes += len(chunk)
                            if actual_extracted_bytes > MAX_EXTRACTED_BYTES:
                                raise ValueError("Extracted project exceeds the 100 MB limit.")
                            output.write(chunk)
        except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
            raise ValueError(f"Unable to extract uploaded zip: {type(exc).__name__}.") from exc

        if not any(content_root.iterdir()):
            raise ValueError("The zip contains no project files after unsafe files are skipped.")
        content_root.replace(destination)
    return destination


def _validate_zip_entries(
    entries: list[zipfile.ZipInfo],
) -> list[tuple[zipfile.ZipInfo, PurePosixPath, bool]]:
    if len(entries) > MAX_ARCHIVE_FILES * 2:
        raise ValueError(f"Zip contains more than {MAX_ARCHIVE_FILES * 2} entries.")
    validated: list[tuple[zipfile.ZipInfo, PurePosixPath, bool]] = []
    total_bytes = 0
    file_count = 0
    for info in entries:
        normalized = info.filename.replace("\\", "/")
        windows_path = PureWindowsPath(normalized)
        path = PurePosixPath(normalized)
        if (
            not normalized
            or normalized.startswith("/")
            or windows_path.is_absolute()
            or windows_path.drive
            or not path.parts
            or any(
                part in {"", ".", ".."} or "\x00" in part or part.endswith((".", " "))
                for part in path.parts
            )
            or any(
                ":" in part
                or re.search(r'[<>:"|?*]', part)
                or re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", part, re.I)
                for part in path.parts
            )
        ):
            raise ValueError(f"Unsafe zip path rejected: {info.filename!r}.")
        mode = info.external_attr >> 16
        if stat.S_ISLNK(mode):
            raise ValueError(f"Zip symlink rejected: {info.filename!r}.")
        file_type = stat.S_IFMT(mode)
        if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
            raise ValueError(f"Unsupported special file rejected: {info.filename!r}.")
        if info.flag_bits & 0x1:
            raise ValueError("Encrypted zip entries are not supported.")
        if not info.is_dir():
            file_count += 1
            total_bytes += info.file_size
            if file_count > MAX_ARCHIVE_FILES:
                raise ValueError(f"Zip contains more than {MAX_ARCHIVE_FILES} files.")
            if total_bytes > MAX_EXTRACTED_BYTES:
                raise ValueError("Extracted project exceeds the 100 MB limit.")
        validated.append((info, path, _is_skipped_path(path)))
    return validated


def _common_root(entries: list[tuple[zipfile.ZipInfo, PurePosixPath, bool]]) -> bool:
    files = [path for info, path, skip in entries if not skip and not info.is_dir()]
    if not files or any(len(path.parts) < 2 for path in files):
        return False
    first = {path.parts[0] for path in files}
    return len(first) == 1 and all(path.parts[0] in first for _, path, skip in entries if not skip)


def _is_skipped_path(path: PurePosixPath) -> bool:
    if any(part.lower() in _SKIPPED_DIRECTORIES for part in path.parts):
        return True
    if any(
        part.lower() == ".env"
        or part.lower().startswith(".env.")
        or any(word in part.lower() for word in ("secret", "credential", "id_rsa", "id_ed25519"))
        for part in path.parts
    ):
        return True
    name = path.name.lower()
    if name == ".env" or name.startswith(".env."):
        return True
    if name.endswith((".pem", ".key", ".p12", ".pfx")):
        return True
    return any(word in name for word in ("secret", "credential", "id_rsa", "id_ed25519"))


def inspect_project(project_path: Path) -> InspectionResult:
    """Find framework hints and likely agent symbols without importing target code."""
    patterns = {
        "LangGraph": ("langgraph", "StateGraph", "create_react_agent"),
        "Google ADK": ("google.adk", "LlmAgent", "SequentialAgent"),
        "CrewAI": ("crewai", "Crew("),
        "AutoGen": ("autogen", "AssistantAgent", "ConversableAgent"),
    }
    frameworks: set[str] = set()
    candidates: dict[tuple[str, str], set[str]] = {}
    assignment = re.compile(
        r"(?m)^\s*([A-Za-z_]\w*)\s*=\s*(?:[\w.]+\.)?"
        r"(StateGraph|create_react_agent|LlmAgent|SequentialAgent|Crew|"
        r"AssistantAgent|ConversableAgent|Agent)\s*\("
    )
    decorated_agent = re.compile(r"""@traced_agent\s*\(\s*name\s*=\s*["']([^"']+)""")
    for source in project_path.rglob("*.py"):
        if _is_skipped_path(PurePosixPath(source.relative_to(project_path).as_posix())):
            continue
        try:
            text = source.read_text(encoding="utf-8", errors="replace")[:1_000_000]
        except OSError:
            continue
        matched_frameworks = {
            name for name, hints in patterns.items() if any(hint in text for hint in hints)
        }
        frameworks.update(matched_frameworks)
        for match in assignment.finditer(text):
            constructor = match.group(2)
            framework = _framework_for_constructor(constructor, matched_frameworks)
            if framework:
                line = text.count(chr(10), 0, match.start()) + 1
                candidates.setdefault((match.group(1), framework), set()).add(
                    f"{source.relative_to(project_path)}:{line}"
                )
        for match in decorated_agent.finditer(text):
            candidates.setdefault((match.group(1), "AgentProbe SDK"), set()).add(
                f"{source.relative_to(project_path)}:{text.count(chr(10), 0, match.start()) + 1}"
            )
    return InspectionResult(
        frameworks=sorted(frameworks),
        candidates=[
            AgentCandidate(name=name, framework=framework, evidence=sorted(evidence))
            for (name, framework), evidence in sorted(candidates.items())
        ],
    )


def _framework_for_constructor(constructor: str, frameworks: set[str]) -> str | None:
    associated = {
        "StateGraph": "LangGraph",
        "create_react_agent": "LangGraph",
        "LlmAgent": "Google ADK",
        "SequentialAgent": "Google ADK",
        "Crew": "CrewAI",
        "AssistantAgent": "AutoGen",
        "ConversableAgent": "AutoGen",
    }
    framework = associated.get(constructor)
    if framework in frameworks:
        return framework
    if constructor == "Agent" and len(frameworks) == 1:
        return next(iter(frameworks))
    return None


async def prepare_target_environment(
    project_path: Path, venv_path: Path, *, timeout_seconds: float = 600.0
) -> tuple[Path, str | None]:
    """Create an isolated venv and install declared project dependencies."""
    python = _venv_python(venv_path)
    venv_parent = venv_path.parent
    venv_parent.mkdir(parents=True, exist_ok=True)
    if (
        venv_path.is_symlink()
        or venv_parent.is_symlink()
        or python.is_symlink()
        or venv_parent.resolve().parent != venv_parent.parent.resolve()
        or venv_path.resolve().parent != venv_parent.resolve()
    ):
        return python, "Target virtual environment path must not be a linked directory."
    if python.is_file():
        return python, None
    environment = _subprocess_environment()
    # Prefer uv to create the target venv: it bundles its own pip and does not
    # depend on the system ensurepip (which is absent or broken on many installs).
    # Fall back to python -m venv if uv is not on PATH.
    uv_exe = shutil.which("uv")
    if uv_exe:
        create_venv_cmd = [uv_exe, "venv", str(venv_path), "--seed", "--quiet"]
    else:
        system_python = shutil.which("python") or shutil.which("python3") or sys.executable
        create_venv_cmd = [system_python, "-m", "venv", str(venv_path)]
    commands = [create_venv_cmd]
    requirements = project_path / "requirements.txt"
    project_metadata = project_path / "pyproject.toml"
    if requirements.is_file():
        commands.append([str(python), "-m", "pip", "install", "-r", str(requirements)])
        if project_metadata.is_file():
            commands.append([str(python), "-m", "pip", "install", "--no-deps", str(project_path)])
    elif project_metadata.is_file():
        commands.append([str(python), "-m", "pip", "install", str(project_path)])
    def _run_cmd_sync(cmd: list[str], cwd: Path, env: dict[str, str], to: float) -> tuple[int, str]:
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            env=env,
            timeout=to,
            check=False,
        )
        err = (proc.stderr or proc.stdout or "").strip()
        return proc.returncode, err

    for command in commands:
        is_venv_cmd = command is create_venv_cmd or command[0] == create_venv_cmd[0]
        cwd = venv_path.parent if is_venv_cmd else project_path
        try:
            returncode, err = await asyncio.to_thread(
                _run_cmd_sync, command, cwd, environment, timeout_seconds
            )
        except subprocess.TimeoutExpired:
            cleanup_error = _remove_failed_venv(venv_path)
            if cleanup_error:
                return python, cleanup_error
            return python, "Dependency setup timed out."
        except OSError:
            cleanup_error = _remove_failed_venv(venv_path)
            if cleanup_error:
                return python, cleanup_error
            return python, "Unable to start the isolated Python environment."
        if returncode != 0:
            cleanup_error = _remove_failed_venv(venv_path)
            if cleanup_error:
                return python, cleanup_error
            err_msg = f": {err}" if err else ""
            return (
                python,
                f"Dependency installation failed{err_msg}. "
                "Check requirements.txt or pyproject.toml.",
            )
    return python, None


async def dry_run_target(
    *,
    project_path: Path,
    venv_path: Path,
    entry_point: str,
    env: dict[str, str],
    input_mapping: InputMapping,
    output_mapping: dict[str, str],
    sample_input: str,
    timeout_seconds: float,
) -> AgentResult:
    """Prepare and execute one target input in a separate Python process."""
    python, setup_error = await prepare_target_environment(project_path, venv_path)
    if setup_error:
        return AgentResult(final_answer="", steps=[], error=setup_error)
    adapter = PythonModuleAdapter(
        project_path,
        entry_point,
        timeout_seconds=timeout_seconds,
        env=env,
        python_executable=python,
        input_mapping=input_mapping.model_copy().validate_mapping().model_dump(),
        output_mapping=output_mapping,
    )
    return await adapter.run(sample_input)


def redact_target_result(result: AgentResult, secret_values: Iterable[str]) -> AgentResult:
    """Ensure configured target environment values never appear in dry-run responses."""
    values = [value for value in secret_values if isinstance(value, str) and value]

    def redact(value: Any) -> Any:
        if isinstance(value, str):
            return redact_target_text(value, values)
        if isinstance(value, list):
            return [redact(item) for item in value]
        if isinstance(value, dict):
            return {redact(key): redact(item) for key, item in value.items()}
        return value

    return AgentResult.model_validate(redact(result.model_dump(mode="json")))


def redact_target_text(text: str, secret_values: Iterable[str]) -> str:
    """Replace configured environment values from any public target response text."""
    for secret in secret_values:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text


def _venv_python(venv_path: Path) -> Path:
    return venv_path / "Scripts" / "python.exe" if os.name == "nt" else venv_path / "bin" / "python"


def _remove_failed_venv(venv_path: Path) -> str | None:
    try:
        shutil.rmtree(venv_path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        return (
            "Dependency setup failed and temporary environment cleanup was incomplete "
            f"({type(exc).__name__})."
        )
    return None


def _subprocess_environment() -> dict[str, str]:
    allowed = (
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "TEMP",
        "TMP",
        "PATHEXT",
        "HOME",
        "USERPROFILE",
        "APPDATA",
        "LOCALAPPDATA",
    )
    return {
        **{key: os.environ[key] for key in allowed if key in os.environ},
        "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        "PIP_NO_INPUT": "1",
    }
