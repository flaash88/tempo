"""Initial schema — activities, streams, laps, wellness, load, fitness,
athlete settings, AI call log and sync log.

Revision ID: 0001
Revises: -
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "activity",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("start_local", sa.DateTime(), nullable=False),
        sa.Column("sport", sa.String(length=32), nullable=False),
        sa.Column("distance_m", sa.Float(), nullable=True),
        sa.Column("moving_s", sa.Integer(), nullable=True),
        sa.Column("elapsed_s", sa.Integer(), nullable=True),
        sa.Column("elevation_gain_m", sa.Float(), nullable=True),
        sa.Column("avg_hr", sa.Integer(), nullable=True),
        sa.Column("max_hr", sa.Integer(), nullable=True),
        sa.Column("avg_pace_s_per_km", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("fit_path", sa.Text(), nullable=True),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_activity")),
    )
    with op.batch_alter_table("activity", schema=None) as batch_op:
        batch_op.create_index("ix_activity_start_local", ["start_local"], unique=False)

    op.create_table(
        "ai_call",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("endpoint", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_eur", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ai_call")),
    )
    with op.batch_alter_table("ai_call", schema=None) as batch_op:
        batch_op.create_index("ix_ai_call_ts", ["ts"], unique=False)

    op.create_table(
        "athlete_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("hr_max", sa.Integer(), nullable=True),
        sa.Column("hr_rest", sa.Integer(), nullable=True),
        sa.Column("lthr", sa.Integer(), nullable=True),
        sa.Column("threshold_pace_s_per_km", sa.Float(), nullable=True),
        sa.Column("zone_model", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_athlete_settings_singleton")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_athlete_settings")),
    )
    op.create_table(
        "daily_load",
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("trimp", sa.Float(), nullable=True),
        sa.Column("hr_tss", sa.Float(), nullable=True),
        sa.Column("r_tss", sa.Float(), nullable=True),
        sa.Column("duration_s", sa.Integer(), nullable=False),
        sa.Column("distance_m", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("date", name=op.f("pk_daily_load")),
    )
    op.create_table(
        "fitness_day",
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("ctl", sa.Float(), nullable=True),
        sa.Column("atl", sa.Float(), nullable=True),
        sa.Column("tsb", sa.Float(), nullable=True),
        sa.Column("acwr", sa.Float(), nullable=True),
        sa.Column("monotony", sa.Float(), nullable=True),
        sa.Column("strain", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("days_of_history", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name=op.f("ck_fitness_day_confidence_range"),
        ),
        sa.PrimaryKeyConstraint("date", name=op.f("pk_fitness_day")),
    )
    op.create_table(
        "sync_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sync_log")),
    )
    with op.batch_alter_table("sync_log", schema=None) as batch_op:
        batch_op.create_index(
            "ix_sync_log_source_started_at", ["source", "started_at"], unique=False
        )

    op.create_table(
        "wellness_day",
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("resting_hr", sa.Integer(), nullable=True),
        sa.Column("hrv_rmssd", sa.Float(), nullable=True),
        sa.Column("sleep_secs", sa.Integer(), nullable=True),
        sa.Column("sleep_score", sa.Integer(), nullable=True),
        sa.Column("vo2max", sa.Float(), nullable=True),
        sa.Column("weight_kg", sa.Float(), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("date", name=op.f("pk_wellness_day")),
    )
    op.create_table(
        "activity_stream",
        sa.Column("activity_id", sa.String(length=64), nullable=False),
        sa.Column("offset_s", sa.Integer(), nullable=False),
        sa.Column("hr", sa.Integer(), nullable=True),
        sa.Column("speed_m_s", sa.Float(), nullable=True),
        sa.Column("altitude_m", sa.Float(), nullable=True),
        sa.Column("cadence", sa.Integer(), nullable=True),
        sa.Column("power", sa.Integer(), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["activity_id"],
            ["activity.id"],
            name=op.f("fk_activity_stream_activity_id_activity"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "activity_id", "offset_s", name=op.f("pk_activity_stream")
        ),
    )
    op.create_table(
        "lap",
        sa.Column("activity_id", sa.String(length=64), nullable=False),
        sa.Column("index", sa.Integer(), nullable=False),
        sa.Column("distance_m", sa.Float(), nullable=True),
        sa.Column("duration_s", sa.Integer(), nullable=True),
        sa.Column("avg_hr", sa.Integer(), nullable=True),
        sa.Column("avg_pace_s_per_km", sa.Float(), nullable=True),
        sa.ForeignKeyConstraint(
            ["activity_id"],
            ["activity.id"],
            name=op.f("fk_lap_activity_id_activity"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("activity_id", "index", name=op.f("pk_lap")),
    )


def downgrade() -> None:
    op.drop_table("lap")
    op.drop_table("activity_stream")
    op.drop_table("wellness_day")
    with op.batch_alter_table("sync_log", schema=None) as batch_op:
        batch_op.drop_index("ix_sync_log_source_started_at")

    op.drop_table("sync_log")
    op.drop_table("fitness_day")
    op.drop_table("daily_load")
    op.drop_table("athlete_settings")
    with op.batch_alter_table("ai_call", schema=None) as batch_op:
        batch_op.drop_index("ix_ai_call_ts")

    op.drop_table("ai_call")
    with op.batch_alter_table("activity", schema=None) as batch_op:
        batch_op.drop_index("ix_activity_start_local")

    op.drop_table("activity")
