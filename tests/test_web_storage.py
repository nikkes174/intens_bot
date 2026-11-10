from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.database import session_scope
from app.models import WebArticleRecord, WebDelivery, WebDetection, WebWatch
from app.services.web_monitor.providers import WebArticle
from app.services.web_monitor.storage import (
    ingest_article,
    normalize_url,
    upsert_article,
    url_hash,
)


def test_tracking_parameters_are_removed_but_identity_is_preserved():
    normalized = normalize_url(
        "HTTPS://News.Example/story/?utm_source=x&fbclid=y&id=42#comments"
    )
    assert normalized == "https://news.example/story?id=42"
    assert url_hash(normalized) == url_hash("https://news.example/story?id=42&utm_medium=email")


@pytest.mark.asyncio
async def test_article_and_detection_are_deduplicated():
    async with session_scope() as db:
        watch = WebWatch(query="иван иванов", enabled=True)
        db.add(watch)
        await db.commit()
        await db.refresh(watch)
        watch_id = watch.id
    article = WebArticle(
        provider="google_news",
        title="Иван Иванов выступил",
        url="https://news.example/a?utm_source=google",
        source_name="Example",
        published_at=datetime.now(UTC),
    )
    first = await ingest_article(article, notify_watch_ids=[watch_id], admin_id=1)
    second = await ingest_article(
        replace(article, provider="gdelt"),
        notify_watch_ids=[watch_id],
        admin_id=1,
    )
    assert first.created_article is True
    assert second.created_article is False
    async with session_scope() as db:
        assert await db.scalar(select(func.count(WebArticleRecord.id))) == 1
        assert await db.scalar(select(func.count(WebDetection.id))) == 1
        assert await db.scalar(select(func.count(WebDelivery.id))) == 1


@pytest.mark.asyncio
async def test_existing_detection_does_not_create_delivery_after_restart():
    async with session_scope() as db:
        watch = WebWatch(query="иван иванов", enabled=True)
        db.add(watch)
        await db.commit()
        await db.refresh(watch)
        watch_id = watch.id
    article = WebArticle(
        provider="google_news",
        title="Иван Иванов выступил",
        url="https://news.example/replay",
        source_name="Example",
        published_at=datetime.now(UTC),
    )
    initial = await ingest_article(article, notify_watch_ids=(), admin_id=1)
    replay = await ingest_article(article, notify_watch_ids=[watch_id], admin_id=1)
    assert initial.detection_ids == replay.detection_ids
    assert replay.delivery_ids == ()
    async with session_scope() as db:
        assert await db.scalar(select(func.count(WebDelivery.id))) == 0


@pytest.mark.asyncio
async def test_same_story_from_google_and_gdelt_is_deduplicated_by_metadata():
    published = datetime(2026, 11, 10, 12, tzinfo=UTC)
    google = WebArticle(
        provider="google_news",
        title="Иван Иванов выступил на форуме",
        url="https://news.example/google-link",
        source_name="https://www.example.com",
        published_at=published,
    )
    gdelt = replace(
        google,
        provider="gdelt",
        url="https://example.com/gdelt-link",
    )
    different = replace(gdelt, title="Иван Иванов выступил на форуме сегодня")
    first, first_created = await upsert_article(google)
    second, second_created = await upsert_article(gdelt)
    third, third_created = await upsert_article(different)
    assert first_created is True
    assert second_created is False
    assert second.id == first.id
    assert third_created is True
    assert third.id != first.id
