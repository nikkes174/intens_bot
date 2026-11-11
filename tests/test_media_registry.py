from sqlalchemy import func, select

from app.database import session_scope
from app.models import WebMediaCandidate, WebMediaSource
from app.services.web_monitor.media_registry import (
    bulk_import_sources,
    get_enabled_feed_definitions,
    normalize_domain,
)
from app.services.web_monitor.providers import WebArticle
from app.services.web_monitor.source_autodiscovery import (
    observe_article_source,
    process_pending_candidates,
)
from app.services.web_monitor.source_discovery import SitemapEntry, SourceDiscoveryResult


async def test_bulk_import_handles_thousands_and_deduplicates_domains():
    rows = [
        {
            "name": f"Источник {index}",
            "domain": f"news-{index}.example",
            "region": "Тестовый регион",
            "category": "regional",
        }
        for index in range(1001)
    ]
    rows.append(rows[500] | {"domain": "https://www.news-500.example/"})

    result = await bulk_import_sources(rows, batch_size=100)

    assert result == {"imported": 1001, "duplicates": 1, "rejected": 0}
    async with session_scope() as db:
        count = await db.scalar(
            select(func.count(WebMediaSource.id)).where(
                WebMediaSource.domain.like("news-%.example")
            )
        )
    assert count == 1001


async def test_registry_normalizes_domains_and_keeps_legacy_feeds_available():
    assert normalize_domain("HTTPS://WWW.Example.RU/path") == "example.ru"
    feeds = await get_enabled_feed_definitions()
    assert any(url == "https://tass.ru/rss/v2.xml" for _, url in feeds)


async def test_provider_domain_is_saved_as_candidate_without_network_check():
    candidate_id = await observe_article_source(
        WebArticle(
            provider="gdelt",
            title="Новая публикация",
            url="https://candidate.example/news/1",
            source_name="Кандидат",
            published_at=None,
        )
    )
    assert candidate_id is not None
    async with session_scope() as db:
        candidate = await db.get(WebMediaCandidate, candidate_id)
    assert candidate is not None
    assert candidate.status == "pending"


async def test_autodiscovery_does_not_activate_non_russian_candidate(monkeypatch):
    candidate_id = await observe_article_source(
        WebArticle(
            provider="google_news",
            title="English publication",
            url="https://unknown.example/news/1",
            source_name="Unknown",
            published_at=None,
        )
    )

    async def fake_inspect(*args, **kwargs):
        return SourceDiscoveryResult(
            domain="unknown.example",
            feeds=("https://unknown.example/feed.xml",),
            sample_entries=(SitemapEntry(url="https://unknown.example/1", title="English"),),
        )

    monkeypatch.setattr(
        "app.services.web_monitor.source_autodiscovery.inspect_source",
        fake_inspect,
    )
    await process_pending_candidates(object())

    async with session_scope() as db:
        candidate = await db.get(WebMediaCandidate, candidate_id)
        source = await db.scalar(
            select(WebMediaSource).where(WebMediaSource.domain == "unknown.example")
        )
    assert candidate.status == "pending_review"
    assert source is None
