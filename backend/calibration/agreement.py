"""Calculate binary human-versus-judge agreement and Cohen's kappa."""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from backend.app.adapters import load_agent_config, make_adapter
from backend.app.core.config import get_settings
from backend.app.runner.models import TraceStep
from backend.app.runner.run import EvaluationRunner, RunnerOptions
from backend.app.scorers.llm_judge import create_llm_judge
from backend.suite.loader import load_cases
from backend.suite.schema import TestCase


class CalibrationError(ValueError):
    """Calibration labels or predictions are incomplete or inconsistent."""


class FixtureGroundTruth(BaseModel):
    """Expected labels for a deliberately constructed calibration example."""

    model_config = ConfigDict(extra="forbid")

    correct: bool
    faithful: bool


class CalibrationFixture(BaseModel):
    """Known-output example scored without running the target agent."""

    model_config = ConfigDict(extra="forbid")

    case: TestCase
    final_answer: str
    steps: list[TraceStep]
    ground_truth: FixtureGroundTruth


def calculate_agreement(
    labels_path: Path,
    predictions_path: Path,
    include_disagreements: bool = False,
) -> dict[str, Any]:
    """Compare the combined correct-and-faithful label for each case."""
    human = _load_pairs(labels_path, "human_correct", "human_faithful")
    judge = _load_pairs(predictions_path, "judge_correct", "judge_faithful")
    if human.keys() != judge.keys():
        missing = sorted(human.keys() - judge.keys())
        unexpected = sorted(judge.keys() - human.keys())
        raise CalibrationError(
            f"Label/prediction case IDs differ; missing={missing}, unexpected={unexpected}."
        )
    human_pass = {case_id: correct and faithful for case_id, (correct, faithful) in human.items()}
    judge_pass = {case_id: correct and faithful for case_id, (correct, faithful) in judge.items()}
    count = len(human_pass)
    if count == 0:
        raise CalibrationError("Calibration data must contain at least one labeled case.")
    agreements = sum(human_pass[case_id] == judge_pass[case_id] for case_id in human_pass)
    pass_agreement, pass_kappa = _agreement_metrics(human_pass, judge_pass)
    correctness_agreement, correctness_kappa = _agreement_metrics(
        {case_id: value[0] for case_id, value in human.items()},
        {case_id: value[0] for case_id, value in judge.items()},
    )
    faithfulness_agreement, faithfulness_kappa = _agreement_metrics(
        {case_id: value[1] for case_id, value in human.items()},
        {case_id: value[1] for case_id, value in judge.items()},
    )
    report: dict[str, Any] = {
        "cases": count,
        "agreements": agreements,
        "agreement_pct": pass_agreement,
        "cohen_kappa": pass_kappa,
        "correctness_agreement_pct": correctness_agreement,
        "correctness_cohen_kappa": correctness_kappa,
        "faithfulness_agreement_pct": faithfulness_agreement,
        "faithfulness_cohen_kappa": faithfulness_kappa,
    }
    if include_disagreements:
        report["disagreements"] = [
            {
                "case_id": case_id,
                "human_correct": human[case_id][0],
                "human_faithful": human[case_id][1],
                "judge_correct": judge[case_id][0],
                "judge_faithful": judge[case_id][1],
                "human_passed": human_pass[case_id],
                "judge_passed": judge_pass[case_id],
            }
            for case_id in sorted(human_pass)
            if human_pass[case_id] != judge_pass[case_id]
        ]
    return report


def _agreement_metrics(
    human: dict[str, bool], judge: dict[str, bool]
) -> tuple[float, float | None]:
    """Return observed agreement and Cohen's kappa for binary labels."""
    count = len(human)
    observed = sum(human[case_id] == judge[case_id] for case_id in human) / count
    human_positive = sum(human.values()) / count
    judge_positive = sum(judge.values()) / count
    expected = human_positive * judge_positive + (1 - human_positive) * (1 - judge_positive)
    kappa = (observed - expected) / (1 - expected) if expected != 1 else None
    return observed * 100, kappa


def _load_pairs(
    path: Path, correct_column: str, faithful_column: str
) -> dict[str, tuple[bool, bool]]:
    try:
        with path.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            required = {"case_id", correct_column, faithful_column}
            if reader.fieldnames is None or not required.issubset(reader.fieldnames):
                raise CalibrationError(f"{path}: CSV must contain columns {sorted(required)!r}.")
            values: dict[str, tuple[bool, bool]] = {}
            for line_number, row in enumerate(reader, start=2):
                case_id = (row.get("case_id") or "").strip()
                if not case_id:
                    raise CalibrationError(f"{path}:{line_number}: case_id must not be blank.")
                if case_id in values:
                    raise CalibrationError(f"{path}:{line_number}: duplicate case_id {case_id!r}.")
                values[case_id] = (
                    _parse_bool(row.get(correct_column), path, line_number, correct_column),
                    _parse_bool(row.get(faithful_column), path, line_number, faithful_column),
                )
            return values
    except OSError as exc:
        raise CalibrationError(f"Cannot read calibration file {path}: {exc}") from exc


def _parse_bool(value: str | None, path: Path, line: int, column: str) -> bool:
    normalized = (value or "").strip().casefold()
    if normalized == "true":
        return True
    if normalized == "false":
        return False
    raise CalibrationError(f"{path}:{line}: {column} must be filled with true or false.")


def _load_case_ids(path: Path) -> list[str]:
    try:
        with path.open(newline="", encoding="utf-8") as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames is None or "case_id" not in reader.fieldnames:
                raise CalibrationError(f"{path}: CSV must contain a case_id column.")
            case_ids = [(row.get("case_id") or "").strip() for row in reader]
    except OSError as exc:
        raise CalibrationError(f"Cannot read calibration file {path}: {exc}") from exc
    if not case_ids or any(not case_id for case_id in case_ids):
        raise CalibrationError(f"{path}: calibration case IDs must not be blank.")
    if len(case_ids) != len(set(case_ids)):
        raise CalibrationError(f"{path}: calibration case IDs must be unique.")
    return case_ids


def _load_fixtures(path: Path) -> dict[str, CalibrationFixture]:
    try:
        payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise CalibrationError(f"Cannot load calibration fixtures {path}: {exc}") from exc
    if not isinstance(payload, list) or not payload:
        raise CalibrationError(f"{path}: expected a non-empty list of calibration fixtures.")
    fixtures: dict[str, CalibrationFixture] = {}
    for index, item in enumerate(payload, start=1):
        try:
            fixture = CalibrationFixture.model_validate(item)
        except ValidationError as exc:
            raise CalibrationError(f"{path}, fixture {index}: invalid fixture:\n{exc}") from exc
        case_id = fixture.case.id
        if case_id in fixtures:
            raise CalibrationError(f"{path}, fixture {index}: duplicate case ID {case_id!r}.")
        fixtures[case_id] = fixture
    return fixtures


async def _generate_judge_predictions(
    labels_path: Path,
    predictions_path: Path,
    config_path: Path,
    suite_path: Path,
    fixtures_path: Path,
) -> None:
    case_ids = _load_case_ids(labels_path)
    fixtures = _load_fixtures(fixtures_path)
    target_case_ids = set(case_ids) - fixtures.keys()
    all_cases = load_cases(suite_path) if target_case_ids else []
    cases_by_id = {case.id: case for case in all_cases}
    missing = sorted(target_case_ids - cases_by_id.keys())
    if missing:
        raise CalibrationError(f"Calibration cases are missing from the suite: {missing}.")
    cases = [cases_by_id[case_id] for case_id in case_ids if case_id in cases_by_id]
    results = []
    if cases:
        config = load_agent_config(config_path)
        adapter = make_adapter(config, config_path=config_path)
        results = await EvaluationRunner(
            adapter,
            RunnerOptions(timeout_seconds=config.target.timeout_seconds, retries=0),
        ).run_cases(cases)
    judge = create_llm_judge(get_settings())
    results_by_key = {(result.case_id, result.repeat_index): result for result in results}
    predictions: list[dict[str, Any]] = []
    for index, case_id in enumerate(case_ids, start=1):
        print(
            f"Calibrating case {index}/{len(case_ids)}: {case_id}",
            file=sys.stderr,
            flush=True,
        )
        fixture = fixtures.get(case_id)
        final_answer: str | None
        if fixture is not None:
            case = fixture.case
            final_answer = fixture.final_answer
            steps = fixture.steps
        else:
            case = cases_by_id[case_id]
            result = results_by_key[(case.id, 0)]
            final_answer = result.output
            steps = result.steps
        judgment = await judge.evaluate(
            case,
            final_answer=final_answer,
            steps=steps,
        )
        predictions.append(
            {
                "case_id": case_id,
                "judge_correct": str(judgment.correctness).lower(),
                "judge_faithful": str(judgment.faithfulness).lower(),
            }
        )
    predictions_path.parent.mkdir(parents=True, exist_ok=True)
    with predictions_path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=["case_id", "judge_correct", "judge_faithful"],
        )
        writer.writeheader()
        writer.writerows(predictions)


DEFAULT_LABELS_PATH = Path(__file__).with_name("labels.csv")
DEFAULT_PREDICTIONS_PATH = Path(__file__).with_name("judge_predictions.csv")
DEFAULT_FIXTURES_PATH = Path(__file__).with_name("failure_fixtures.yaml")
DEFAULT_SUITE_PATH = Path(__file__).parents[1] / "suite" / "cases"
DEFAULT_CONFIG_PATH = Path("agentprobe.yaml")


def get_latest_calibration(
    labels_path: Path | None = None,
    predictions_path: Path | None = None,
) -> dict[str, Any] | None:
    labels = labels_path or DEFAULT_LABELS_PATH
    predictions = predictions_path or DEFAULT_PREDICTIONS_PATH
    if not labels.is_file() or not predictions.is_file():
        return None
    try:
        return calculate_agreement(labels, predictions, include_disagreements=True)
    except CalibrationError:
        return None


async def perform_calibration(
    *,
    labels_path: Path | None = None,
    predictions_path: Path | None = None,
    config_path: Path | None = None,
    suite_path: Path | None = None,
    fixtures_path: Path | None = None,
) -> dict[str, Any]:
    labels = labels_path or DEFAULT_LABELS_PATH
    predictions = predictions_path or DEFAULT_PREDICTIONS_PATH
    cfg = config_path or DEFAULT_CONFIG_PATH
    suite = suite_path or DEFAULT_SUITE_PATH
    fixtures = fixtures_path or DEFAULT_FIXTURES_PATH

    await _generate_judge_predictions(
        labels,
        predictions,
        cfg,
        suite,
        fixtures,
    )
    return calculate_agreement(labels, predictions, include_disagreements=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--labels",
        type=Path,
        default=Path(__file__).with_name("labels.csv"),
        help="human labels CSV (default: backend/calibration/labels.csv)",
    )
    parser.add_argument(
        "--predictions",
        type=Path,
        help="existing judge output CSV with case_id,judge_correct,judge_faithful columns",
    )
    parser.add_argument(
        "--run-judge",
        action="store_true",
        help="run configured target on calibration cases and write judge predictions",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="prediction output path for --run-judge (default: labels sibling CSV)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("agentprobe.yaml"),
        help="agent target config used with --run-judge",
    )
    parser.add_argument(
        "--suite",
        type=Path,
        default=Path(__file__).parents[1] / "suite" / "cases",
        help="case suite used with --run-judge",
    )
    parser.add_argument(
        "--fixtures",
        type=Path,
        default=Path(__file__).with_name("failure_fixtures.yaml"),
        help="known-output synthetic failure fixtures",
    )
    args = parser.parse_args(argv)
    predictions_path: Path | None = args.predictions
    if args.run_judge:
        predictions_path = args.output or args.labels.with_name("judge_predictions.csv")
        try:
            asyncio.run(
                _generate_judge_predictions(
                    args.labels,
                    predictions_path,
                    args.config,
                    args.suite,
                    args.fixtures,
                )
            )
        except (CalibrationError, ValueError) as exc:
            parser.error(str(exc))
    elif predictions_path is None:
        parser.error("provide --predictions or use --run-judge.")
    try:
        report = calculate_agreement(args.labels, predictions_path)
    except CalibrationError as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
