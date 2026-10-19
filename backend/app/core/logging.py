"""Structured JSON logging configuration."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any


class JsonFormatter(logging.Formatter):
    """Format log records as one JSON object per line."""

    _standard_fields = frozenset(logging.makeLogRecord({}).__dict__.keys()) | frozenset(
        {"message", "asctime"}
    )

    def format(self, record: logging.LogRecord) -> str:
        data: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in self._standard_fields and not key.startswith("_"):
                data[key] = value
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        return json.dumps(data, default=str, ensure_ascii=True)


def configure_logging(level: str = "INFO") -> None:
    """Install a JSON stream handler without replacing application handlers."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    if not any(isinstance(handler.formatter, JsonFormatter) for handler in root.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
