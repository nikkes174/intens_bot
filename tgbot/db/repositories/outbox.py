from datetime import datetime

from sqlalchemy import select

from tgbot.db.models import OutboxEventModel
from tgbot.db.repositories.base import BaseRepository


class OutboxRepository(BaseRepository):
    async def create_event(
        self,
        event_type: str,
        payload: str,
    ) -> OutboxEventModel:
        event = OutboxEventModel(
            event_type=event_type,
            payload=payload,
            status="pending",
        )
        self.session.add(event)
        await self.session.commit()
        await self.session.refresh(event)
        return event

    async def get_pending_events(self, limit: int = 100) -> list[OutboxEventModel]:
        result = await self.session.execute(
            select(OutboxEventModel)
            .where(OutboxEventModel.status == "pending")
            .order_by(OutboxEventModel.created_at)
            .limit(limit)
        )
        return list(result.scalars().all())

    async def mark_processed(self, event_id: int) -> None:
        event = await self.session.get(OutboxEventModel, event_id)
        if event:
            event.status = "processed"
            event.processed_at = datetime.utcnow()
            await self.session.commit()

    async def mark_failed(self, event_id: int, max_attempts: int = 5) -> None:
        event = await self.session.get(OutboxEventModel, event_id)
        if event:
            event.attempts += 1
            if event.attempts >= max_attempts:
                event.status = "failed"
            else:
                event.next_attempt_at = datetime.utcnow()
            await self.session.commit()
