"""Persist uploaded target metadata and private environment values."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20261017_0003"
down_revision: str | None = "20260624_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "targets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("entry_point", sa.String(length=512), nullable=False),
        sa.Column("input_mapping_json", sa.Text(), nullable=False),
        sa.Column("output_mapping_json", sa.Text(), nullable=False),
        sa.Column("env_json", sa.Text(), nullable=False),
        sa.Column("agent_names_json", sa.Text(), nullable=False),
        sa.Column("setup_status", sa.String(length=32), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_targets_created_at", "targets", ["created_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_targets_created_at", table_name="targets")
    op.drop_table("targets")
