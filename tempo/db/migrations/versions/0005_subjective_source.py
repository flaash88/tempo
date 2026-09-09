"""Where the subjective readings came from.

``wellness_day.source`` says where the row came from, which stops being one
answer as soon as the athlete can enter fatigue, soreness and mood on a day
whose measurements arrived from intervals.icu. This column carries the
provenance of those three fields on their own, the same way
``hrv_source_field`` carries the provenance of the HRV value: recorded, not
interpreted.

Revision ID: 0005
Revises: 0004
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("wellness_day", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("subjective_source", sa.String(length=16), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("wellness_day", schema=None) as batch_op:
        batch_op.drop_column("subjective_source")
