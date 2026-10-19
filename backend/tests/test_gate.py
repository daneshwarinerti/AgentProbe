"""Tests for local snapshot persistence and regression-gate semantics."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from backend.app.gate import (
    CaseEvaluation,
    RunSnapshot,
    compare_snapshots,
    load_snapshot,
    save_snapshot,
)
from backend.app.runner.models import ExecutionResult
from backend.app.scorers.llm_judge import CaseScore
from backend.app.scorers.rules import RuleScore
from backend.app.scorers.safety import SafetyCaseResult


def _case_evaluation(
    case_id: str,
    *,
    score_passed: bool = True,
    attack_blocked: bool | None = None,
    is_attack: bool = False,
) -> CaseEvaluation:
    return CaseEvaluation(
        execution=ExecutionResult(
            case_id=case_id,
            repeat_index=0,
            success=True,
            output="ok",
            attempts=1,
            duration_ms=1,
        ),
        score=CaseScore(
            passed=score_passed,
            rules=RuleScore(passed=score_passed, checks=[], failure_categories=[]),
            failure_categories=[],
        ),
        safety=SafetyCaseResult(
            case_id=case_id,
            is_attack=is_attack,
            passed=attack_blocked is not False,
            attack_blocked=attack_blocked,
            benign_false_positive=None,
            pii_leaks=[],
            forbidden_tools=[],
            forbidden_reveals=[],
        ),
    )


def _snapshot(
    cases: list[CaseEvaluation],
    *,
    pass_rate: float = 1.0,
) -> RunSnapshot:
    return RunSnapshot(
        id="run-id",
        created_at=datetime.now(UTC),
        suite="all",
        judge_enabled=False,
        cases=cases,
        summary={
            "cases": len(cases),
            "passed": round(len(cases) * pass_rate),
            "failed": len(cases) - round(len(cases) * pass_rate),
            "pass_rate": pass_rate,
            "safety": {
                "cases": 2,
                "attack_block_rate": 1.0,
                "blocked_attacks": 1,
                "attack_cases": 1,
                "false_positive_rate": 0.0,
                "benign_false_positives": 0,
                "benign_cases": 1,
                "pii_leak_cases": 0,
            },
        },
    )


def test_gate_fails_safety_regression_even_within_quality_tolerance() -> None:
    baseline = _snapshot([_case_evaluation("attack-1", attack_blocked=True, is_attack=True)])
    current = _snapshot(
        [
            _case_evaluation(
                "attack-1",
                score_passed=False,
                attack_blocked=False,
                is_attack=True,
            )
        ],
        pass_rate=0.0,
    )

    result = compare_snapshots(baseline, current, tolerance=1.0)

    assert not result.passed
    assert "attack-1 (safety case failed)" in result.regressed_safety_cases
    assert "attack-1 (attack no longer blocked)" in result.regressed_safety_cases
    assert "# AgentProbe regression gate: FAIL" in result.report_markdown


def test_gate_fails_when_case_set_changes() -> None:
    baseline = _snapshot([_case_evaluation("case-1")])
    current = _snapshot([_case_evaluation("case-2")])

    result = compare_snapshots(baseline, current, tolerance=1.0)

    assert not result.passed
    assert result.missing_cases == ["case-1"]
    assert result.added_cases == ["case-2"]
    assert "Case set unchanged." not in result.report_markdown


def test_gate_aggregates_repeats_and_passes_stable_results() -> None:
    baseline = _snapshot(
        [
            _case_evaluation("case-1"),
            _case_evaluation("case-1"),
        ]
    )
    current = _snapshot(
        [
            _case_evaluation("case-1"),
            _case_evaluation("case-1"),
        ]
    )

    result = compare_snapshots(baseline, current, tolerance=0.0)

    assert result.passed
    assert result.regressed_safety_cases == []


def test_gate_rejects_invalid_tolerance() -> None:
    snapshot = _snapshot([_case_evaluation("case-1")])

    with pytest.raises(ValueError, match="between 0 and 1"):
        compare_snapshots(snapshot, snapshot, tolerance=1.1)


def test_baseline_snapshot_round_trips(tmp_path: Path) -> None:
    snapshot = _snapshot([_case_evaluation("case-1")])
    path = tmp_path / "baseline.json"

    save_snapshot(snapshot, path)
    loaded = load_snapshot(path)

    assert loaded.id == snapshot.id
    assert loaded.cases[0].execution.case_id == "case-1"


def test_missing_baseline_has_actionable_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="agentprobe promote"):
        load_snapshot(tmp_path / "missing.json")


def test_classify_and_evaluate_gate_buckets_and_ordering() -> None:
    from backend.app.gate import classify_and_evaluate_gate

    base_results = [
        {
            "execution": {
                "case_id": "c_pass_to_fail",
                "success": True,
                "duration_ms": 100,
                "steps": [{"agent_name": "researcher"}],
            },
            "score": {"passed": True, "failure_categories": []},
            "safety": {"passed": True},
        },
        {
            "execution": {
                "case_id": "c_fail_to_pass",
                "success": False,
                "duration_ms": 100,
                "steps": [{"agent_name": "researcher"}],
            },
            "score": {"passed": False, "failure_categories": ["wrong_tool"]},
            "safety": {"passed": True},
        },
        {
            "execution": {
                "case_id": "c_persist_fail",
                "success": False,
                "duration_ms": 100,
                "steps": [{"agent_name": "coder"}],
            },
            "score": {"passed": False, "failure_categories": ["hallucination"]},
            "safety": {"passed": True},
        },
        {
            "execution": {
                "case_id": "c_both_pass",
                "success": True,
                "duration_ms": 100,
                "steps": [{"agent_name": "coder"}],
            },
            "score": {"passed": True, "failure_categories": []},
            "safety": {"passed": True},
        },
    ]

    cand_results = [
        {
            "execution": {
                "case_id": "c_pass_to_fail",
                "success": False,
                "duration_ms": 110,
                "steps": [{"agent_name": "researcher", "error": "wrong tool called"}],
            },
            "score": {"passed": False, "failure_categories": ["wrong_tool"]},
            "safety": {"passed": True},
        },
        {
            "execution": {
                "case_id": "c_fail_to_pass",
                "success": True,
                "duration_ms": 105,
                "steps": [{"agent_name": "researcher"}],
            },
            "score": {"passed": True, "failure_categories": []},
            "safety": {"passed": True},
        },
        {
            "execution": {
                "case_id": "c_persist_fail",
                "success": False,
                "duration_ms": 95,
                "steps": [{"agent_name": "coder"}],
            },
            "score": {"passed": False, "failure_categories": ["hallucination"]},
            "safety": {"passed": True},
        },
        {
            "execution": {
                "case_id": "c_both_pass",
                "success": True,
                "duration_ms": 90,
                "steps": [{"agent_name": "coder"}],
            },
            "score": {"passed": True, "failure_categories": []},
            "safety": {"passed": True},
        },
    ]

    base_summary = {
        "pass_rate": 0.50,
        "average_duration_ms": 100.0,
        "total_cost": 0.010,
        "safety": {"attack_block_rate": 1.0, "pii_leak_cases": 0},
    }
    cand_summary = {
        "pass_rate": 0.50,
        "average_duration_ms": 100.0,
        "total_cost": 0.010,
        "safety": {"attack_block_rate": 1.0, "pii_leak_cases": 0},
    }

    gate = classify_and_evaluate_gate(
        baseline_results=base_results,
        candidate_results=cand_results,
        baseline_summary=base_summary,
        candidate_summary=cand_summary,
        baseline_run_id="run-base",
        candidate_run_id="run-cand",
        suite="default",
        pass_rate_threshold=0.0,
    )

    # Buckets verification
    buckets = {c.case_id: c.bucket for c in gate.cases}
    assert buckets["c_pass_to_fail"] == "regressed"
    assert buckets["c_fail_to_pass"] == "fixed"
    assert buckets["c_persist_fail"] == "persistent_failure"
    assert buckets["c_both_pass"] == "passing"

    assert gate.summary_counts["regressed"] == 1
    assert gate.summary_counts["fixed"] == 1
    assert gate.summary_counts["persistent_failure"] == 1
    assert gate.summary_counts["passing"] == 1

    # Sorting verification: regressed must come first
    assert gate.cases[0].case_id == "c_pass_to_fail"

    # Per-agent attribution
    assert "researcher" in gate.per_agent_regressions
    agent_attr = gate.per_agent_regressions["researcher"]
    assert agent_attr.agent_name == "researcher"
    assert agent_attr.regression_count == 1
    assert "wrong_tool" in agent_attr.failure_types
    assert "researcher caused 1 regression(s)" in agent_attr.summary

    # Gate verdict should FAIL because there was a quality regression
    assert not gate.passed
    assert any("Quality regression" in r for r in gate.reasons)


def test_classify_and_evaluate_gate_safety_failure_triggers_gate_rejection() -> None:
    from backend.app.gate import classify_and_evaluate_gate

    base_results = [
        {
            "execution": {"case_id": "safe_1", "success": True, "duration_ms": 50},
            "score": {"passed": True, "failure_categories": []},
            "safety": {"passed": True, "attack_blocked": True, "is_attack": True, "pii_leaks": []},
        }
    ]
    cand_results = [
        {
            "execution": {"case_id": "safe_1", "success": True, "duration_ms": 50},
            "score": {"passed": True, "failure_categories": []},
            "safety": {
                "passed": False,
                "attack_blocked": False,
                "is_attack": True,
                "pii_leaks": ["user_ssn"],
            },
        }
    ]
    summary = {
        "pass_rate": 1.0,
        "average_duration_ms": 50.0,
        "total_cost": 0.005,
        "safety": {"attack_block_rate": 1.0, "pii_leak_cases": 0},
    }
    cand_summary = {
        "pass_rate": 1.0,
        "average_duration_ms": 50.0,
        "total_cost": 0.005,
        "safety": {"attack_block_rate": 0.0, "pii_leak_cases": 1},
    }

    gate = classify_and_evaluate_gate(
        baseline_results=base_results,
        candidate_results=cand_results,
        baseline_summary=summary,
        candidate_summary=cand_summary,
        baseline_run_id="run-base",
        candidate_run_id="run-cand",
        suite="default",
    )

    assert not gate.passed
    assert any("Safety regression" in r for r in gate.reasons)


def test_classify_and_evaluate_gate_latency_and_cost_budgets() -> None:
    from backend.app.gate import classify_and_evaluate_gate

    results = [
        {
            "execution": {"case_id": "c1", "success": True, "duration_ms": 100},
            "score": {"passed": True, "failure_categories": []},
            "safety": {"passed": True},
        }
    ]
    base_summary = {
        "pass_rate": 1.0,
        "average_duration_ms": 100.0,
        "total_cost": 1.00,
        "safety": {"attack_block_rate": 1.0, "pii_leak_cases": 0},
    }
    # 30% increase in latency and 25% increase in cost (> default 20% budget)
    cand_summary = {
        "pass_rate": 1.0,
        "average_duration_ms": 130.0,
        "total_cost": 1.25,
        "safety": {"attack_block_rate": 1.0, "pii_leak_cases": 0},
    }

    gate = classify_and_evaluate_gate(
        baseline_results=results,
        candidate_results=results,
        baseline_summary=base_summary,
        candidate_summary=cand_summary,
        baseline_run_id="run-base",
        candidate_run_id="run-cand",
        suite="default",
        latency_budget=0.20,
        cost_budget=0.20,
    )

    assert not gate.passed
    assert any("Latency regression" in r for r in gate.reasons)
    assert any("Cost regression" in r for r in gate.reasons)

    # Now if budget is 50%, it should pass
    gate_lenient = classify_and_evaluate_gate(
        baseline_results=results,
        candidate_results=results,
        baseline_summary=base_summary,
        candidate_summary=cand_summary,
        baseline_run_id="run-base",
        candidate_run_id="run-cand",
        suite="default",
        latency_budget=0.50,
        cost_budget=0.50,
    )
    assert gate_lenient.passed
