"""Initial schema

Revision ID: 0001
Revises:
Create Date: 2024-01-01 00:00:00.000000

"""
import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Users table
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "telegram_user_id",
            sa.BigInteger(),
            nullable=False,
            unique=True,
            index=True,
        ),
        sa.Column("username", sa.String(length=255), nullable=True),
        sa.Column("email", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.Date(), nullable=True),
        sa.Column("payment_id", sa.String(length=64), nullable=True),
        sa.Column("payment_ok", sa.Boolean(), nullable=False, default=False),
    )

    # Trainings table
    op.create_table(
        "trainings",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("price", sa.Numeric(10, 2), nullable=False, default=0),
        sa.Column("currency", sa.String(length=3), nullable=False, default="RUB"),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, default=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, default=0),
        sa.Column("created_at", sa.Date(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("telegram_chat_id", name="uq_training_chat_id"),
    )
    op.create_index(
        "ix_trainings_active_sort", "trainings", ["is_active", "sort_order"]
    )

    # Training materials table
    op.create_table(
        "training_materials",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "training_id",
            sa.Integer(),
            sa.ForeignKey("trainings.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("material_type", sa.String(length=20), nullable=False),
        sa.Column("telegram_file_id", sa.String(length=255), nullable=True),
        sa.Column("telegram_file_unique_id", sa.String(length=255), nullable=True),
        sa.Column("file_name", sa.String(length=255), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("text_content", sa.Text(), nullable=True),
        sa.Column("sort_order", sa.Integer(), nullable=False, default=0),
        sa.Column("created_at", sa.Date(), nullable=True),
    )

    # Orders table
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "training_id",
            sa.Integer(),
            sa.ForeignKey("trainings.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("payment_id", sa.String(length=64), nullable=True, index=True),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, default="RUB"),
        sa.Column("status", sa.String(length=20), nullable=False, default="pending"),
        sa.Column("customer_email", sa.String(length=255), nullable=True),
        sa.Column("idempotency_key", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("paid_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("payment_id", name="uq_order_payment_id"),
    )
    op.create_index("ix_orders_user_status", "orders", ["user_id", "status"])
    op.create_index(
        "ix_orders_training_status", "orders", ["training_id", "status"]
    )

    # User training access table
    op.create_table(
        "user_training_access",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "training_id",
            sa.Integer(),
            sa.ForeignKey("trainings.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "order_id",
            sa.Integer(),
            sa.ForeignKey("orders.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("is_active", sa.Boolean(), nullable=False, default=True),
        sa.Column("granted_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("user_id", "training_id", name="uq_user_training_access"),
    )

    # Material deliveries table
    op.create_table(
        "material_deliveries",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "training_id",
            sa.Integer(),
            sa.ForeignKey("trainings.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "material_id",
            sa.Integer(),
            sa.ForeignKey("training_materials.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("status", sa.String(length=20), nullable=False, default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, default=0),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.UniqueConstraint("user_id", "material_id", name="uq_material_delivery"),
    )

    # Outbox events table
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "event_type",
            sa.String(length=50),
            nullable=False,
            index=True,
        ),
        sa.Column("payload", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, default="pending"),
        sa.Column("attempts", sa.Integer(), nullable=False, default=0),
        sa.Column("max_attempts", sa.Integer(), nullable=False, default=5),
        sa.Column("next_attempt_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_outbox_status_next", "outbox_events", ["status", "next_attempt_at"]
    )

    # Invite links table
    op.create_table(
        "invite_links",
        sa.Column("id", sa.Integer(), primary_key=True, index=True),
        sa.Column(
            "training_id",
            sa.Integer(),
            sa.ForeignKey("trainings.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "invite_link",
            sa.String(length=255),
            nullable=False,
        ),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "creates_join_request",
            sa.Boolean(),
            nullable=False,
            default=True,
        ),
        sa.Column("expire_date", sa.DateTime(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, default=True),
        sa.Column("created_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("invite_links")
    op.drop_table("outbox_events")
    op.drop_table("material_deliveries")
    op.drop_table("user_training_access")
    op.drop_table("orders")
    op.drop_table("training_materials")
    op.drop_table("trainings")
    op.drop_table("users")
