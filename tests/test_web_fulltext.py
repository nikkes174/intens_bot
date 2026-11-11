from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select

from app.database import session_scope
from app.models import WebArticleRecord, WebDelivery, WebDetection, WebWatch
from app.services.web_monitor.providers import WebArticle
from app.services.web_monitor.scheduler import WebMonitor
from app.services.web_monitor.storage import normalize_url, url_hash


@pytest.mark.asyncio
async def test_name_found_only_in_full_text_creates_one_notification():
    async with session_scope() as db:
        watch = WebWatch(query="иван иванов", enabled=True)
        db.add(watch)
        await db.commit()
        await db.refresh(watch)
        watch_id = watch.id
    article = WebArticle(
        provider="rss",
        title="Новый закон принят",
        url="https://media.example/full-text-only",
        source_name="РБК",
        published_at=datetime.now(UTC),
        description="Краткое описание без имени",
        full_text="В полном тексте Иван Иванов сообщил о решении.",
    )
    from app.services.web_monitor.storage import ingest_article

    result = await ingest_article(article, notify_watch_ids=[watch_id], admin_id=1)
    assert len(result.detection_ids) == 1
    assert len(result.delivery_ids) == 1


@pytest.mark.asyncio
async def test_name_absent_from_full_text_does_not_create_detection():
    async with session_scope() as db:
        watch = WebWatch(query="иван иванов", enabled=True)
        db.add(watch)
        await db.commit()
        await db.refresh(watch)
        watch_id = watch.id
    article = WebArticle(
        provider="rss",
        title="Новый закон принят",
        url="https://media.example/no-full-text-match",
        source_name="ТАСС",
        published_at=datetime.now(UTC),
        full_text="В статье говорится о погоде и транспорте.",
    )
    from app.services.web_monitor.storage import ingest_article

    result = await ingest_article(article, notify_watch_ids=[watch_id], admin_id=1)
    assert result.detection_ids == ()
    async with session_scope() as db:
        assert await db.scalar(select(func.count(WebDetection.id))) == 0


@pytest.mark.asyncio
async def test_title_and_full_text_match_create_one_detection():
    async with session_scope() as db:
        watch = WebWatch(query="иван иванов", enabled=True)
        db.add(watch)
        await db.commit()
        await db.refresh(watch)
        watch_id = watch.id
    article = WebArticle(
        provider="google_news",
        title="Иван Иванов выступил",
        url="https://media.example/title-and-text",
        source_name="Google News",
        published_at=datetime.now(UTC),
        full_text="Иван Иванов также ответил на вопросы журналистов.",
    )
    from app.services.web_monitor.storage import ingest_article

    first = await ingest_article(article, notify_watch_ids=[watch_id], admin_id=1)
    second = await ingest_article(article, notify_watch_ids=[watch_id], admin_id=1)
    assert len(first.detection_ids) == 1
    assert second.delivery_ids == ()
    async with session_scope() as db:
        assert await db.scalar(select(func.count(WebDetection.id))) == 1
        assert await db.scalar(select(func.count(WebDelivery.id))) == 1


@pytest.mark.asyncio
async def test_existing_article_is_extracted_once_and_cached(monkeypatch):
    article = WebArticle(
        provider="rss",
        title="Заголовок",
        url="https://media.example/cached",
        source_name="РБК",
        published_at=datetime.now(UTC),
    )
    extractor = AsyncMock(return_value="Иван Иванов в полном тексте")
    monkeypatch.setattr("app.services.web_monitor.scheduler.extract_article", extractor)
    monitor = WebMonitor(providers=[], client=None)
    try:
        first = await monitor._prepare_article(article)
        second = await monitor._prepare_article(article)
    finally:
        await monitor.close()
    assert first.full_text == second.full_text == "Иван Иванов в полном тексте"
    extractor.assert_awaited_once()
    async with session_scope() as db:
        record = await db.scalar(
            select(WebArticleRecord).where(
                WebArticleRecord.url_hash == url_hash(normalize_url(article.url))
            )
        )
        assert record.full_text == "Иван Иванов в полном тексте"
        assert record.extraction_status == "success"
