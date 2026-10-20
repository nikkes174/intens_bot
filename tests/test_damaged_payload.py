import pytest
from datetime import datetime, timedelta, timezone

from app.database import session_scope
from app.models import Delivery, Detection
from app.services.monitor import _process_delivery


@pytest.mark.asyncio
async def test_damaged_payload_marked_failed(monkeypatch):
    async def no_bot(*args, **kwargs):
        return None

    async with session_scope() as db:
        detection = Detection(source_id=1, message_id=2, matched_keywords="[]")
        db.add(detection)
        await db.commit()
        await db.refresh(detection)
        delivery = Delivery(
            detection_id=detection.id,
            status="processing",
            payload="{bad json",
            processing_started_at=datetime.now(timezone.utc) - timedelta(minutes=6),
        )
        db.add(delivery)
        await db.commit()
        await db.refresh(delivery)
        delivery_id = delivery.id

    await _process_delivery(delivery_id)

    async with session_scope() as db:
        delivery = await db.get(Delivery, delivery_id)
        assert delivery.status == "failed"
        assert "Invalid notification payload" in (delivery.last_error or "")
        assert delivery.processing_started_at is None


@pytest.mark.asyncio
async def test_missing_payload_marked_failed():
    async with session_scope() as db:
        detection = Detection(source_id=1, message_id=3, matched_keywords="[]")
        db.add(detection)
        await db.commit()
        await db.refresh(detection)
        delivery = Delivery(
            detection_id=detection.id,
            status="processing",
            payload=None,
            processing_started_at=datetime.now(timezone.utc) - timedelta(minutes=6),
        )
        db.add(delivery)
        await db.commit()
        await db.refresh(delivery)
        delivery_id = delivery.id

    await _process_delivery(delivery_id)

    async with session_scope() as db:
        delivery = await db.get(Delivery, delivery_id)
        assert delivery.status == "failed"
        assert delivery.last_error == "Missing notification payload"
        assert delivery.processing_started_at is None
