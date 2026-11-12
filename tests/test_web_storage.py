from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.database import session_scope
from app.models import WebArticleRecord, WebDelivery, WebDetection, WebWatch
from app.services.web_monitor.providers import WebArticle
from app.services.web_monitor.storage import (
    WATCH_START_GRACE,
    create_watch,
    find_watch_by_query,
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
async def test_article_text_is_cleaned_before_persisting():
    article = WebArticle(
        provider="google_news",
        title="<b>Лерчек попросила Владимира Путина</b>",
        url="https://news.example/cleaned-description",
        source_name="Sostav.ru",
        published_at=datetime.now(UTC),
        description=(
            '<a href="https://example.test">о помиловании</a>&nbsp;'
            "<font>Sostav.ru</font>"
        ),
    )

    record, created = await upsert_article(article)

    assert created is True
    assert record.title == "Лерчек попросила Владимира Путина"
    assert record.description == "о помиловании Sostav.ru"


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
async def test_new_watch_does_not_notify_for_old_publication():
    old_published_at = datetime.now(UTC) - timedelta(hours=1)
    article = WebArticle(
        provider="google_news",
        title="Иван Иванов выступил",
        url="https://news.example/old-for-new-watch",
        source_name="Example",
        published_at=old_published_at,
    )
    record, _ = await upsert_article(article)

    async with session_scope() as db:
        watch = WebWatch(
            query="иван иванов",
            enabled=True,
            created_at=datetime.now(UTC),
        )
        db.add(watch)
        await db.commit()
        await db.refresh(watch)
        watch_id = watch.id

    result = await ingest_article(article, notify_watch_ids=[watch_id], admin_id=1)

    assert result.detection_ids
    assert result.delivery_ids == ()
    async with session_scope() as db:
        assert await db.get(WebArticleRecord, record.id) is not None
        assert await db.scalar(select(func.count(WebDelivery.id))) == 0


@pytest.mark.asyncio
async def test_new_watch_notifies_for_new_publication():
    watch_started_at = datetime.now(UTC)
    async with session_scope() as db:
        watch = WebWatch(
            query="иван иванов",
            enabled=True,
            created_at=watch_started_at,
        )
        db.add(watch)
        await db.commit()
        await db.refresh(watch)
        watch_id = watch.id

    article = WebArticle(
        provider="google_news",
        title="Иван Иванов выступил",
        url="https://news.example/new-for-watch",
        source_name="Example",
        published_at=watch_started_at + timedelta(seconds=1),
    )
    result = await ingest_article(article, notify_watch_ids=[watch_id], admin_id=1)

    assert len(result.delivery_ids) == 1


@pytest.mark.asyncio
async def test_new_web_detection_creates_one_delivery_per_unique_admin(monkeypatch):
    monkeypatch.setattr(get_settings(), "admin_ids", (101, 202, 101))
    watch_started_at = datetime.now(UTC)
    async with session_scope() as db:
        watch = WebWatch(
            query="иван иванов",
            enabled=True,
            created_at=watch_started_at,
        )
        db.add(watch)
        await db.commit()
        await db.refresh(watch)
        watch_id = watch.id

    article = WebArticle(
        provider="rss",
        title="Иван Иванов выступил для двух администраторов",
        url="https://news.example/multiple-admins",
        source_name="Example",
        published_at=watch_started_at + timedelta(seconds=1),
    )
    first = await ingest_article(article, notify_watch_ids=[watch_id])
    second = await ingest_article(article, notify_watch_ids=[watch_id])

    assert len(first.delivery_ids) == 2
    assert second.delivery_ids == ()
    async with session_scope() as db:
        deliveries = list((await db.execute(select(WebDelivery))).scalars().all())
    assert {delivery.admin_id for delivery in deliveries} == {101, 202}


@pytest.mark.asyncio
async def test_watch_types_are_stored_searched_and_old_records_default_to_person():
    person = await create_watch("Иван Иванов", clarification="Москва")
    organization = await create_watch("Газпром", watch_type="organization")
    keyword = await create_watch("Банк России", watch_type="keyword")

    assert person.watch_type == "person"
    assert organization.watch_type == "organization"
    assert keyword.watch_type == "keyword"
    assert (
        await find_watch_by_query(
            "Иван Иванов",
            clarification="Москва",
            watch_type="person",
        )
    ).id == person.id
    assert (await find_watch_by_query("Газпром", watch_type="organization")).id == organization.id
    assert (await find_watch_by_query("Банк России", watch_type="keyword")).id == keyword.id

    async with session_scope() as db:
        old_record = WebWatch(query="Старый запрос", enabled=True)
        db.add(old_record)
        await db.commit()
        await db.refresh(old_record)
        assert old_record.watch_type == "person"


@pytest.mark.asyncio
async def test_new_watch_allows_publication_from_previous_five_minutes():
    watch_started_at = datetime.now(UTC)
    async with session_scope() as db:
        watch = WebWatch(
            query="иван иванов",
            enabled=True,
            created_at=watch_started_at,
        )
        db.add(watch)
        await db.commit()
        await db.refresh(watch)
        watch_id = watch.id

    article = WebArticle(
        provider="google_news",
        title="Иван Иванов выступил незадолго до добавления",
        url="https://news.example/grace-period",
        source_name="Example",
        published_at=watch_started_at - timedelta(minutes=3),
    )
    result = await ingest_article(article, notify_watch_ids=[watch_id], admin_id=1)

    assert len(result.delivery_ids) == 1


@pytest.mark.asyncio
async def test_published_at_allows_delayed_detection_beyond_scheduler_window():
    watch_started_at = datetime.now(UTC) - timedelta(minutes=10)
    article = WebArticle(
        provider="google_news",
        title="Иван Иванов обнаружен с задержкой",
        url="https://news.example/delayed-detection",
        source_name="Example",
        published_at=watch_started_at + timedelta(minutes=1),
    )
    record, _ = await upsert_article(article)
    async with session_scope() as db:
        db_record = await db.get(WebArticleRecord, record.id)
        db_record.first_seen_at = datetime.now(UTC) - timedelta(minutes=10)
        watch = WebWatch(
            query="иван иванов",
            enabled=True,
            created_at=watch_started_at,
        )
        db.add(watch)
        await db.commit()
        await db.refresh(watch)
        watch_id = watch.id

    result = await ingest_article(article, notify_watch_ids=[watch_id], admin_id=1)

    assert len(result.delivery_ids) == 1
    assert datetime.now(UTC) - watch_started_at > timedelta(seconds=60)
    assert WATCH_START_GRACE == timedelta(minutes=5)


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
