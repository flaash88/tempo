"""Write-back bookkeeping on ``planned_workout``.

Phase 2 deliberately left these columns out: pushing sessions to the watch
was not built yet, and a status column that nothing sets is a column that
lies. Now something sets them.

``confirmed_at`` is what keeps anything from going out unasked, and
``remote_event_id`` is what keeps a change from creating a second event
next to the first instead of updating it.

Revision ID: 0007
Revises: 0006
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("planned_workout", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "sync_status",
                sa.String(length=16),
                nullable=False,
                server_default="not_sent",
            )
        )
        batch_op.add_column(
            sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("remote_event_id", sa.String(length=64), nullable=True)
        )
        batch_op.add_column(sa.Column("sync_error", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("planned_workout", schema=None) as batch_op:
        batch_op.drop_column("sync_error")
        batch_op.drop_column("remote_event_id")
        batch_op.drop_column("synced_at")
        batch_op.drop_column("sync_status")
        batch_op.drop_column("confirmed_at")
