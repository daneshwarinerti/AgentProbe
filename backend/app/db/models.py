"""SQLAlchemy rows for targets, run history, and approved baselines."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.db.database import Base

__all__ = [
    "Base",
    "RunRecord",
    "BaselineRecord",
    "ComparisonRecord",
    "TargetRecord",
    "SuiteCaseRecord",
]


class TargetRecord(Base):
    """Uploaded local project metadata and private environment configuration."""

    __tablename__ = "targets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
    name: Mapped[str] = mapped_column(String(255))
    entry_point: Mapped[str] = mapped_column(String(512))
    input_mapping_json: Mapped[str] = mapped_column(Text)
    output_mapping_json: Mapped[str] = mapped_column(Text)
    env_json: Mapped[str] = mapped_column(Text)
    agent_names_json: Mapped[str] = mapped_column(Text, default="[]")
    setup_status: Mapped[str] = mapped_column(String(32), default="uploaded")


class SuiteCaseRecord(Base):
    """Editable local test case plus review and provenance metadata."""

    __tablename__ = "suite_cases"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    target_id: Mapped[str | None] = mapped_column(
        ForeignKey("targets.id"), nullable=True, index=True
    )
    case_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="approved", nullable=False)
    provenance: Mapped[str] = mapped_column(String(32), default="manual", nullable=False)
    human_reviewed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )


class RunRecord(Base):
    """One completed API evaluation and its serialized case traces."""

    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
    suite: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32))
    summary_json: Mapped[str] = mapped_column(Text)
    results_json: Mapped[str] = mapped_column(Text)


class BaselineRecord(Base):
    """The approved baseline is a pointer to a previously persisted run."""

    __tablename__ = "baselines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), unique=True)
    promoted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )


class ComparisonRecord(Base):
    """Persisted side-by-side model comparison and per-case results."""

    __tablename__ = "model_comparisons"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC), index=True
    )
    suite: Mapped[str] = mapped_column(String(128))
    repeats: Mapped[int] = mapped_column(Integer)
    result_json: Mapped[str] = mapped_column(Text)
