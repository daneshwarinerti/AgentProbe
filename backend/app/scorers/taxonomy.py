"""Stable failure labels shared by rule and judge scorers."""

from __future__ import annotations

from typing import Literal

FailureCategory = Literal[
    "wrong_tool",
    "bad_args",
    "hallucination",
    "loop",
    "unsafe_output",
    "execution_error",
]
