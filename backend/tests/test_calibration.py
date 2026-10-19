"""Tests for the calibration agreement and Cohen's kappa calculation."""

import csv
from pathlib import Path

import pytest

from backend.app.scorers.llm_judge import JudgeResponse
from backend.calibration.agreement import (
    CalibrationError,
    _generate_judge_predictions,
    _load_fixtures,
    calculate_agreement,
)


def test_calibration_reports_observed_agreement_and_cohen_kappa(tmp_path: Path) -> None:
    labels = tmp_path / "labels.csv"
    labels.write_text(
        "case_id,human_correct,human_faithful\na,true,true\nb,true,true\nc,false,true\n",
        encoding="utf-8",
    )
    predictions = tmp_path / "predictions.csv"
    predictions.write_text(
        "case_id,judge_correct,judge_faithful\na,true,true\nb,false,true\nc,false,true\n",
        encoding="utf-8",
    )
    report = calculate_agreement(labels, predictions)
    assert report == {
        "cases": 3,
        "agreements": 2,
        "agreement_pct": pytest.approx(200 / 3),
        "cohen_kappa": pytest.approx(0.4),
        "correctness_agreement_pct": pytest.approx(200 / 3),
        "correctness_cohen_kappa": pytest.approx(0.4),
        "faithfulness_agreement_pct": 100.0,
        "faithfulness_cohen_kappa": None,
    }


def test_calibration_rejects_unlabeled_cells_and_case_mismatch(tmp_path: Path) -> None:
    labels = tmp_path / "labels.csv"
    labels.write_text(
        "case_id,human_correct,human_faithful\na,true,\n",
        encoding="utf-8",
    )
    predictions = tmp_path / "predictions.csv"
    predictions.write_text(
        "case_id,judge_correct,judge_faithful\na,true,true\n",
        encoding="utf-8",
    )
    with pytest.raises(CalibrationError, match="human_faithful must be filled"):
        calculate_agreement(labels, predictions)


def test_failure_fixtures_have_seven_known_nonpassing_examples() -> None:
    fixture_path = Path(__file__).parents[1] / "calibration" / "failure_fixtures.yaml"
    fixtures = _load_fixtures(fixture_path)
    assert len(fixtures) == 7
    assert sum(not item.ground_truth.correct for item in fixtures.values()) == 6
    assert sum(not item.ground_truth.faithful for item in fixtures.values()) == 7
    labels_path = Path(__file__).parents[1] / "calibration" / "labels.csv"
    with labels_path.open(newline="", encoding="utf-8") as stream:
        labels = {row["case_id"]: row for row in csv.DictReader(stream)}
    assert len(labels) == 20
    assert (
        sum(
            row["human_correct"] == "true" and row["human_faithful"] == "true"
            for row in labels.values()
        )
        == 13
    )
    assert (
        sum(
            row["human_correct"] == "false" or row["human_faithful"] == "false"
            for row in labels.values()
        )
        == 7
    )
    for case_id, fixture in fixtures.items():
        assert labels[case_id]["human_correct"] == str(fixture.ground_truth.correct).lower()
        assert labels[case_id]["human_faithful"] == str(fixture.ground_truth.faithful).lower()


async def test_calibration_judges_synthetic_fixtures_without_running_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    labels = tmp_path / "labels.csv"
    labels.write_text(
        "case_id,human_correct,human_faithful\n"
        "calibration-wrong-order-status,false,false\n"
        "calibration-ungrounded-policy,true,false\n",
        encoding="utf-8",
    )

    class JudgeStub:
        async def evaluate(
            self, case: object, *, final_answer: str, steps: list[object]
        ) -> JudgeResponse:
            assert final_answer
            return JudgeResponse(
                correctness=False,
                faithfulness=False,
                reason="Fixture has a deliberate failure.",
            )

    monkeypatch.setattr(
        "backend.calibration.agreement.create_llm_judge",
        lambda _settings: JudgeStub(),
    )
    predictions = tmp_path / "predictions.csv"
    await _generate_judge_predictions(
        labels,
        predictions,
        config_path=tmp_path / "unused-config.yaml",
        suite_path=tmp_path / "unused-suite",
        fixtures_path=Path(__file__).parents[1] / "calibration" / "failure_fixtures.yaml",
    )
    assert predictions.read_text(encoding="utf-8").splitlines() == [
        "case_id,judge_correct,judge_faithful",
        "calibration-wrong-order-status,false,false",
        "calibration-ungrounded-policy,false,false",
    ]
    assert "Calibrating case 1/2: calibration-wrong-order-status" in capsys.readouterr().err


def test_calibration_disagreements_reporting(tmp_path: Path) -> None:
    labels = tmp_path / "labels.csv"
    labels.write_text(
        "case_id,human_correct,human_faithful\na,true,true\nb,true,true\n",
        encoding="utf-8",
    )
    predictions = tmp_path / "predictions.csv"
    predictions.write_text(
        "case_id,judge_correct,judge_faithful\na,true,true\nb,false,true\n",
        encoding="utf-8",
    )
    report = calculate_agreement(labels, predictions, include_disagreements=True)
    assert "disagreements" in report
    assert len(report["disagreements"]) == 1
    assert report["disagreements"][0]["case_id"] == "b"
    assert report["disagreements"][0]["human_passed"] is True
    assert report["disagreements"][0]["judge_passed"] is False
