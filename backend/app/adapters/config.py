"""Load the discriminated agent target from an agentprobe.yaml file."""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    HttpUrl,
    ValidationError,
    field_validator,
    model_validator,
)

from backend.app.adapters.base import AgentAdapter
from backend.app.adapters.git_repo import GitRepoAdapter
from backend.app.adapters.http import HttpAdapter
from backend.app.adapters.python_module import PythonModuleAdapter

_ENV_PATTERN = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


class _TargetBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeout_seconds: float = Field(default=30.0, gt=0.0)
    env: dict[str, str] = Field(default_factory=dict)


class PythonModuleTarget(_TargetBase):
    type: Literal["python_module"]
    project_path: str
    entry_point: str
    python_executable: str | None = None


class HttpTarget(_TargetBase):
    type: Literal["http"]
    url: HttpUrl
    request_mapping: dict[str, str] = Field(default_factory=lambda: {"input": "case_input"})
    response_mapping: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    auth_header: str | None = None
    auth_env: str | None = None

    @field_validator("url")
    @classmethod
    def reject_url_credentials(cls, value: HttpUrl) -> HttpUrl:
        if value.username is not None or value.password is not None:
            raise ValueError("HTTP target URL must not embed credentials; use auth_env.")
        return value

    @model_validator(mode="after")
    def validate_auth_pair(self) -> HttpTarget:
        if bool(self.auth_header) != bool(self.auth_env):
            raise ValueError("configure both 'auth_header' and 'auth_env', or neither.")
        return self


class GitRepoTarget(_TargetBase):
    type: Literal["git_repo"]
    repo_url: str
    delegate_type: Literal["python_module", "http"] = "python_module"
    entry_point: str | None = None
    docker_network: str | None = None
    docker_image: str = "agentprobe-sandbox:latest"
    docker_executable: str = "docker"
    timeout_seconds: float = Field(default=300.0, gt=0.0)
    startup_timeout_seconds: float = Field(default=30.0, gt=0.0)
    service_command: list[str] = Field(default_factory=list)
    service_url: str = "http://127.0.0.1:8000/run"
    request_mapping: dict[str, str] = Field(default_factory=lambda: {"input": "case_input"})
    response_mapping: dict[str, str] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    auth_header: str | None = None
    auth_env: str | None = None
    http_timeout_seconds: float = Field(default=30.0, gt=0.0)

    @model_validator(mode="after")
    def validate_delegate(self) -> GitRepoTarget:
        if self.delegate_type == "http" and not self.service_command:
            raise ValueError("Git HTTP delegate requires a service_command argument list.")
        if bool(self.auth_header) != bool(self.auth_env):
            raise ValueError("configure both 'auth_header' and 'auth_env', or neither.")
        return self


AgentTarget = Annotated[
    PythonModuleTarget | HttpTarget | GitRepoTarget,
    Field(discriminator="type"),
]


class AgentConfig(BaseModel):
    """Validated top-level agentprobe YAML configuration."""

    model_config = ConfigDict(extra="forbid")

    target: AgentTarget


class AgentConfigError(ValueError):
    """Invalid configuration or unavailable environment interpolation."""


def load_agent_config(path: Path) -> AgentConfig:
    """Read and validate one YAML config, resolving only explicit env references."""
    try:
        with path.open("r", encoding="utf-8") as stream:
            raw = yaml.safe_load(stream)
        expanded = _expand_environment(raw)
        return AgentConfig.model_validate(expanded)
    except (OSError, yaml.YAMLError, ValidationError, AgentConfigError) as exc:
        raise AgentConfigError(f"{path}: invalid agent configuration: {exc}") from exc


def make_adapter(config: AgentConfig, *, config_path: Path) -> AgentAdapter:
    """Construct the configured adapter; relative project paths use config location."""
    target = config.target
    if isinstance(target, PythonModuleTarget):
        project = Path(target.project_path)
        if not project.is_absolute():
            project = (config_path.resolve().parent / project).resolve()
        python_executable = (
            (project / target.python_executable).resolve()
            if target.python_executable and not Path(target.python_executable).is_absolute()
            else Path(target.python_executable)
            if target.python_executable
            else None
        )
        return PythonModuleAdapter(
            project,
            target.entry_point,
            timeout_seconds=target.timeout_seconds,
            env=target.env,
            python_executable=python_executable,
        )
    if isinstance(target, HttpTarget):
        auth_value = _environment_secret(target.auth_env) if target.auth_env else None
        mapping = target.response_mapping or {"final_answer": "final_answer"}
        return HttpAdapter(
            str(target.url),
            request_mapping=target.request_mapping,
            response_mapping=mapping,
            timeout_seconds=target.timeout_seconds,
            headers=target.headers,
            auth_header=target.auth_header,
            auth_value=auth_value,
        )
    auth_value = _environment_secret(target.auth_env) if target.auth_env else None
    return GitRepoAdapter(
        target.repo_url,
        target.entry_point,
        docker_network=target.docker_network,
        delegate_type=target.delegate_type,
        docker_image=target.docker_image,
        timeout_seconds=target.timeout_seconds,
        startup_timeout_seconds=target.startup_timeout_seconds,
        env=target.env,
        service_command=target.service_command,
        service_url=target.service_url,
        request_mapping=target.request_mapping,
        response_mapping=target.response_mapping or {"final_answer": "final_answer"},
        headers=target.headers,
        auth_header=target.auth_header,
        auth_value=auth_value,
        http_timeout_seconds=target.http_timeout_seconds,
        docker_executable=target.docker_executable,
    )


def _expand_environment(value: object) -> object:
    if isinstance(value, dict):
        return {key: _expand_environment(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_expand_environment(item) for item in value]
    if isinstance(value, str):
        match = _ENV_PATTERN.fullmatch(value)
        if match:
            variable = match.group(1)
            resolved = os.environ.get(variable)
            if resolved is None:
                raise AgentConfigError(f"Environment variable {variable} is not set.")
            return resolved
    return value


def _environment_secret(variable: str) -> str:
    value = os.environ.get(variable)
    if value is None:
        raise AgentConfigError(f"HTTP auth environment variable {variable} is not set.")
    return value
