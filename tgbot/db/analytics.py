"""Persistent traffic, funnel and payment analytics.

The service deliberately uses the same AsyncSession as the rest of the bot.
Every write commits its own small transaction so an analytics failure can be
handled at the integration boundary without replaying a YooKassa request.
"""

import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import and_, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tgbot.db.models import (
    FunnelEventModel,
    PaymentAnalyticsModel,
    TrafficSourceModel,
    UserVisitModel,
    utcnow,
)


ORGANIC_SOURCE = "organic"
SOURCE_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def normalize_source(source: Optional[str]) -> str:
    """Return a safe Telegram start payload, or the organic bucket."""

    if source is None:
        return ORGANIC_SOURCE
    value = str(source).strip()
    if not value:
        return ORGANIC_SOURCE
    value = value.lower()
    if not SOURCE_RE.fullmatch(value):
        return ORGANIC_SOURCE
    return value


def parse_source_filter(value: Optional[str]) -> Optional[Tuple[str, bool]]:
    """Parse an exact or prefix source filter without accepting SQL syntax."""

    if value is None:
        return None
    raw = str(value).strip().lower()
    if not raw:
        return None
    is_prefix = raw.endswith("*")
    label = raw[:-1] if is_prefix else raw
    if not label or not SOURCE_RE.fullmatch(label):
        return None
    return label, is_prefix


class AnalyticsService:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def _get_or_create_source(self, name: str) -> TrafficSourceModel:
        result = await self.session.execute(
            select(TrafficSourceModel).where(TrafficSourceModel.name == name)
        )
        source = result.scalar_one_or_none()
        if source is not None:
            return source

        # The savepoint handles two /start requests creating the same new
        # label at the same time.  The UNIQUE constraint remains the source of
        # truth on both PostgreSQL and SQLite.
        try:
            async with self.session.begin_nested():
                source = TrafficSourceModel(name=name)
                self.session.add(source)
                await self.session.flush()
        except IntegrityError:
            pass

        result = await self.session.execute(
            select(TrafficSourceModel).where(TrafficSourceModel.name == name)
        )
        source = result.scalar_one()
        return source

    async def register_visit(self, user_id: int, source: Optional[str]) -> int:
        source_model = await self._get_or_create_source(normalize_source(source))
        visit = UserVisitModel(user_id=user_id, source_id=source_model.id)
        self.session.add(visit)
        await self.session.flush()
        visit_id = visit.id
        await self.session.commit()
        return visit_id

    async def get_latest_visit_id(self, user_id: int) -> Optional[int]:
        result = await self.session.execute(
            select(UserVisitModel.id)
            .where(UserVisitModel.user_id == user_id)
            .order_by(UserVisitModel.visited_at.desc(), UserVisitModel.id.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _resolve_visit(
        self, user_id: int, visit_id: Optional[int]
    ) -> UserVisitModel:
        visit = None
        if visit_id is not None:
            result = await self.session.execute(
                select(UserVisitModel).where(
                    UserVisitModel.id == visit_id,
                    UserVisitModel.user_id == user_id,
                )
            )
            visit = result.scalar_one_or_none()

        if visit is None:
            result = await self.session.execute(
                select(UserVisitModel)
                .where(UserVisitModel.user_id == user_id)
                .order_by(UserVisitModel.visited_at.desc(), UserVisitModel.id.desc())
                .limit(1)
            )
            visit = result.scalar_one_or_none()

        if visit is None:
            visit_id = await self.register_visit(user_id, ORGANIC_SOURCE)
            result = await self.session.execute(
                select(UserVisitModel).where(UserVisitModel.id == visit_id)
            )
            visit = result.scalar_one()
        return visit

    async def create_payment_record(
        self,
        payment_id: str,
        user_id: int,
        amount: Decimal,
        currency: str = "RUB",
        visit_id: Optional[int] = None,
        status: str = "pending",
        paid_at: Optional[datetime] = None,
    ) -> PaymentAnalyticsModel:
        result = await self.session.execute(
            select(PaymentAnalyticsModel).where(
                PaymentAnalyticsModel.payment_id == payment_id
            )
        )
        existing = result.scalar_one_or_none()
        if existing is not None:
            return existing

        visit = await self._resolve_visit(user_id, visit_id)
        record = PaymentAnalyticsModel(
            payment_id=payment_id,
            user_id=user_id,
            visit_id=visit.id,
            source_id=visit.source_id,
            amount=Decimal(str(amount)),
            currency=(currency or "RUB")[:3].upper(),
            status=status,
            paid_at=paid_at,
        )
        self.session.add(record)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            result = await self.session.execute(
                select(PaymentAnalyticsModel).where(
                    PaymentAnalyticsModel.payment_id == payment_id
                )
            )
            existing = result.scalar_one_or_none()
            if existing is None:
                raise
            return existing
        return record

    async def get_payment(self, payment_id: str) -> Optional[PaymentAnalyticsModel]:
        result = await self.session.execute(
            select(PaymentAnalyticsModel).where(
                PaymentAnalyticsModel.payment_id == payment_id
            )
        )
        return result.scalar_one_or_none()

    async def get_payment_visit_id(self, payment_id: str) -> Optional[int]:
        result = await self.session.execute(
            select(PaymentAnalyticsModel.visit_id).where(
                PaymentAnalyticsModel.payment_id == payment_id
            )
        )
        return result.scalar_one_or_none()

    async def get_latest_successful_payment(
        self, user_id: int
    ) -> Optional[PaymentAnalyticsModel]:
        result = await self.session.execute(
            select(PaymentAnalyticsModel)
            .where(
                PaymentAnalyticsModel.user_id == user_id,
                PaymentAnalyticsModel.status == "succeeded",
            )
            .order_by(
                PaymentAnalyticsModel.paid_at.desc(),
                PaymentAnalyticsModel.id.desc(),
            )
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def has_source_filter(self, source_filter: str) -> bool:
        condition = self._source_condition(TrafficSourceModel.name, source_filter)
        if condition is None:
            return False
        result = await self.session.execute(
            select(TrafficSourceModel.id).where(condition).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def log_event(
        self,
        user_id: int,
        event: str,
        visit_id: Optional[int] = None,
        payment_id: Optional[str] = None,
    ) -> Optional[int]:
        item = FunnelEventModel(
            user_id=user_id,
            visit_id=visit_id,
            event=event[:64],
            payment_id=payment_id,
        )
        self.session.add(item)
        try:
            await self.session.commit()
        except IntegrityError:
            # Payment lifecycle events have a unique (event, payment_id)
            # key.  A retry is already recorded and is therefore successful.
            await self.session.rollback()
            if payment_id is None:
                raise
            result = await self.session.execute(
                select(FunnelEventModel.id).where(
                    FunnelEventModel.event == event[:64],
                    FunnelEventModel.payment_id == payment_id,
                )
            )
            return result.scalar_one_or_none()
        return item.id

    async def has_event(
        self, user_id: int, event: str, payment_id: Optional[str] = None
    ) -> bool:
        conditions = [
            FunnelEventModel.user_id == user_id,
            FunnelEventModel.event == event,
        ]
        if payment_id is None:
            conditions.append(FunnelEventModel.payment_id.is_(None))
        else:
            conditions.append(FunnelEventModel.payment_id == payment_id)
        result = await self.session.execute(
            select(FunnelEventModel.id).where(and_(*conditions)).limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def mark_payment_success(self, payment_id: str) -> bool:
        """Atomically transition a pending payment and emit one success event."""

        result = await self.session.execute(
            update(PaymentAnalyticsModel)
            .where(
                PaymentAnalyticsModel.payment_id == payment_id,
                PaymentAnalyticsModel.status.not_in(("succeeded", "canceled")),
            )
            .values(status="succeeded", paid_at=utcnow())
        )
        if result.rowcount != 1:
            await self.session.rollback()
            return False

        payment = await self.get_payment(payment_id)
        if payment is None:
            await self.session.rollback()
            return False
        self.session.add(
            FunnelEventModel(
                user_id=payment.user_id,
                visit_id=payment.visit_id,
                event="payment_succeeded",
                payment_id=payment_id,
            )
        )
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            return False
        return True

    async def mark_payment_canceled(self, payment_id: str) -> bool:
        result = await self.session.execute(
            update(PaymentAnalyticsModel)
            .where(
                PaymentAnalyticsModel.payment_id == payment_id,
                PaymentAnalyticsModel.status == "pending",
            )
            .values(status="canceled")
        )
        changed = result.rowcount == 1
        await self.session.commit()
        return changed

    @staticmethod
    def _source_condition(column: Any, source_filter: Optional[str]):
        parsed = parse_source_filter(source_filter)
        if parsed is None:
            return None
        label, is_prefix = parsed
        if is_prefix:
            return column.like(label + "%", escape="\\")
        return column == label

    def _visit_conditions(
        self,
        source_filter: Optional[str],
        start: Optional[datetime],
        end: Optional[datetime],
    ) -> List[Any]:
        conditions = []
        source_condition = self._source_condition(
            TrafficSourceModel.name, source_filter
        )
        if source_condition is not None:
            conditions.append(source_condition)
        if start is not None:
            conditions.append(UserVisitModel.visited_at >= start)
        if end is not None:
            conditions.append(UserVisitModel.visited_at < end)
        return conditions

    def _payment_conditions(
        self,
        source_filter: Optional[str],
        start: Optional[datetime],
        end: Optional[datetime],
    ) -> List[Any]:
        conditions: List[Any] = [PaymentAnalyticsModel.status == "succeeded"]
        if start is not None:
            conditions.append(PaymentAnalyticsModel.paid_at >= start)
        if end is not None:
            conditions.append(PaymentAnalyticsModel.paid_at < end)
        return conditions

    async def _metric_value(self, statement: Any, default: Any = 0) -> Any:
        result = await self.session.execute(statement)
        value = result.scalar_one_or_none()
        return default if value is None else value

    async def get_funnel_statistics(
        self,
        source_filter: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        visit_conditions = self._visit_conditions(source_filter, start, end)
        visit_scope = (
            select(UserVisitModel.id, UserVisitModel.user_id)
            .join(
                TrafficSourceModel,
                TrafficSourceModel.id == UserVisitModel.source_id,
            )
            .where(*visit_conditions)
            .subquery()
        )
        cohort_users = select(visit_scope.c.user_id).distinct().subquery()

        users = await self._metric_value(
            select(func.count(func.distinct(visit_scope.c.user_id)))
        )
        visits = await self._metric_value(select(func.count(visit_scope.c.id)))

        event_names = (
            "bot_started",
            "consent_accepted",
            "description_received",
            "email_entered",
            "payment_created",
            "payment_succeeded",
            "access_granted",
        )
        events: Dict[str, int] = {}
        for event_name in event_names:
            events[event_name] = int(
                await self._metric_value(
                    select(func.count(func.distinct(FunnelEventModel.user_id))).where(
                        FunnelEventModel.event == event_name,
                        FunnelEventModel.visit_id.in_(select(visit_scope.c.id)),
                    )
                )
            )

        payment_conditions = self._payment_conditions(source_filter, start, end)

        def payment_statement(expression: Any) -> Any:
            statement = select(expression).select_from(PaymentAnalyticsModel)
            if source_filter is not None:
                statement = statement.join(
                    TrafficSourceModel,
                    TrafficSourceModel.id == PaymentAnalyticsModel.source_id,
                ).where(
                    self._source_condition(TrafficSourceModel.name, source_filter)
                )
            return statement.where(*payment_conditions)

        # Purchases and revenue are filtered by payment paid_at and the
        # immutable source attribution.  Only the buyer count used for
        # conversion is intersected with the explicitly selected cohort.
        payments = await self._metric_value(
            payment_statement(func.count(func.distinct(PaymentAnalyticsModel.payment_id)))
        )
        revenue = await self._metric_value(
            payment_statement(func.coalesce(func.sum(PaymentAnalyticsModel.amount), 0))
        )
        buyers = await self._metric_value(
            payment_statement(func.count(func.distinct(PaymentAnalyticsModel.user_id))).where(
                PaymentAnalyticsModel.user_id.in_(select(cohort_users.c.user_id))
            )
        )

        conversion = (float(buyers) * 100 / int(users)) if users else 0.0
        return {
            "users": int(users),
            "visits": int(visits),
            "payments": int(payments),
            "buyers": int(buyers),
            "revenue": Decimal(str(revenue)),
            "conversion": conversion,
            "events": events,
        }

    async def get_source_statistics(
        self,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        result = await self.session.execute(
            select(TrafficSourceModel.name).order_by(TrafficSourceModel.name)
        )
        names = result.scalars().all()
        rows: List[Dict[str, Any]] = []
        for name in names:
            metrics = await self.get_funnel_statistics(name, start, end)
            metrics["name"] = name
            rows.append(metrics)
        return rows
