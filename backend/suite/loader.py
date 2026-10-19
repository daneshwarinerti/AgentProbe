"""Load and validate one or more YAML evaluation case files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from backend.suite.schema import TestCase


class CaseLoadError(ValueError):
    """A case file could not be parsed or validated."""


def load_cases(path: Path) -> list[TestCase]:
    """Load cases from one YAML file or all YAML files in a directory."""
    files = [path] if path.is_file() else sorted(path.glob("*.yaml")) + sorted(path.glob("*.yml"))
    if not files:
        raise CaseLoadError(f"No YAML case files found at {path}.")
    cases: list[TestCase] = []
    seen_ids: set[str] = set()
    for file_path in files:
        try:
            with file_path.open("r", encoding="utf-8") as stream:
                payload: Any = yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            raise CaseLoadError(f"{file_path}: invalid YAML: {exc}") from exc
        entries = payload if isinstance(payload, list) else [payload]
        if not isinstance(entries, list) or not entries:
            raise CaseLoadError(f"{file_path}: expected a case mapping or non-empty list of cases.")
        for index, entry in enumerate(entries, start=1):
            try:
                case = TestCase.model_validate(entry)
            except ValidationError as exc:
                raise CaseLoadError(
                    f"{file_path}, case {index}: invalid case data:\n{exc}"
                ) from exc
            if case.id in seen_ids:
                raise CaseLoadError(f"{file_path}, case {index}: duplicate case id {case.id!r}.")
            seen_ids.add(case.id)
            cases.append(case)
    return cases
