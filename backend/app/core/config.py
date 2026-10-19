"""Environment-only application settings."""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


def _get_env_file() -> str | None:
    if (
        "pytest" in sys.modules
        or "PYTEST_CURRENT_TEST" in os.environ
        or os.environ.get("AGENTPROBE_TESTING") == "1"
    ):
        return None
    return ".env"


class ModelPrice(BaseModel):
    """Configured input/output price in USD per one million tokens."""

    model_config = ConfigDict(extra="forbid")

    input_per_million_usd: float = Field(ge=0.0)
    output_per_million_usd: float = Field(ge=0.0)


class Settings(BaseSettings):
    """Settings loaded only from environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="AGENTPROBE_",
        env_file=_get_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    llm_provider: Literal["mock", "openai"] = "mock"
    model_name: str = "gpt-4o-mini"
    judge_model_name: str = "gpt-4o-mini"
    comparison_model: str = Field(
        default="",
        validation_alias=AliasChoices("COMPARISON_MODEL", "AGENTPROBE_COMPARISON_MODEL"),
    )
    openai_api_key: SecretStr = SecretStr("")
    openai_base_url: str | None = None
    gemini_api_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias=AliasChoices("GEMINI_API_KEY", "AGENTPROBE_GEMINI_API_KEY"),
    )
    primary_input_cost_per_million_usd: float | None = Field(
        default=None,
        ge=0.0,
    )
    primary_output_cost_per_million_usd: float | None = Field(
        default=None,
        ge=0.0,
    )
    comparison_input_cost_per_million_usd: float | None = Field(
        default=None,
        ge=0.0,
    )
    comparison_output_cost_per_million_usd: float | None = Field(
        default=None,
        ge=0.0,
    )
    model_prices: dict[str, ModelPrice] = Field(default_factory=dict)
    judge_timeout_seconds: float = Field(default=60.0, gt=0.0)
    database_url: str = "sqlite:///./agentprobe.db"
    api_key: SecretStr = SecretStr("")
    cors_origins: list[str] = Field(default_factory=list)
    api_rate_limit_per_minute: int = Field(default=60, ge=1)
    regression_tolerance: float = Field(default=0.05, ge=0.0, le=1.0)
    demo_bad_prompt: bool = False
    log_level: str = "INFO"
    temperature: float = 0.0
    seed: int | None = 17
    case_timeout_seconds: float = Field(default=30.0, gt=0.0)
    retries: int = Field(default=1, ge=0)
    concurrency: int = Field(default=4, ge=1)
    provider_call_delay_seconds: float = Field(default=1.0, ge=0.0, le=60.0)
    rate_limit_retry_base_seconds: float = Field(default=1.0, gt=0.0, le=60.0)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return cached settings parsed from the process environment."""
    return Settings()
