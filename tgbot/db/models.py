from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

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
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from tgbot.db.db import Base


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    user_name: Mapped[Optional[str]] = mapped_column(String(255))

    payment_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    payment_ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    create_date: Mapped[Optional[date]] = mapped_column(Date)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TrafficSourceModel(Base):
    __tablename__ = "traffic_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )


class UserVisitModel(Base):
    __tablename__ = "user_visits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("traffic_sources.id"), nullable=False
    )
    visited_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        Index("ix_user_visits_user_date", "user_id", "visited_at"),
    )


class FunnelEventModel(Base):
    __tablename__ = "funnel_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    visit_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_visits.id"), nullable=True, index=True
    )
    event: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payment_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    # A payment lifecycle event is a fact, not a polling observation.  This
    # also makes retries safe while leaving non-payment events repeatable.
    __table_args__ = (
        UniqueConstraint(
            "event", "payment_id", name="uq_funnel_events_event_payment"
        ),
    )


class PaymentAnalyticsModel(Base):
    __tablename__ = "payment_analytics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    payment_id: Mapped[str] = mapped_column(
        String(64), unique=True, nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    visit_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user_visits.id"), nullable=True
    )
    source_id: Mapped[int] = mapped_column(
        ForeignKey("traffic_sources.id"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), default="RUB", nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), default="pending", nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    paid_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
