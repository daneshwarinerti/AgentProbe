"""Persist model comparison runs."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260624_0002"
down_revision: str | None = "20260220_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "model_comparisons",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("suite", sa.String(length=128), nullable=False),
        sa.Column("repeats", sa.Integer(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_model_comparisons_created_at", "model_comparisons", ["created_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_model_comparisons_created_at", table_name="model_comparisons")
    op.drop_table("model_comparisons")
