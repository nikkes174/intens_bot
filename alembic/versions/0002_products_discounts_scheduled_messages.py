"""Add product identity, bundles, discounted order lines, and scheduled messages.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-18
"""

import sqlalchemy as sa

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("trainings", sa.Column("product_code", sa.String(length=50), nullable=True))
    op.add_column(
        "trainings",
        sa.Column("is_bundle", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_trainings_product_code", "trainings", ["product_code"], unique=True)

    op.add_column(
        "orders",
        sa.Column("discount_percent", sa.Numeric(5, 2), nullable=False, server_default="0.00"),
    )
    op.add_column(
        "orders",
        sa.Column("discount_amount", sa.Numeric(10, 2), nullable=False, server_default="0.00"),
    )
    op.add_column("orders", sa.Column("checkout_key", sa.String(length=255), nullable=True))
    op.add_column("orders", sa.Column("payment_url", sa.String(length=1000), nullable=True))
    op.add_column(
        "orders",
        sa.Column(
            "delivery_status", sa.String(length=20), nullable=False, server_default="pending"
        ),
    )
    op.create_index("ix_orders_checkout_key", "orders", ["checkout_key"])
    op.create_index(
        "uq_orders_pending_checkout_key",
        "orders",
        ["checkout_key"],
        unique=True,
        sqlite_where=sa.text("status = 'pending' AND checkout_key IS NOT NULL"),
        postgresql_where=sa.text("status = 'pending' AND checkout_key IS NOT NULL"),
    )

    op.add_column("outbox_events", sa.Column("dedupe_key", sa.String(length=255), nullable=True))
    op.create_index("ix_outbox_events_dedupe_key", "outbox_events", ["dedupe_key"], unique=True)

    op.create_table(
        "product_components",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "bundle_id",
            sa.Integer(),
            sa.ForeignKey("trainings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "training_id",
            sa.Integer(),
            sa.ForeignKey("trainings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.UniqueConstraint("bundle_id", "training_id", name="uq_bundle_component"),
    )
    op.create_index("ix_product_components_bundle_id", "product_components", ["bundle_id"])
    op.create_index("ix_product_components_training_id", "product_components", ["training_id"])

    op.create_table(
        "order_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "training_id",
            sa.Integer(),
            sa.ForeignKey("trainings.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("item_price", sa.Numeric(10, 2), nullable=False),
        sa.Column("is_discounted", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_order_items_order_id", "order_items", ["order_id"])
    op.create_index("ix_order_items_training_id", "order_items", ["training_id"])

    op.create_table(
        "scheduled_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("dedupe_key", sa.String(length=255), nullable=False, unique=True),
        sa.Column(
            "user_id", sa.Integer(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column(
            "order_id", sa.Integer(), sa.ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
        ),
        sa.Column(
            "training_id",
            sa.Integer(),
            sa.ForeignKey("trainings.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("message_type", sa.String(length=40), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_scheduled_messages_user_id", "scheduled_messages", ["user_id"])
    op.create_index("ix_scheduled_messages_order_id", "scheduled_messages", ["order_id"])
    op.create_index("ix_scheduled_messages_training_id", "scheduled_messages", ["training_id"])
    op.create_index("ix_scheduled_messages_message_type", "scheduled_messages", ["message_type"])
    op.create_index("ix_scheduled_messages_scheduled_at", "scheduled_messages", ["scheduled_at"])
    op.create_index("ix_scheduled_status_time", "scheduled_messages", ["status", "scheduled_at"])


def downgrade() -> None:
    op.drop_table("scheduled_messages")
    op.drop_table("order_items")
    op.drop_table("product_components")
    op.drop_index("ix_outbox_events_dedupe_key", table_name="outbox_events")
    op.drop_column("outbox_events", "dedupe_key")
    op.drop_index("ix_orders_checkout_key", table_name="orders")
    op.drop_index("uq_orders_pending_checkout_key", table_name="orders")
    op.drop_column("orders", "delivery_status")
    op.drop_column("orders", "payment_url")
    op.drop_column("orders", "checkout_key")
    op.drop_column("orders", "discount_amount")
    op.drop_column("orders", "discount_percent")
    op.drop_index("ix_trainings_product_code", table_name="trainings")
    op.drop_column("trainings", "is_bundle")
    op.drop_column("trainings", "product_code")
