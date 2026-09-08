"""Subjective daily form, and the athlete's own sleep target.

``fatigue``, ``soreness`` and ``mood`` are stored and read by nothing yet.
They exist because the readiness weights are a choice, and the only way to
find out whether they are the right choice is to compare the score against
how the athlete actually felt — which needs the felt part on record from now
on rather than from whenever an evaluation gets written.

``athlete_settings.sleep_target_s`` replaces the fixed eight hours in the
sleep term with the athlete's own target; ``NULL`` keeps the default.

Revision ID: 0004
Revises: 0003
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("wellness_day", schema=None) as batch_op:
        batch_op.add_column(sa.Column("fatigue", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("soreness", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("mood", sa.Integer(), nullable=True))

    with op.batch_alter_table("athlete_settings", schema=None) as batch_op:
        batch_op.add_column(sa.Column("sleep_target_s", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("athlete_settings", schema=None) as batch_op:
        batch_op.drop_column("sleep_target_s")

    with op.batch_alter_table("wellness_day", schema=None) as batch_op:
        batch_op.drop_column("mood")
        batch_op.drop_column("soreness")
        batch_op.drop_column("fatigue")
