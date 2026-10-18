"""Persist payment metadata generation and scheduled-message claim times.

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-18
"""

import sqlalchemy as sa

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "orders", sa.Column("payment_metadata_version", sa.String(length=8), nullable=True)
    )
    op.execute("UPDATE orders SET payment_metadata_version = 'legacy'")
    op.add_column("scheduled_messages", sa.Column("claimed_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    op.drop_column("scheduled_messages", "claimed_at")
    op.drop_column("orders", "payment_metadata_version")
