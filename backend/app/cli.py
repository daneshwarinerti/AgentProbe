"""Command-line entrypoints for local evaluation."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from backend.agents.sample_agent.agent import SampleAgent
from backend.app.adapters import load_agent_config, make_adapter
from backend.app.core.config import get_settings
from backend.app.core.logging import configure_logging
from backend.app.gate import (
    DEFAULT_BASELINE_PATH,
    DEFAULT_REPORT_PATH,
    DEFAULT_SUITE_PATH,
    compare_snapshots,
    evaluate_suite,
    load_snapshot,
    save_snapshot,
    write_report,
)
from backend.app.runner.models import ExecutionResult
from backend.app.runner.run import EvaluationRunner, RunnerOptions
from backend.app.scorers.llm_judge import CaseScore, create_llm_judge, score_case
from backend.app.scorers.safety import SafetyCaseResult, score_safety_case
from backend.suite.loader import load_cases
from backend.suite.schema import TestCase


@dataclass(frozen=True)
class ScoredResult:
    """Quality and safety outcomes emitted together for one execution."""

    quality: CaseScore
    safety: SafetyCaseResult


async def _run_demo(*, score_enabled: bool, judge_enabled: bool) -> int:
    settings = get_settings()
    configure_logging(settings.log_level)
    cases = load_cases(Path(__file__).parents[1] / "suite" / "cases")
    runner = EvaluationRunner(
        SampleAgent(settings=settings),
        RunnerOptions(
            temperature=settings.temperature,
            seed=settings.seed,
            timeout_seconds=settings.case_timeout_seconds,
            retries=settings.retries,
            concurrency=settings.concurrency,
        ),
    )
    results = await runner.run_cases(cases)
    scores = (
        await _score_results(cases, results, judge_enabled=judge_enabled) if score_enabled else {}
    )
    if not score_enabled:
        for result in results:
            _print_result(result)
    failures = sum(
        not result.success
        or (
            (score := scores.get((result.case_id, result.repeat_index))) is not None
            and (not score.quality.passed or not score.safety.passed)
        )
        for result in results
    )
    logging.getLogger(__name__).info(
        "Demo completed", extra={"case_count": len(results), "failed_count": failures}
    )
    return int(failures > 0)


async def _run_configured(
    config_path: Path,
    suite_path: Path,
    *,
    score_enabled: bool,
    judge_enabled: bool,
) -> int:
    config = load_agent_config(config_path)
    cases = load_cases(suite_path)
    adapter = make_adapter(config, config_path=config_path)
    runner = EvaluationRunner(
        adapter,
        RunnerOptions(
            timeout_seconds=config.target.timeout_seconds,
            retries=0,
        ),
    )
    results = await runner.run_cases(cases)
    scores = (
        await _score_results(cases, results, judge_enabled=judge_enabled) if score_enabled else {}
    )
    if not score_enabled:
        for result in results:
            _print_result(result)
    failures = sum(
        not result.success
        or (
            (score := scores.get((result.case_id, result.repeat_index))) is not None
            and (not score.quality.passed or not score.safety.passed)
        )
        for result in results
    )
    logging.getLogger(__name__).info(
        "Configured evaluation completed",
        extra={"case_count": len(results), "failed_count": failures},
    )
    return int(failures > 0)


async def _run_gate(
    *,
    config_path: Path,
    suite_path: Path,
    baseline_path: Path,
    report_path: Path,
    repeats: int,
    tolerance: float | None,
    judge_enabled: bool,
) -> int:
    try:
        baseline = load_snapshot(baseline_path)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    settings = get_settings()
    current = await evaluate_suite(
        suite_path=suite_path,
        config_path=config_path,
        repeats=repeats,
        judge_enabled=judge_enabled,
        settings=settings,
    )
    effective_tolerance = settings.regression_tolerance if tolerance is None else tolerance
    check = compare_snapshots(
        baseline,
        current,
        tolerance=effective_tolerance,
    )
    write_report(check.report_markdown, report_path)
    print(check.report_markdown, end="")
    print(f"Report saved to {report_path}", file=sys.stderr)
    return int(not check.passed)


async def _promote_baseline(
    *,
    config_path: Path,
    suite_path: Path,
    baseline_path: Path,
    repeats: int,
    judge_enabled: bool,
) -> int:
    current = await evaluate_suite(
        suite_path=suite_path,
        config_path=config_path,
        repeats=repeats,
        judge_enabled=judge_enabled,
    )
    unsafe_cases = [
        case.safety.case_id
        for case in current.cases
        if (
            (case.safety.is_attack and case.safety.attack_blocked is not True)
            or case.safety.pii_leaks
            or case.safety.forbidden_tools
            or case.safety.forbidden_reveals
        )
    ]
    if unsafe_cases:
        print(
            "Baseline not promoted: safety checks failed for " + ", ".join(sorted(unsafe_cases)),
            file=sys.stderr,
        )
        return 1
    save_snapshot(current, baseline_path)
    print(
        f"Promoted run {current.id} ({current.summary.pass_rate:.1%} pass rate) to {baseline_path}"
    )
    return 0


async def _score_results(
    cases: Sequence[TestCase],
    results: Sequence[ExecutionResult],
    *,
    judge_enabled: bool,
) -> dict[tuple[str, int], ScoredResult]:
    judge = create_llm_judge(get_settings()) if judge_enabled else None
    cases_by_id = {case.id: case for case in cases}
    scores: dict[tuple[str, int], ScoredResult] = {}
    for index, result in enumerate(results, start=1):
        if judge_enabled:
            print(
                f"Judging case {index}/{len(results)}: {result.case_id}",
                file=sys.stderr,
                flush=True,
            )
        quality = await score_case(
            cases_by_id[result.case_id],
            final_answer=result.output,
            steps=result.steps,
            execution_error=result.error,
            judge=judge,
        )
        case = cases_by_id[result.case_id]
        safety_judgment = (
            await judge.evaluate_safety(case, final_answer=result.output, steps=result.steps)
            if judge is not None and "safety" in case.tags
            else None
        )
        safety = score_safety_case(
            case,
            result,
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
        scored_result = ScoredResult(quality=quality, safety=safety)
        scores[(result.case_id, result.repeat_index)] = scored_result
        _print_result(result, scored_result)
    return scores


def _print_result(result: ExecutionResult, score: ScoredResult | None = None) -> None:
    output: dict[str, Any] = {
        "case_id": result.case_id,
        "execution_succeeded": result.success,
        "output": result.output,
        "error": result.error,
        "steps": len(result.steps),
        "attempts": result.attempts,
    }
    if score is not None:
        output["score"] = score.quality.model_dump(mode="json")
        output["safety"] = score.safety.model_dump(mode="json")
    print(json.dumps(output, ensure_ascii=True), flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(prog="agentprobe")
    subparsers = parser.add_subparsers(dest="command", required=True)
    demo_parser = subparsers.add_parser("demo", help="run the bundled sample evaluation")
    demo_parser.add_argument("--score", action="store_true", help="apply deterministic scorers")
    demo_parser.add_argument(
        "--judge", action="store_true", help="also run the configured LLM judge"
    )
    run_parser = subparsers.add_parser("run", help="run cases using agentprobe.yaml")
    run_parser.add_argument("--config", type=Path, default=Path("agentprobe.yaml"))
    run_parser.add_argument(
        "--suite",
        type=Path,
        default=Path(__file__).parents[1] / "suite" / "cases",
    )
    run_parser.add_argument("--score", action="store_true", help="apply deterministic scorers")
    run_parser.add_argument(
        "--judge", action="store_true", help="also run the configured LLM judge"
    )
    gate_parser = subparsers.add_parser(
        "gate", help="compare a fresh local evaluation to its approved baseline"
    )
    _add_evaluation_options(gate_parser)
    gate_parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE_PATH)
    gate_parser.add_argument("--report", type=Path, default=DEFAULT_REPORT_PATH)
    gate_parser.add_argument("--tolerance", type=float)
    promote_parser = subparsers.add_parser(
        "promote", help="evaluate the configured suite and approve it as the baseline"
    )
    _add_evaluation_options(promote_parser)
    promote_parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE_PATH)
    args = parser.parse_args()
    if getattr(args, "repeats", 1) < 1:
        parser.error("--repeats must be greater than zero.")
    if args.command == "gate" and args.tolerance is not None and not 0.0 <= args.tolerance <= 1.0:
        parser.error("--tolerance must be between 0 and 1.")
    if args.command == "demo":
        return asyncio.run(
            _run_demo(score_enabled=args.score or args.judge, judge_enabled=args.judge)
        )
    if args.command == "run":
        return asyncio.run(
            _run_configured(
                args.config,
                args.suite,
                score_enabled=args.score or args.judge,
                judge_enabled=args.judge,
            )
        )
    if args.command == "gate":
        return asyncio.run(
            _run_gate(
                config_path=args.config,
                suite_path=args.suite,
                baseline_path=args.baseline,
                report_path=args.report,
                repeats=args.repeats,
                tolerance=args.tolerance,
                judge_enabled=args.judge,
            )
        )
    return asyncio.run(
        _promote_baseline(
            config_path=args.config,
            suite_path=args.suite,
            baseline_path=args.baseline,
            repeats=args.repeats,
            judge_enabled=args.judge,
        )
    )


def _add_evaluation_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--config", type=Path, default=Path("agentprobe.yaml"))
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE_PATH)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--judge", action="store_true", help="also run the configured LLM judge")


if __name__ == "__main__":
    raise SystemExit(main())
