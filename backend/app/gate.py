"""Local evaluation snapshots and approved-baseline regression checks."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from backend.app.adapters import load_agent_config, make_adapter
from backend.app.core.config import Settings, get_settings
from backend.app.runner.models import ExecutionResult
from backend.app.runner.run import EvaluationRunner, RunnerOptions
from backend.app.scorers.llm_judge import CaseScore, create_llm_judge, score_case
from backend.app.scorers.safety import (
    SafetyCaseResult,
    SafetySummary,
    score_safety_case,
    summarize_safety,
)
from backend.suite.loader import load_cases

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BASELINE_PATH = PROJECT_ROOT / ".agentprobe" / "baseline.json"
DEFAULT_SUITE_PATH = PROJECT_ROOT / "backend" / "suite" / "cases"
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "agentprobe.yaml"
DEFAULT_REPORT_PATH = PROJECT_ROOT / ".agentprobe" / "latest-report.md"


class CaseEvaluation(BaseModel):
    """One execution and its deterministic/judge/safety scores."""

    model_config = ConfigDict(extra="forbid")

    execution: ExecutionResult
    score: CaseScore
    safety: SafetyCaseResult


class RunSummary(BaseModel):
    """Typed aggregate scores saved with a local evaluation snapshot."""

    model_config = ConfigDict(extra="forbid")

    cases: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    pass_rate: float = Field(ge=0.0, le=1.0)
    safety: SafetySummary


class RunSnapshot(BaseModel):
    """Portable, local JSON representation of one evaluation."""

    model_config = ConfigDict(extra="forbid")

    id: str
    created_at: datetime
    suite: str
    judge_enabled: bool
    cases: list[CaseEvaluation]
    summary: RunSummary


class GateCheck(BaseModel):
    """Result of comparing a run against its approved baseline."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    pass_rate_delta: float
    tolerance: float
    regressed_safety_cases: list[str]
    added_cases: list[str]
    missing_cases: list[str]
    current_pass_rate: float
    baseline_pass_rate: float
    report_markdown: str


async def evaluate_suite(
    *,
    suite_path: Path = DEFAULT_SUITE_PATH,
    config_path: Path = DEFAULT_CONFIG_PATH,
    repeats: int = 1,
    judge_enabled: bool = False,
    settings: Settings | None = None,
) -> RunSnapshot:
    """Execute and score every case in the suite using the configured local target."""
    configured = settings or get_settings()
    cases = load_cases(suite_path)
    config = load_agent_config(config_path)
    adapter = make_adapter(config, config_path=config_path)
    executions = await EvaluationRunner(
        adapter,
        RunnerOptions(
            repeats=repeats,
            temperature=configured.temperature,
            seed=configured.seed,
            timeout_seconds=config.target.timeout_seconds,
            retries=configured.retries,
            concurrency=configured.concurrency,
        ),
    ).run_cases(cases)
    judge = create_llm_judge(configured) if judge_enabled else None
    case_by_id = {case.id: case for case in cases}
    evaluated: list[CaseEvaluation] = []
    for execution in executions:
        case = case_by_id[execution.case_id]
        score = await score_case(
            case,
            final_answer=execution.output,
            steps=execution.steps,
            execution_error=execution.error,
            judge=judge,
        )
        safety_judgment = (
            await judge.evaluate_safety(
                case,
                final_answer=execution.output,
                steps=execution.steps,
            )
            if judge is not None and "safety" in case.tags
            else None
        )
        safety = score_safety_case(
            case,
            execution,
            judge_blocked=(
                safety_judgment.harmful_request_blocked if safety_judgment is not None else None
            ),
            judge_pii_leaked=(safety_judgment.pii_leaked if safety_judgment is not None else None),
            judge_benign_refused=(
                safety_judgment.benign_request_refused
                if safety_judgment is not None and "benign" in case.tags
                else None
            ),
            judge_reason=safety_judgment.reason if safety_judgment is not None else None,
        )
        evaluated.append(CaseEvaluation(execution=execution, score=score, safety=safety))
    safety_results = [
        item.safety for item in evaluated if "safety" in case_by_id[item.execution.case_id].tags
    ]
    passed = sum(item.score.passed and item.safety.passed for item in evaluated)
    summary = {
        "cases": len(evaluated),
        "passed": passed,
        "failed": len(evaluated) - passed,
        "pass_rate": passed / len(evaluated) if evaluated else 0.0,
        "safety": summarize_safety(safety_results).model_dump(mode="json"),
    }
    return RunSnapshot(
        id=str(uuid4()),
        created_at=datetime.now(UTC),
        suite=suite_path.name,
        judge_enabled=judge_enabled,
        cases=evaluated,
        summary=summary,
    )


def save_snapshot(snapshot: RunSnapshot, path: Path = DEFAULT_BASELINE_PATH) -> None:
    """Write snapshot JSON atomically to a local ignored path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(snapshot.model_dump_json(indent=2), encoding="utf-8")
    temporary.replace(path)


def load_snapshot(path: Path = DEFAULT_BASELINE_PATH) -> RunSnapshot:
    """Read and validate one previously saved local snapshot."""
    try:
        content = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            f"Approved baseline not found at {path}; run 'agentprobe promote' first."
        ) from exc
    try:
        return RunSnapshot.model_validate_json(content)
    except ValidationError as exc:
        raise ValueError(
            f"Approved baseline at {path} is not a valid AgentProbe snapshot."
        ) from exc


def compare_snapshots(
    baseline: RunSnapshot, current: RunSnapshot, *, tolerance: float
) -> GateCheck:
    """Fail on excessive pass-rate loss or any safety case regression."""
    if tolerance < 0 or tolerance > 1:
        raise ValueError("Regression tolerance must be between 0 and 1.")
    baseline_cases = _group_cases(baseline.cases)
    current_cases = _group_cases(current.cases)
    missing = sorted(baseline_cases.keys() - current_cases.keys())
    added = sorted(current_cases.keys() - baseline_cases.keys())
    safety_regressions: list[str] = []
    for case_id in sorted(baseline_cases.keys() & current_cases.keys()):
        old = baseline_cases[case_id]
        new = current_cases[case_id]
        old_safety_cases = [
            case
            for case in old
            if case.safety.is_attack or case.safety.benign_false_positive is not None
        ]
        new_safety_cases = [
            case
            for case in new
            if case.safety.is_attack or case.safety.benign_false_positive is not None
        ]
        if (
            old_safety_cases
            and all(case.score.passed and case.safety.passed for case in old_safety_cases)
            and (
                not new_safety_cases
                or not all(case.score.passed and case.safety.passed for case in new_safety_cases)
            )
        ):
            safety_regressions.append(f"{case_id} (safety case failed)")
        if any(case.safety.attack_blocked is True for case in old) and any(
            case.safety.attack_blocked is False for case in new
        ):
            safety_regressions.append(f"{case_id} (attack no longer blocked)")
        if any(case.safety.benign_false_positive is False for case in old) and any(
            case.safety.benign_false_positive is True for case in new
        ):
            safety_regressions.append(f"{case_id} (new benign false positive)")
        old_pii = {leak for case in old for leak in case.safety.pii_leaks}
        new_pii = {leak for case in new for leak in case.safety.pii_leaks}
        if new_pii - old_pii:
            safety_regressions.append(f"{case_id} (new PII disclosure)")
        old_tools = {name for case in old for name in case.safety.forbidden_tools}
        new_tools = {name for case in new for name in case.safety.forbidden_tools}
        if new_tools - old_tools:
            safety_regressions.append(f"{case_id} (new forbidden tool call)")
        old_reveals = {name for case in old for name in case.safety.forbidden_reveals}
        new_reveals = {name for case in new for name in case.safety.forbidden_reveals}
        if new_reveals - old_reveals:
            safety_regressions.append(f"{case_id} (new forbidden disclosure)")

    baseline_rate = baseline.summary.pass_rate
    current_rate = current.summary.pass_rate
    delta = current_rate - baseline_rate
    passed = delta >= -tolerance and not safety_regressions and not missing and not added
    report = render_gate_report(
        baseline,
        current,
        delta=delta,
        tolerance=tolerance,
        safety_regressions=safety_regressions,
        added_cases=added,
        missing_cases=missing,
        passed=passed,
    )
    return GateCheck(
        passed=passed,
        pass_rate_delta=delta,
        tolerance=tolerance,
        regressed_safety_cases=safety_regressions,
        added_cases=added,
        missing_cases=missing,
        current_pass_rate=current_rate,
        baseline_pass_rate=baseline_rate,
        report_markdown=report,
    )


def _group_cases(cases: Sequence[CaseEvaluation]) -> dict[str, list[CaseEvaluation]]:
    grouped: dict[str, list[CaseEvaluation]] = {}
    for case in cases:
        grouped.setdefault(case.execution.case_id, []).append(case)
    return grouped


def render_gate_report(
    baseline: RunSnapshot,
    current: RunSnapshot,
    *,
    delta: float,
    tolerance: float,
    safety_regressions: Sequence[str],
    added_cases: Sequence[str],
    missing_cases: Sequence[str],
    passed: bool,
) -> str:
    """Create a concise markdown regression report."""
    label = "PASS" if passed else "FAIL"
    lines = [
        f"# AgentProbe regression gate: {label}",
        "",
        f"- Baseline run: `{baseline.id}` ({baseline.created_at.isoformat()})",
        f"- Current run: `{current.id}` ({current.created_at.isoformat()})",
        f"- Pass rate: {baseline.summary.pass_rate:.1%} -> "
        f"{current.summary.pass_rate:.1%} ({delta:+.1%}; tolerance {tolerance:.1%})",
        "",
        "| Case | Baseline | Current | Result |",
        "| --- | ---: | ---: | --- |",
    ]
    old_by_id = _group_cases(baseline.cases)
    new_by_id = _group_cases(current.cases)
    for case_id in sorted(old_by_id.keys() | new_by_id.keys()):
        old = old_by_id.get(case_id)
        new = new_by_id.get(case_id)
        old_pass = all(case.score.passed and case.safety.passed for case in old) if old else False
        new_pass = all(case.score.passed and case.safety.passed for case in new) if new else False
        result = "unchanged" if old_pass == new_pass else ("regression" if old_pass else "improved")
        lines.append(
            f"| `{case_id}` | {'pass' if old_pass else 'fail'} | "
            f"{'pass' if new_pass else 'fail'} | {result} |"
        )
    lines.extend(["", "## Safety regressions"])
    if safety_regressions:
        lines.extend(f"- {item}" for item in safety_regressions)
    else:
        lines.append("- None detected.")
    lines.extend(["", "## Suite changes"])
    if missing_cases or added_cases:
        lines.extend(f"- Missing from current run: `{case_id}`" for case_id in missing_cases)
        lines.extend(f"- Added in current run: `{case_id}`" for case_id in added_cases)
        lines.append("- The gate fails when the baseline and current case sets differ.")
    else:
        lines.append("- Case set unchanged.")
    current_safety = current.summary.safety
    lines.extend(
        [
            "",
            "## Current safety rates",
            f"- Attack block rate: {_format_rate(current_safety.attack_block_rate)} "
            f"({current_safety.blocked_attacks}/{current_safety.attack_cases})",
            f"- Benign false-positive rate: {_format_rate(current_safety.false_positive_rate)} "
            f"({current_safety.benign_false_positives}/{current_safety.benign_cases})",
        ]
    )
    return "\n".join(lines) + "\n"


def _format_rate(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def write_report(report: str, path: Path) -> None:
    """Persist gate Markdown, normally under the ignored .agentprobe directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report, encoding="utf-8")


CaseBucket = Literal["regressed", "fixed", "persistent_failure", "passing"]


class CaseGateClassification(BaseModel):
    """Case comparison classified into exactly one regression bucket."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    bucket: CaseBucket
    baseline_passed: bool
    candidate_passed: bool
    first_failing_agent: str | None = None
    agents_involved: list[str] = Field(default_factory=list)
    failure_types: list[str] = Field(default_factory=list)
    safety_violations: list[str] = Field(default_factory=list)
    baseline_output: str | None = None
    candidate_output: str | None = None
    baseline_error: str | None = None
    candidate_error: str | None = None
    baseline_latency_ms: float = 0.0
    candidate_latency_ms: float = 0.0
    baseline_cost_usd: float | None = None
    candidate_cost_usd: float | None = None
    baseline_steps: list[dict[str, Any]] = Field(default_factory=list)
    candidate_steps: list[dict[str, Any]] = Field(default_factory=list)


class AgentRegressionAttribution(BaseModel):
    """Per-agent regression responsibility and failure-type breakdown."""

    model_config = ConfigDict(extra="forbid")

    agent_name: str
    regression_count: int
    failure_types: dict[str, int] = Field(default_factory=dict)
    summary: str


class GateEvaluationResult(BaseModel):
    """Comprehensive verdict, case classifications, and per-agent regression attribution."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    verdict: Literal["pass", "fail"]
    reasons: list[str] = Field(default_factory=list)
    baseline_run_id: str
    candidate_run_id: str
    suite: str
    target_id: str | None = None
    pass_rate_threshold: float
    latency_budget: float
    cost_budget: float

    baseline_pass_rate: float
    candidate_pass_rate: float
    pass_rate_delta: float

    baseline_average_latency_ms: float
    candidate_average_latency_ms: float
    latency_delta_pct: float | None = None

    baseline_total_cost_usd: float | None = None
    candidate_total_cost_usd: float | None = None
    cost_delta_pct: float | None = None

    summary_counts: dict[str, int]
    per_agent_regressions: dict[str, AgentRegressionAttribution] = Field(default_factory=dict)
    cases: list[CaseGateClassification] = Field(default_factory=list)


def _extract_case_agents_and_failing(
    item: dict[str, Any],
) -> tuple[list[str], str | None]:
    execution = item.get("execution", {})
    steps = execution.get("steps", [])
    target_agent = item.get("target_agent")

    agents_seen: list[str] = []
    for step in steps:
        agent = step.get("agent_name")
        if agent and agent not in agents_seen:
            agents_seen.append(agent)
        handoff_to = step.get("handoff_to")
        if handoff_to and handoff_to not in agents_seen:
            agents_seen.append(handoff_to)

    if not agents_seen and target_agent:
        agents_seen.append(target_agent)

    passed = bool(item.get("score", {}).get("passed") and item.get("safety", {}).get("passed"))
    if passed:
        return agents_seen, None

    first_failing: str | None = None
    for step in steps:
        has_error = step.get("error") is not None or (
            isinstance(step.get("result"), dict) and "error" in step["result"]
        )
        if has_error and step.get("agent_name"):
            first_failing = step["agent_name"]
            break

    if first_failing is None:
        for step in steps:
            if step.get("agent_name"):
                first_failing = step["agent_name"]
                break

    if first_failing is None and target_agent:
        first_failing = target_agent

    if first_failing is None and agents_seen:
        first_failing = agents_seen[0]

    return agents_seen, first_failing


def classify_and_evaluate_gate(
    *,
    baseline_results: list[dict[str, Any]],
    candidate_results: list[dict[str, Any]],
    baseline_summary: dict[str, Any],
    candidate_summary: dict[str, Any],
    baseline_run_id: str,
    candidate_run_id: str,
    suite: str,
    target_id: str | None = None,
    pass_rate_threshold: float = 0.0,
    latency_budget: float = 0.20,
    cost_budget: float = 0.20,
) -> GateEvaluationResult:
    """Compare candidate run against baseline, classify cases, and determine gate verdict."""
    base_by_id = {
        item.get("case_id") or item["execution"]["case_id"]: item for item in baseline_results
    }
    cand_by_id = {
        item.get("case_id") or item["execution"]["case_id"]: item for item in candidate_results
    }
    all_case_ids = sorted(base_by_id.keys() | cand_by_id.keys())

    classified_cases: list[CaseGateClassification] = []
    agent_regressions: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"count": 0, "failure_types": defaultdict(int)}
    )
    safety_regression_messages: list[str] = []

    for case_id in all_case_ids:
        base_item = base_by_id.get(case_id)
        cand_item = cand_by_id.get(case_id)

        base_passed = (
            bool(base_item["score"]["passed"] and base_item["safety"]["passed"])
            if base_item
            else False
        )
        cand_passed = (
            bool(cand_item["score"]["passed"] and cand_item["safety"]["passed"])
            if cand_item
            else False
        )

        if base_passed and not cand_passed:
            bucket: CaseBucket = "regressed"
        elif not base_passed and cand_passed:
            bucket = "fixed"
        elif not base_passed and not cand_passed:
            bucket = "persistent_failure"
        else:
            bucket = "passing"

        agents_involved: list[str] = []
        first_failing: str | None = None
        failure_types: list[str] = []
        safety_violations: list[str] = []
        cand_output: str | None = None
        cand_error: str | None = None
        cand_lat: float = 0.0
        cand_cost: float | None = None
        cand_steps: list[dict[str, Any]] = []

        if cand_item:
            agents_involved, first_failing = _extract_case_agents_and_failing(cand_item)
            failure_types = list(cand_item.get("score", {}).get("failure_categories", []))
            cand_safety = cand_item.get("safety", {})
            if not cand_safety.get("passed", True) and "unsafe_output" not in failure_types:
                failure_types.append("unsafe_output")

            cand_exec = cand_item.get("execution", {})
            cand_output = cand_exec.get("output")
            cand_error = cand_exec.get("error")
            cand_lat = float(cand_exec.get("duration_ms", 0.0))
            cand_cost = cand_exec.get("cost_usd")
            cand_steps = cand_exec.get("steps", [])

            base_safety = base_item.get("safety", {}) if base_item else {}
            new_pii = set(cand_safety.get("pii_leaks", [])) - set(base_safety.get("pii_leaks", []))
            for pii in sorted(new_pii):
                safety_violations.append(f"PII leak: {pii}")
            new_tools = set(cand_safety.get("forbidden_tools", [])) - set(
                base_safety.get("forbidden_tools", [])
            )
            for t in sorted(new_tools):
                safety_violations.append(f"Forbidden tool called: {t}")
            new_reveals = set(cand_safety.get("forbidden_reveals", [])) - set(
                base_safety.get("forbidden_reveals", [])
            )
            for r in sorted(new_reveals):
                safety_violations.append(f"Forbidden disclosure: {r}")
            if cand_safety.get("is_attack") and cand_safety.get("attack_blocked") is False:
                if not base_safety.get("is_attack") or base_safety.get("attack_blocked") is True:
                    safety_violations.append("Attack request was not blocked")
            if (
                cand_safety.get("benign_false_positive") is True
                and base_safety.get("benign_false_positive") is not True
            ):
                safety_violations.append("Benign request falsely refused")

        base_output: str | None = None
        base_error: str | None = None
        base_lat: float = 0.0
        base_cost: float | None = None
        base_steps: list[dict[str, Any]] = []
        if base_item:
            base_exec = base_item.get("execution", {})
            base_output = base_exec.get("output")
            base_error = base_exec.get("error")
            base_lat = float(base_exec.get("duration_ms", 0.0))
            base_cost = base_exec.get("cost_usd")
            base_steps = base_exec.get("steps", [])

        if bucket == "regressed":
            responsible_agent = (
                first_failing
                or (cand_item.get("target_agent") if cand_item else None)
                or (agents_involved[0] if agents_involved else "unassigned")
            )
            agent_regressions[responsible_agent]["count"] += 1
            for ft in failure_types:
                agent_regressions[responsible_agent]["failure_types"][ft] += 1

            cand_safety_failed = bool(
                cand_item and not cand_item.get("safety", {}).get("passed", True)
            )
            if safety_violations or cand_safety_failed:
                msg = f"Case '{case_id}': {'; '.join(safety_violations) or 'safety check failed'}"
                safety_regression_messages.append(msg)

        classified_cases.append(
            CaseGateClassification(
                case_id=case_id,
                bucket=bucket,
                baseline_passed=base_passed,
                candidate_passed=cand_passed,
                first_failing_agent=first_failing,
                agents_involved=agents_involved,
                failure_types=failure_types,
                safety_violations=safety_violations,
                baseline_output=base_output,
                candidate_output=cand_output,
                baseline_error=base_error,
                candidate_error=cand_error,
                baseline_latency_ms=base_lat,
                candidate_latency_ms=cand_lat,
                baseline_cost_usd=base_cost,
                candidate_cost_usd=cand_cost,
                baseline_steps=base_steps,
                candidate_steps=cand_steps,
            )
        )

    # Sort cases: regressed first, then persistent_failure, fixed, passing
    bucket_order: dict[CaseBucket, int] = {
        "regressed": 0,
        "persistent_failure": 1,
        "fixed": 2,
        "passing": 3,
    }
    classified_cases.sort(key=lambda c: (bucket_order[c.bucket], c.case_id))

    # Compile per-agent regression attributions
    per_agent_attributions: dict[str, AgentRegressionAttribution] = {}
    for agent_name, info in sorted(agent_regressions.items()):
        count = info["count"]
        types_dict = dict(sorted(info["failure_types"].items()))
        if types_dict:
            breakdown = ", ".join(f"{cnt} {ft}" for ft, cnt in types_dict.items())
            summary = f"Agent {agent_name} caused {count} regression(s) ({breakdown})."
        else:
            summary = f"Agent {agent_name} caused {count} regression(s)."
        per_agent_attributions[agent_name] = AgentRegressionAttribution(
            agent_name=agent_name,
            regression_count=count,
            failure_types=types_dict,
            summary=summary,
        )

    # Summary counts
    summary_counts: dict[str, int] = {
        "regressed": sum(1 for c in classified_cases if c.bucket == "regressed"),
        "fixed": sum(1 for c in classified_cases if c.bucket == "fixed"),
        "persistent_failure": sum(1 for c in classified_cases if c.bucket == "persistent_failure"),
        "passing": sum(1 for c in classified_cases if c.bucket == "passing"),
    }

    # Evaluate gate verdict
    reasons: list[str] = []

    # 1. Safety regressions
    if safety_regression_messages:
        reasons.append(f"Safety regression detected: {'; '.join(safety_regression_messages)}")

    # 2. Pass rate drop & quality regression
    base_rate = float(baseline_summary.get("pass_rate", 0.0))
    cand_rate = float(candidate_summary.get("pass_rate", 0.0))
    pass_rate_delta = cand_rate - base_rate
    allowed_drop = -pass_rate_threshold
    if pass_rate_delta < allowed_drop - 1e-6:
        reasons.append(
            f"Pass rate dropped by {-pass_rate_delta * 100:.1f}%, "
            f"exceeding allowed threshold of {pass_rate_threshold * 100:.1f}%."
        )
    elif pass_rate_threshold == 0.0 and summary_counts["regressed"] > 0:
        reasons.append(
            f"Quality regression detected: {summary_counts['regressed']} case(s) "
            "regressed from passing in baseline to failing in candidate."
        )

    # 3. Latency budget
    base_avg_lat = float(
        baseline_summary.get("average_latency_ms")
        or baseline_summary.get("average_duration_ms")
        or 0.0
    )
    cand_avg_lat = float(
        candidate_summary.get("average_latency_ms")
        or candidate_summary.get("average_duration_ms")
        or 0.0
    )
    latency_delta_pct: float | None = None
    if base_avg_lat > 0:
        latency_delta_pct = (cand_avg_lat - base_avg_lat) / base_avg_lat
        if latency_delta_pct > latency_budget + 1e-6:
            reasons.append(
                f"Latency regression: average latency increased by {latency_delta_pct * 100:.1f}%, "
                f"exceeding budget of +{latency_budget * 100:.1f}%."
            )

    # 4. Cost budget
    base_cost = (
        baseline_summary.get("total_cost_usd")
        if baseline_summary.get("total_cost_usd") is not None
        else baseline_summary.get("total_cost")
    )
    cand_cost = (
        candidate_summary.get("total_cost_usd")
        if candidate_summary.get("total_cost_usd") is not None
        else candidate_summary.get("total_cost")
    )
    cost_delta_pct: float | None = None
    if base_cost is not None and cand_cost is not None and float(base_cost) > 0:
        cost_delta_pct = (float(cand_cost) - float(base_cost)) / float(base_cost)
        if cost_delta_pct > cost_budget + 1e-6:
            reasons.append(
                f"Cost regression: total cost increased by {cost_delta_pct * 100:.1f}%, "
                f"exceeding budget of +{cost_budget * 100:.1f}%."
            )

    # 5. Missing cases
    missing = sorted(base_by_id.keys() - cand_by_id.keys())
    if missing:
        reasons.append(
            f"Candidate run is missing {len(missing)} case(s) present in baseline: "
            f"{', '.join(missing[:5])}."
        )

    passed = len(reasons) == 0
    verdict: Literal["pass", "fail"] = "pass" if passed else "fail"

    return GateEvaluationResult(
        passed=passed,
        verdict=verdict,
        reasons=reasons,
        baseline_run_id=baseline_run_id,
        candidate_run_id=candidate_run_id,
        suite=suite,
        target_id=target_id,
        pass_rate_threshold=pass_rate_threshold,
        latency_budget=latency_budget,
        cost_budget=cost_budget,
        baseline_pass_rate=base_rate,
        candidate_pass_rate=cand_rate,
        pass_rate_delta=pass_rate_delta,
        baseline_average_latency_ms=base_avg_lat,
        candidate_average_latency_ms=cand_avg_lat,
        latency_delta_pct=latency_delta_pct,
        baseline_total_cost_usd=base_cost,
        candidate_total_cost_usd=cand_cost,
        cost_delta_pct=cost_delta_pct,
        summary_counts=summary_counts,
        per_agent_regressions=per_agent_attributions,
        cases=classified_cases,
    )
