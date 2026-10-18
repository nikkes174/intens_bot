from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tgbot.db.db import Base


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger, unique=True, nullable=False, index=True
    )
    username: Mapped[str | None] = mapped_column(String(255))
    email: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[date | None] = mapped_column(Date)

    # Legacy fields preserved from the original schema
    payment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    payment_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    orders = relationship("OrderModel", back_populates="user", lazy="selectin")
    accesses = relationship("UserTrainingAccessModel", back_populates="user", lazy="selectin")


class TrainingModel(Base):
    __tablename__ = "trainings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    product_code: Mapped[str | None] = mapped_column(String(50), unique=True, nullable=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False, default=Decimal("0"))
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RUB")
    telegram_chat_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    is_bundle: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[date | None] = mapped_column(Date, default=date.today)
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime, nullable=True, onupdate=datetime.utcnow
    )

    materials = relationship(
        "TrainingMaterialModel",
        back_populates="training",
        lazy="selectin",
        cascade="all, delete-orphan",
        order_by="TrainingMaterialModel.sort_order",
    )
    orders = relationship("OrderModel", back_populates="training", lazy="selectin")
    accesses = relationship("UserTrainingAccessModel", back_populates="training", lazy="selectin")
    bundle_components = relationship(
        "ProductComponentModel",
        foreign_keys="ProductComponentModel.bundle_id",
        lazy="selectin",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint("telegram_chat_id", name="uq_training_chat_id"),
        Index("ix_trainings_active_sort", "is_active", "sort_order"),
    )


class TrainingMaterialModel(Base):
    __tablename__ = "training_materials"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    training_id: Mapped[int] = mapped_column(
        ForeignKey("trainings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    material_type: Mapped[str] = mapped_column(
        String(20), nullable=False
    )  # video, pdf, document, photo, audio, text
    telegram_file_id: Mapped[str | None] = mapped_column(String(255))
    telegram_file_unique_id: Mapped[str | None] = mapped_column(String(255))
    file_name: Mapped[str | None] = mapped_column(String(255))
    caption: Mapped[str | None] = mapped_column(Text)
    text_content: Mapped[str | None] = mapped_column(Text)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[date | None] = mapped_column(Date, default=date.today)

    training = relationship("TrainingModel", back_populates="materials")
    deliveries = relationship("MaterialDeliveryModel", back_populates="material", lazy="selectin")


class OrderModel(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    training_id: Mapped[int] = mapped_column(
        ForeignKey("trainings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    payment_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    discount_percent: Mapped[Decimal] = mapped_column(
        Numeric(5, 2), nullable=False, default=Decimal("0.00")
    )
    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), nullable=False, default=Decimal("0.00")
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="RUB")
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending, succeeded, canceled
    customer_email: Mapped[str | None] = mapped_column(String(255))
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    checkout_key: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    payment_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    delivery_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending, sent, attention
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user = relationship("UserModel", back_populates="orders")
    training = relationship("TrainingModel", back_populates="orders")
    items = relationship(
        "OrderItemModel", back_populates="order", lazy="selectin", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("payment_id", name="uq_order_payment_id"),
        Index("ix_orders_user_status", "user_id", "status"),
        Index("ix_orders_training_status", "training_id", "status"),
        Index(
            "uq_orders_pending_checkout_key",
            "checkout_key",
            unique=True,
            sqlite_where=(status == "pending") & checkout_key.is_not(None),
            postgresql_where=(status == "pending") & checkout_key.is_not(None),
        ),
    )


class UserTrainingAccessModel(Base):
    __tablename__ = "user_training_access"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    training_id: Mapped[int] = mapped_column(
        ForeignKey("trainings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    granted_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    user = relationship("UserModel", back_populates="accesses")
    training = relationship("TrainingModel", back_populates="accesses")

    __table_args__ = (UniqueConstraint("user_id", "training_id", name="uq_user_training_access"),)


class MaterialDeliveryModel(Base):
    __tablename__ = "material_deliveries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    training_id: Mapped[int] = mapped_column(
        ForeignKey("trainings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    material_id: Mapped[int] = mapped_column(
        ForeignKey("training_materials.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending, sent, failed
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    material = relationship("TrainingMaterialModel", back_populates="deliveries")

    __table_args__ = (UniqueConstraint("user_id", "material_id", name="uq_material_delivery"),)


class OutboxEventModel(Base):
    __tablename__ = "outbox_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    payload: Mapped[str] = mapped_column(Text, nullable=False)
    dedupe_key: Mapped[str | None] = mapped_column(String(255), nullable=True, unique=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending"
    )  # pending, processed, failed
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    __table_args__ = (Index("ix_outbox_status_next", "status", "next_attempt_at"),)


class InviteLinkModel(Base):
    __tablename__ = "invite_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    training_id: Mapped[int] = mapped_column(
        ForeignKey("trainings.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    invite_link: Mapped[str] = mapped_column(String(255), nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    creates_join_request: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    expire_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, default=datetime.utcnow)


class ProductComponentModel(Base):
    """A bundle's references to existing training products; materials stay normalized."""

    __tablename__ = "product_components"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bundle_id: Mapped[int] = mapped_column(
        ForeignKey("trainings.id", ondelete="CASCADE"), nullable=False, index=True
    )
    training_id: Mapped[int] = mapped_column(
        ForeignKey("trainings.id", ondelete="CASCADE"), nullable=False, index=True
    )

    bundle = relationship(
        "TrainingModel", foreign_keys=[bundle_id], back_populates="bundle_components"
    )
    training = relationship("TrainingModel", foreign_keys=[training_id])

    __table_args__ = (UniqueConstraint("bundle_id", "training_id", name="uq_bundle_component"),)


class OrderItemModel(Base):
    """Products bought in an order, with the per-item price captured at checkout."""

    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True
    )
    training_id: Mapped[int] = mapped_column(
        ForeignKey("trainings.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    item_price: Mapped[Decimal] = mapped_column(Numeric(10, 2), nullable=False)
    is_discounted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    order = relationship("OrderModel", back_populates="items")
    training = relationship("TrainingModel")


class ScheduledMessageModel(Base):
    """Persistent, deduplicated Telegram messages scheduled for a future time."""

    __tablename__ = "scheduled_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    order_id: Mapped[int | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), nullable=True, index=True
    )
    training_id: Mapped[int | None] = mapped_column(
        ForeignKey("trainings.id", ondelete="SET NULL"), nullable=True, index=True
    )
    message_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    __table_args__ = (Index("ix_scheduled_status_time", "status", "scheduled_at"),)
