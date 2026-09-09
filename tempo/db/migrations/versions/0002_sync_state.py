"""Incremental sync watermark, plus the two wellness columns that only the
optional Garmin connector can fill.

Revision ID: 0002
Revises: 0001
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "sync_state",
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("last_activity_start", sa.DateTime(), nullable=True),
        sa.Column("last_wellness_date", sa.Date(), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("source", name=op.f("pk_sync_state")),
    )
    with op.batch_alter_table("wellness_day", schema=None) as batch_op:
        batch_op.add_column(sa.Column("body_battery", sa.Integer(), nullable=True))
        batch_op.add_column(
            sa.Column("training_readiness", sa.Integer(), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("wellness_day", schema=None) as batch_op:
        batch_op.drop_column("training_readiness")
        batch_op.drop_column("body_battery")

    op.drop_table("sync_state")
