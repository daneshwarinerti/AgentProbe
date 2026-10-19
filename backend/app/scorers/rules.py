"""Deterministic rule-based evaluation of one case result."""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict

from backend.app.runner.models import TraceStep
from backend.app.scorers.agent_routing import score_case_routing
from backend.app.scorers.taxonomy import FailureCategory
from backend.suite.schema import TestCase

_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)

CheckName = Literal[
    "execution_successful",
    "correct_tool",
    "correct_args",
    "forbidden_behavior",
    "no_repeated_calls",
    "max_steps",
    "answer_contains_expected_terms",
    "agent_routing",
]


class RuleCheck(BaseModel):
    """One named deterministic evaluation check."""

    model_config = ConfigDict(extra="forbid")

    name: CheckName
    passed: bool
    reason: str


class RuleScore(BaseModel):
    """Rule-check outcomes and normalized failure categories."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    checks: list[RuleCheck]
    failure_categories: list[FailureCategory]


def _is_read_only_tool(name: str | None, allowed_list: list[str]) -> bool:
    if not name:
        return False
    if name in allowed_list:
        return True
    return name.startswith(("search_", "get_", "lookup_", "check_", "read_", "list_", "fetch_"))


def score_case_rules(
    case: TestCase,
    *,
    final_answer: str | None,
    steps: list[TraceStep],
    execution_error: str | None = None,
) -> RuleScore:
    """Compare observed answer and trace with the expectations declared by a case."""
    tool_steps = [step for step in steps if step.kind == "tool"]
    observed_tools = [step.tool_name for step in tool_steps]
    expected_tools = [expected.name for expected in case.expected_tools]

    # Evaluate tool sequence
    if case.tool_match_mode == "subsequence":
        matched_indices: list[int] = []
        exp_idx = 0
        disallowed_extra_tools: list[str] = []
        for obs_idx, step in enumerate(tool_steps):
            if exp_idx < len(expected_tools) and step.tool_name == expected_tools[exp_idx]:
                matched_indices.append(obs_idx)
                exp_idx += 1
            else:
                if not _is_read_only_tool(step.tool_name, case.allowed_extra_read_tools):
                    disallowed_extra_tools.append(step.tool_name or "unknown")

        tool_sequence_matched = (exp_idx == len(expected_tools)) and not disallowed_extra_tools
        if tool_sequence_matched:
            tool_reason = (
                f"Required tool subsequence {expected_tools!r} matched with allowable lookups."
            )
        elif exp_idx < len(expected_tools):
            tool_reason = (
                f"Expected tools {expected_tools!r} not found as subsequence in {observed_tools!r}."
            )
        else:
            tool_reason = f"Observed extra non-read-only tool calls: {disallowed_extra_tools!r}."
        matched_tool_steps = [tool_steps[idx] for idx in matched_indices]
    else:
        tool_sequence_matched = observed_tools == expected_tools
        tool_reason = (
            "Tool names and order match expectations."
            if tool_sequence_matched
            else f"Expected tools {expected_tools!r}; observed {observed_tools!r}."
        )
        matched_tool_steps = tool_steps

    checks = [
        RuleCheck(
            name="execution_successful",
            passed=execution_error is None,
            reason=execution_error or "Agent execution completed.",
        ),
        RuleCheck(
            name="correct_tool",
            passed=tool_sequence_matched,
            reason=tool_reason,
        ),
    ]

    args_match = not tool_sequence_matched or all(
        _tool_args_match(
            step.tool_name,
            step.args,
            expected.args,
            expected.match_modes,
        )
        for step, expected in zip(matched_tool_steps, case.expected_tools, strict=True)
    )
    checks.append(
        RuleCheck(
            name="correct_args",
            passed=args_match,
            reason=(
                "Tool arguments match expectations."
                if args_match
                else (
                    "Tool arguments were not assessed because observed tool sequence differs."
                    if not tool_sequence_matched
                    else "One or more tool argument mappings differ materially from expectations."
                )
            ),
        )
    )

    violations: list[str] = []
    for condition in case.forbidden:
        if condition.calls_tool is not None and condition.calls_tool in observed_tools:
            violations.append(f"forbidden tool {condition.calls_tool!r} was called")
        if (
            condition.reveals is not None
            and final_answer is not None
            and condition.reveals.casefold() in final_answer.casefold()
        ):
            violations.append(f"forbidden content {condition.reveals!r} was revealed")
    checks.append(
        RuleCheck(
            name="forbidden_behavior",
            passed=not violations,
            reason="; ".join(violations) if violations else "No forbidden behavior observed.",
        )
    )

    seen_calls: set[str] = set()
    repeated_calls: list[str] = []
    for step in tool_steps:
        signature = json.dumps(
            {"tool_name": step.tool_name, "args": step.args},
            sort_keys=True,
            ensure_ascii=True,
            default=str,
        )
        if signature in seen_calls:
            repeated_calls.append(signature)
        seen_calls.add(signature)
    checks.append(
        RuleCheck(
            name="no_repeated_calls",
            passed=not repeated_calls,
            reason=(
                "No identical tool call was repeated."
                if not repeated_calls
                else f"Found {len(repeated_calls)} repeated identical tool call(s)."
            ),
        )
    )

    tool_call_count = len(tool_steps)
    checks.append(
        RuleCheck(
            name="max_steps",
            passed=tool_call_count <= case.max_steps,
            reason=f"Observed {tool_call_count} tool calls (limit {case.max_steps}).",
        )
    )

    missing_terms = [
        term
        for term in case.expected_answer_contains
        if final_answer is None
        or _normalize_text(term, case.text_normalization)
        not in _normalize_text(final_answer, case.text_normalization)
    ]
    checks.append(
        RuleCheck(
            name="answer_contains_expected_terms",
            passed=not missing_terms,
            reason=(
                "All expected answer terms are present."
                if not missing_terms
                else f"Missing expected answer terms: {missing_terms!r}."
            ),
        )
    )

    if case.expected_agents or case.expected_handoffs or case.expected_first_failing_agent:
        routing = score_case_routing(case, steps)
        checks.append(
            RuleCheck(
                name="agent_routing",
                passed=routing.passed,
                reason="; ".join(routing.mismatches)
                if routing.mismatches
                else "Agent route and handoffs match expectations.",
            )
        )

    category_by_check: dict[CheckName, FailureCategory] = {
        "execution_successful": "execution_error",
        "correct_tool": "wrong_tool",
        "correct_args": "bad_args",
        "forbidden_behavior": "unsafe_output",
        "no_repeated_calls": "loop",
        "max_steps": "loop",
        "answer_contains_expected_terms": "hallucination",
        "agent_routing": "wrong_tool",
    }
    categories = list(
        dict.fromkeys(category_by_check[check.name] for check in checks if not check.passed)
    )
    return RuleScore(
        passed=all(check.passed for check in checks),
        checks=checks,
        failure_categories=categories,
    )


def _tool_args_match(
    tool_name: str | None,
    observed: dict[str, object] | None,
    expected: dict[str, object],
    match_modes: Mapping[str, Literal["exact", "contains", "ignore"]] | None = None,
) -> bool:
    """Match exact arguments by default, with optional per-argument overrides."""
    if observed is None or observed.keys() != expected.keys():
        return False
    for key, expected_value in expected.items():
        observed_value = observed[key]
        mode = (match_modes or {}).get(key)
        if mode == "ignore":
            continue
        if mode == "contains":
            if (
                not isinstance(observed_value, str)
                or not isinstance(expected_value, str)
                or expected_value not in observed_value
            ):
                return False
            continue
        if (
            mode is None
            and tool_name == "search_faq"
            and key == "query"
            and isinstance(observed_value, str)
            and isinstance(expected_value, str)
        ):
            expected_query = _normalize_text(expected_value)
            observed_query = _normalize_text(observed_value)
            observed_terms = set(observed_query.split())
            if observed_query == expected_query:
                continue
            if len(observed_terms) >= 2 and observed_terms.issubset(set(expected_query.split())):
                continue
            return False
        elif observed_value != expected_value:
            return False
    return True


def _normalize_text(value: str, modes: Sequence[str] | None = None) -> str:
    modes_set = set(modes or ("nfkc", "whitespace", "case"))
    normalized = value
    if "nfkc" in modes_set:
        normalized = unicodedata.normalize("NFKC", normalized)
    if "case" in modes_set:
        normalized = normalized.casefold()
    if "whitespace" in modes_set:
        normalized = " ".join(_NON_WORD.sub(" ", normalized).split())
    return normalized
