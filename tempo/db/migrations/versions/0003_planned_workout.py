"""Planned workouts from the source's calendar, and the provenance of the
heart rate variability value.

``wellness_day.hrv_rmssd`` becomes ``wellness_day.hrv`` plus
``hrv_source_field``: which HRV measure a value is depends on the source,
so the source's own field name travels with it instead of the column name
asserting a metric nothing has verified.

Revision ID: 0003
Revises: 0002
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "planned_workout",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=True),
        sa.Column("sport", sa.String(length=32), nullable=True),
        sa.Column("name", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("target_time_s", sa.Integer(), nullable=True),
        sa.Column("target_dist_m", sa.Float(), nullable=True),
        sa.Column("target_load", sa.Float(), nullable=True),
        sa.Column("workout_doc", sa.JSON(), nullable=True),
        sa.Column("external_id", sa.String(length=64), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_planned_workout")),
        sa.UniqueConstraint("external_id", name="uq_planned_workout_external_id"),
    )
    with op.batch_alter_table("planned_workout", schema=None) as batch_op:
        batch_op.create_index("ix_planned_workout_date", ["date"], unique=False)

    with op.batch_alter_table("wellness_day", schema=None) as batch_op:
        batch_op.alter_column("hrv_rmssd", new_column_name="hrv")
        batch_op.add_column(
            sa.Column("hrv_source_field", sa.String(length=32), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("wellness_day", schema=None) as batch_op:
        batch_op.drop_column("hrv_source_field")
        batch_op.alter_column("hrv", new_column_name="hrv_rmssd")

    with op.batch_alter_table("planned_workout", schema=None) as batch_op:
        batch_op.drop_index("ix_planned_workout_date")

    op.drop_table("planned_workout")
