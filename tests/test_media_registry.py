from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from app.database import session_scope
from app.models import WebMediaCandidate, WebMediaEndpoint, WebMediaSource
from app.services.web_monitor.media_registry import (
    bulk_import_sources,
    claim_due_endpoints,
    claim_due_feed_definitions,
    get_enabled_feed_definitions,
    normalize_domain,
    release_endpoint_poll,
    save_endpoint_statuses,
    set_source_enabled,
    upsert_endpoint,
    upsert_source,
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
    repeated_id = await observe_article_source(
        WebArticle(
            provider="google_news",
            title="Та же публикация",
            url="https://www.candidate.example/news/2",
            source_name="Кандидат",
            published_at=None,
        )
    )
    assert repeated_id == candidate_id


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


async def test_imported_source_is_queued_and_automatically_verified(monkeypatch):
    result = await bulk_import_sources(
        [
            {
                "name": "Новости России",
                "domain": "imported.example",
                "category": "federal",
            }
        ]
    )
    assert result["imported"] == 1

    async def fake_inspect(*args, **kwargs):
        return SourceDiscoveryResult(
            domain="imported.example",
            feeds=("https://imported.example/feed.xml",),
            sample_entries=(
                SitemapEntry(
                    url="https://imported.example/news/1",
                    title="Новости региона: первое событие",
                    published_at=datetime(2026, 11, 11, 10, tzinfo=UTC),
                ),
                SitemapEntry(
                    url="https://imported.example/news/2",
                    title="Новости региона: второе событие",
                    published_at=datetime(2026, 11, 11, 9, tzinfo=UTC),
                ),
            ),
        )

    monkeypatch.setattr(
        "app.services.web_monitor.source_autodiscovery.inspect_source",
        fake_inspect,
    )
    await process_pending_candidates(object(), limit=1)

    async with session_scope() as db:
        source = await db.scalar(
            select(WebMediaSource).where(WebMediaSource.domain == "imported.example")
        )
        candidate = await db.scalar(
            select(WebMediaCandidate).where(
                WebMediaCandidate.domain == "imported.example"
            )
        )
        endpoint = await db.scalar(
            select(WebMediaEndpoint).where(
                WebMediaEndpoint.url == "https://imported.example/feed.xml"
            )
        )
    assert source.enabled is True
    assert source.status == "ok"
    assert candidate.status == "approved"
    assert endpoint.enabled is True


async def test_imported_source_check_recovers_after_restart(monkeypatch):
    await bulk_import_sources(
        [{"name": "Восстановление", "domain": "restart.example"}]
    )
    async with session_scope() as db:
        candidate = await db.scalar(
            select(WebMediaCandidate).where(
                WebMediaCandidate.domain == "restart.example"
            )
        )
        candidate.status = "checking"
        candidate.last_checked_at = datetime.now(UTC) - timedelta(minutes=20)
        await db.commit()

    async def fake_inspect(*args, **kwargs):
        return SourceDiscoveryResult(domain="restart.example")

    monkeypatch.setattr(
        "app.services.web_monitor.source_autodiscovery.inspect_source",
        fake_inspect,
    )
    await process_pending_candidates(object(), limit=1)

    async with session_scope() as db:
        candidate = await db.scalar(
            select(WebMediaCandidate).where(
                WebMediaCandidate.domain == "restart.example"
            )
        )
    assert candidate.status == "temporary_error"
    assert candidate.attempts == 1


async def test_manual_disable_is_preserved_when_import_check_succeeds(monkeypatch):
    await bulk_import_sources(
        [{"name": "Отключённый сайт", "domain": "disabled.example"}]
    )
    async with session_scope() as db:
        source = await db.scalar(
            select(WebMediaSource).where(WebMediaSource.domain == "disabled.example")
        )
    await set_source_enabled(source.id, False)

    async def fake_inspect(*args, **kwargs):
        return SourceDiscoveryResult(
            domain="disabled.example",
            feeds=("https://disabled.example/feed.xml",),
            sample_entries=(
                SitemapEntry(
                    url="https://disabled.example/news/1",
                    title="Новости: одно событие",
                    published_at=datetime(2026, 11, 11, 10, tzinfo=UTC),
                ),
                SitemapEntry(
                    url="https://disabled.example/news/2",
                    title="Новости: другое событие",
                    published_at=datetime(2026, 11, 11, 9, tzinfo=UTC),
                ),
            ),
        )

    monkeypatch.setattr(
        "app.services.web_monitor.source_autodiscovery.inspect_source",
        fake_inspect,
    )
    await process_pending_candidates(object(), limit=1)

    async with session_scope() as db:
        source = await db.scalar(
            select(WebMediaSource).where(WebMediaSource.domain == "disabled.example")
        )
    assert source.enabled is False
    assert source.managed_by_user is True
    assert '"rss"' in source.discovery_methods


async def test_sources_are_claimed_independently_and_backoff_is_persisted():
    first = await upsert_source(
        name="Первый",
        domain="first.example",
        enabled=True,
        poll_interval_seconds=30,
    )
    second = await upsert_source(
        name="Второй",
        domain="second.example",
        enabled=True,
        poll_interval_seconds=60,
    )
    await upsert_endpoint(first.id, "https://first.example/feed.xml", kind="rss")
    await upsert_endpoint(second.id, "https://second.example/feed.xml", kind="rss")
    async with session_scope() as db:
        await db.execute(
            WebMediaSource.__table__.update()
            .where(
                WebMediaSource.domain.not_in({"first.example", "second.example"})
            )
            .values(next_poll_at=datetime.now(UTC) + timedelta(hours=1))
        )
        await db.commit()

    claimed = await claim_due_feed_definitions(limit=1)
    assert len(claimed) == 1
    claimed_url = claimed[0][1]
    second_claim = await claim_due_feed_definitions(limit=1)
    assert len(second_claim) == 1
    assert second_claim[0][1] != claimed_url
    await save_endpoint_statuses(
        {claimed_url: {"status": "error", "error": "temporary"}}
    )

    async with session_scope() as db:
        source = await db.scalar(
            select(WebMediaSource).where(WebMediaSource.domain == "first.example")
        )
    assert source.failure_count == 1
    assert source.next_poll_at is not None
    next_poll_at = source.next_poll_at
    if next_poll_at.tzinfo is None:
        next_poll_at = next_poll_at.replace(tzinfo=UTC)
    assert next_poll_at > datetime.now(UTC)

    await set_source_enabled(second.id, False)
    async with session_scope() as db:
        source = await db.scalar(
            select(WebMediaSource).where(WebMediaSource.domain == "second.example")
        )
    assert source.status == "disabled"


async def test_stale_endpoint_claim_is_recovered_after_restart():
    source = await upsert_source(
        name="Restart sitemap",
        domain="restart-sitemap.example",
        enabled=True,
    )
    endpoint = await upsert_endpoint(
        source.id,
        "https://restart-sitemap.example/sitemap.xml",
        kind="sitemap",
    )
    stale_at = datetime.now(UTC) - timedelta(minutes=16)
    async with session_scope() as db:
        source_row = await db.get(WebMediaSource, source.id)
        endpoint_row = await db.get(WebMediaEndpoint, endpoint.id)
        source_row.polling_started_at = stale_at
        endpoint_row.polling_started_at = stale_at
        source_row.next_poll_at = datetime.now(UTC)
        await db.commit()

        await db.execute(
            WebMediaSource.__table__.update()
            .where(WebMediaSource.id != source.id)
            .values(next_poll_at=datetime.now(UTC) + timedelta(hours=1))
        )
        await db.commit()

    claimed = await claim_due_endpoints("sitemap", limit=1)

    assert [item.id for item in claimed] == [endpoint.id]
    async with session_scope() as db:
        endpoint_row = await db.get(WebMediaEndpoint, endpoint.id)
    assert endpoint_row.polling_started_at is not None


async def test_claim_due_endpoints_filters_urls_and_limits_one_source_batch():
    source = await upsert_source(
        name="Multiple RSS",
        domain="multiple-rss.example",
        enabled=True,
    )
    first = await upsert_endpoint(
        source.id,
        "https://multiple-rss.example/first.xml",
        kind="rss",
    )
    second = await upsert_endpoint(
        source.id,
        "https://multiple-rss.example/second.xml",
        kind="rss",
    )

    claimed = await claim_due_endpoints(
        "rss",
        limit=1,
        urls=[second.url],
    )

    assert [endpoint.url for endpoint in claimed] == [second.url]
    async with session_scope() as db:
        first_row = await db.get(WebMediaEndpoint, first.id)
        second_row = await db.get(WebMediaEndpoint, second.id)
        source_row = await db.get(WebMediaSource, source.id)
    assert first_row.polling_started_at is None
    assert second_row.polling_started_at is not None
    assert source_row.polling_started_at is not None

    await release_endpoint_poll(second.id)
    claimed_again = await claim_due_endpoints(
        "rss",
        limit=1,
        urls=[first.url],
    )
    assert [endpoint.url for endpoint in claimed_again] == [first.url]
