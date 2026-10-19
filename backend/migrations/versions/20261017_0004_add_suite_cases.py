"""Persist editable and reviewable evaluation cases."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261017_0004"
down_revision: str | None = "20261017_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "suite_cases",
        sa.Column("id", sa.String(length=128), nullable=False),
        sa.Column("target_id", sa.String(length=36), nullable=True),
        sa.Column("case_json", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("provenance", sa.String(length=32), nullable=False),
        sa.Column("human_reviewed", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["target_id"], ["targets.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_suite_cases_created_at", "suite_cases", ["created_at"], unique=False)
    op.create_index("ix_suite_cases_target_id", "suite_cases", ["target_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_suite_cases_target_id", table_name="suite_cases")
    op.drop_index("ix_suite_cases_created_at", table_name="suite_cases")
    op.drop_table("suite_cases")
