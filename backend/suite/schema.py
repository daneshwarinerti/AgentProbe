"""Pydantic schema for YAML evaluation cases."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ExpectedTool(BaseModel):
    """Expected tool name and argument values for a test case."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    args: dict[str, Any] = Field(default_factory=dict)
    match_modes: dict[str, Literal["exact", "contains", "ignore"]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_match_modes(self) -> ExpectedTool:
        unknown_args = set(self.match_modes) - set(self.args)
        if unknown_args:
            raise ValueError(
                f"match_modes contains arguments not declared in args: {sorted(unknown_args)!r}"
            )
        return self


class ForbiddenCondition(BaseModel):
    """One forbidden call or disclosure condition."""

    model_config = ConfigDict(extra="forbid")

    calls_tool: str | None = Field(default=None, min_length=1)
    reveals: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def validate_exactly_one_condition(self) -> ForbiddenCondition:
        if (self.calls_tool is None) == (self.reveals is None):
            raise ValueError("specify exactly one of 'calls_tool' or 'reveals'")
        return self


class ExpectedHandoff(BaseModel):
    """Expected handoff from one named agent to another."""

    model_config = ConfigDict(extra="forbid")

    from_agent: str = Field(min_length=1)
    to_agent: str = Field(min_length=1)


TextNormOption = Literal["nfkc", "whitespace", "case"]


def _default_text_normalization() -> list[TextNormOption]:
    return ["nfkc", "whitespace", "case"]


class TestCase(BaseModel):
    """A single structured input and its expected/forbidden behaviors."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128)
    input: str = Field(min_length=1)
    expected_tools: list[ExpectedTool] = Field(default_factory=list)
    expected_answer_contains: list[str] = Field(default_factory=list)
    forbidden: list[ForbiddenCondition] = Field(default_factory=list)
    expected_agents: list[str] = Field(default_factory=list)
    expected_handoffs: list[ExpectedHandoff] = Field(default_factory=list)
    expected_first_failing_agent: str | None = None
    target_agent: str | None = None
    max_steps: int = Field(default=5, ge=1)
    category: str = Field(default="general", min_length=1)
    tags: list[str] = Field(default_factory=list)
    text_normalization: list[TextNormOption] = Field(default_factory=_default_text_normalization)
    tool_match_mode: Literal["exact", "subsequence"] = "exact"
    allowed_extra_read_tools: list[str] = Field(default_factory=list)
    seeded_sensitive_values: list[str] = Field(default_factory=list)
