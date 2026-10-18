from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from tgbot.db.models import ScheduledMessageModel
from tgbot.db.repositories.base import BaseRepository


class ScheduledMessageRepository(BaseRepository):
    async def schedule(
        self,
        *,
        dedupe_key: str,
        user_id: int,
        message_type: str,
        scheduled_at: datetime,
        order_id: int | None = None,
        training_id: int | None = None,
    ) -> ScheduledMessageModel:
        existing = await self.get_by_dedupe_key(dedupe_key)
        if existing:
            return existing
        job = ScheduledMessageModel(
            dedupe_key=dedupe_key,
            user_id=user_id,
            order_id=order_id,
            training_id=training_id,
            message_type=message_type,
            scheduled_at=scheduled_at,
            status="pending",
        )
        self.session.add(job)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            existing = await self.get_by_dedupe_key(dedupe_key)
            if existing:
                return existing
            raise
        await self.session.refresh(job)
        return job

    async def get_by_dedupe_key(self, dedupe_key: str) -> ScheduledMessageModel | None:
        result = await self.session.execute(
            select(ScheduledMessageModel).where(ScheduledMessageModel.dedupe_key == dedupe_key)
        )
        return result.scalar_one_or_none()

    async def get_due(self, now: datetime | None = None, limit: int = 50):
        result = await self.session.execute(
            select(ScheduledMessageModel)
            .where(
                ScheduledMessageModel.status == "pending",
                ScheduledMessageModel.scheduled_at <= (now or datetime.utcnow()),
            )
            .order_by(ScheduledMessageModel.scheduled_at, ScheduledMessageModel.id)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def claim(self, job_id: int) -> bool:
        result = await self.session.execute(
            update(ScheduledMessageModel)
            .where(ScheduledMessageModel.id == job_id, ScheduledMessageModel.status == "pending")
            .values(status="processing", claimed_at=datetime.utcnow())
        )
        await self.session.commit()
        return bool(result.rowcount)

    async def mark_sent(self, job_id: int, now: datetime | None = None) -> None:
        job = await self.session.get(ScheduledMessageModel, job_id)
        if job:
            job.status = "sent"
            job.sent_at = now or datetime.utcnow()
            job.last_error = None
            job.claimed_at = None
            await self.session.commit()

    async def mark_skipped(self, job_id: int, reason: str) -> None:
        job = await self.session.get(ScheduledMessageModel, job_id)
        if job:
            job.status = "skipped"
            job.last_error = reason
            job.claimed_at = None
            await self.session.commit()

    async def mark_failed(
        self,
        job_id: int,
        error: str,
        *,
        retry: bool,
        delay: timedelta = timedelta(minutes=5),
    ) -> None:
        job = await self.session.get(ScheduledMessageModel, job_id)
        if job:
            job.attempts += 1
            job.last_error = error[:4000]
            job.claimed_at = None
            if retry and job.attempts < 5:
                job.status = "pending"
                job.scheduled_at = datetime.utcnow() + delay
            else:
                job.status = "failed"
            await self.session.commit()

    async def cancel_product_reminders(self, user_id: int, training_ids: list[int]) -> int:
        if not training_ids:
            return 0
        result = await self.session.execute(
            update(ScheduledMessageModel)
            .where(
                ScheduledMessageModel.user_id == user_id,
                ScheduledMessageModel.training_id.in_(training_ids),
                ScheduledMessageModel.message_type == "card_view_24h",
                ScheduledMessageModel.status.in_(["pending", "processing"]),
            )
            .values(status="canceled", last_error="Product purchased")
        )
        await self.session.commit()
        return result.rowcount or 0

    async def has_recent_view_reminder(self, user_id: int, now: datetime | None = None) -> bool:
        cutoff = (now or datetime.utcnow()) - timedelta(hours=24)
        result = await self.session.execute(
            select(ScheduledMessageModel.id)
            .where(
                ScheduledMessageModel.user_id == user_id,
                ScheduledMessageModel.message_type == "card_view_24h",
                ScheduledMessageModel.status == "sent",
                ScheduledMessageModel.sent_at >= cutoff,
            )
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def recover_processing(self, stale_before: datetime | None = None) -> int:
        cutoff = stale_before or (datetime.utcnow() - timedelta(minutes=5))
        result = await self.session.execute(
            update(ScheduledMessageModel)
            .where(
                ScheduledMessageModel.status == "processing",
                (ScheduledMessageModel.claimed_at.is_(None))
                | (ScheduledMessageModel.claimed_at <= cutoff),
            )
            .values(
                status="failed",
                attempts=ScheduledMessageModel.attempts + 1,
                last_error=(
                    "Delivery outcome is uncertain after worker restart; "
                    "not retried automatically to avoid a duplicate message."
                ),
                claimed_at=None,
            )
        )
        await self.session.commit()
        return result.rowcount or 0
