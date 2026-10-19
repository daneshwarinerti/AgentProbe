"""Tests for YAML loading errors and duplicate case validation."""

import csv
import json
from pathlib import Path

import pytest

from backend.calibration.agreement import _load_fixtures
from backend.suite.loader import CaseLoadError, load_cases


def test_loads_current_suite_and_twenty_calibration_ids() -> None:
    case_directory = Path(__file__).parents[1] / "suite" / "cases"
    cases = load_cases(case_directory)
    assert len(cases) == 65
    assert len({case.id for case in cases}) == len(cases)
    expectation_signatures = {
        json.dumps(
            {
                "tools": [(tool.name, tool.args) for tool in case.expected_tools],
                "terms": case.expected_answer_contains,
                "forbidden": [
                    (condition.calls_tool, condition.reveals) for condition in case.forbidden
                ],
                "category": case.category,
            },
            sort_keys=True,
        )
        for case in cases
    }
    assert len(expectation_signatures) == 50
    calibration_path = Path(__file__).parents[1] / "calibration" / "labels.csv"
    with calibration_path.open(newline="", encoding="utf-8") as stream:
        calibration_ids = {row["case_id"] for row in csv.DictReader(stream)}
    assert len(calibration_ids) == 20
    fixture_path = Path(__file__).parents[1] / "calibration" / "failure_fixtures.yaml"
    assert calibration_ids <= {case.id for case in cases} | _load_fixtures(fixture_path).keys()


def test_invalid_yaml_error_includes_file_path(tmp_path: Path) -> None:
    file_path = tmp_path / "bad.yaml"
    file_path.write_text("id: [", encoding="utf-8")
    with pytest.raises(CaseLoadError, match=r"bad.yaml: invalid YAML"):
        load_cases(file_path)


def test_invalid_case_error_includes_file_and_case_number(tmp_path: Path) -> None:
    file_path = tmp_path / "bad_case.yaml"
    file_path.write_text(
        "id: invalid\ninput: hello\nforbidden:\n  - calls_tool: delete_order\n"
        "    reveals: private\n",
        encoding="utf-8",
    )
    with pytest.raises(CaseLoadError, match=r"bad_case.yaml, case 1: invalid case data"):
        load_cases(file_path)


def test_duplicate_ids_are_rejected_across_files(tmp_path: Path) -> None:
    for filename in ("first.yaml", "second.yaml"):
        (tmp_path / filename).write_text("id: repeated\ninput: hello\n", encoding="utf-8")
    with pytest.raises(CaseLoadError, match="duplicate case id 'repeated'"):
        load_cases(tmp_path)
