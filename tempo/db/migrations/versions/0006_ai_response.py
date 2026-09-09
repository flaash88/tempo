"""The answer cache, and the cache token columns on ``ai_call``.

``ai_response`` stores what the model said next to the feature document it
said it about, keyed on everything that produced the answer. The two new
columns on ``ai_call`` separate cache reads and writes from plain input
tokens: they are billed at different rates, and a month of calls with no
cache reads is the only visible sign that the cached prefix is not being
hit.

Revision ID: 0006
Revises: 0005
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_response",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("cache_key", sa.String(length=64), nullable=False),
        sa.Column("endpoint", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=True),
        sa.Column("features_json", sa.Text(), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("cache_key"),
    )
    op.create_index(
        "ix_ai_response_endpoint_created", "ai_response", ["endpoint", "created_at"]
    )

    with op.batch_alter_table("ai_call", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "cache_read_tokens",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(
            sa.Column(
                "cache_write_tokens",
                sa.Integer(),
                nullable=False,
                server_default="0",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("ai_call", schema=None) as batch_op:
        batch_op.drop_column("cache_write_tokens")
        batch_op.drop_column("cache_read_tokens")

    op.drop_index("ix_ai_response_endpoint_created", table_name="ai_response")
    op.drop_table("ai_response")
