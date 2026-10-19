"""Tests for structured case validation."""

import pytest
from pydantic import ValidationError

from backend.suite.schema import ForbiddenCondition
from backend.suite.schema import TestCase as CaseDefinition


def test_case_defaults_and_fields() -> None:
    case = CaseDefinition(id="simple", input="hello")
    assert case.expected_tools == []
    assert case.expected_answer_contains == []
    assert case.max_steps == 5
    assert case.category == "general"


@pytest.mark.parametrize(
    "payload",
    [{"calls_tool": "delete_order"}, {"reveals": "person@example.test"}],
)
def test_forbidden_condition_accepts_either_shape(payload: dict[str, str]) -> None:
    assert ForbiddenCondition.model_validate(payload)


@pytest.mark.parametrize(
    "payload",
    [{}, {"calls_tool": "delete_order", "reveals": "private"}],
)
def test_forbidden_condition_requires_exactly_one_shape(payload: dict[str, str]) -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        ForbiddenCondition.model_validate(payload)


def test_case_rejects_zero_max_steps_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        CaseDefinition.model_validate({"id": "bad", "input": "hello", "max_steps": 0})
    with pytest.raises(ValidationError):
        CaseDefinition.model_validate({"id": "bad", "input": "hello", "unsupported": True})


def test_case_supports_target_agent_and_argument_match_modes() -> None:
    case = CaseDefinition.model_validate(
        {
            "id": "targeted-case",
            "input": "check order",
            "target_agent": "support",
            "expected_tools": [
                {
                    "name": "get_order_status",
                    "args": {"order_id": "ORD-1001"},
                    "match_modes": {"order_id": "contains"},
                }
            ],
        }
    )

    assert case.target_agent == "support"
    assert case.expected_tools[0].match_modes == {"order_id": "contains"}


def test_expected_tool_rejects_match_mode_for_undeclared_argument() -> None:
    with pytest.raises(ValidationError, match="not declared in args"):
        CaseDefinition.model_validate(
            {
                "id": "bad-match-mode",
                "input": "check order",
                "expected_tools": [
                    {
                        "name": "get_order_status",
                        "args": {"order_id": "ORD-1001"},
                        "match_modes": {"other": "ignore"},
                    }
                ],
            }
        )
