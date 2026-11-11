from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import select

from app.database import session_scope
from app.models import WebMediaEndpoint, WebMediaSource
from app.services.web_monitor.media_registry import (
    bulk_import_sources,
    upsert_endpoint,
    upsert_source,
)
from app.services.web_monitor.providers import (
    GDELTProvider,
    GoogleNewsRSSProvider,
    ProviderHTTPError,
    RSSProvider,
    RSSProviderError,
    SitemapProvider,
)


@pytest.mark.asyncio
async def test_google_news_rss_is_parsed_without_html_search():
    body = """<?xml version='1.0'?><rss><channel><item>
      <title>Иван Иванов выступил</title><link>https://news.example/a</link>
      <pubDate>Tue, 10 Nov 2026 10:00:00 GMT</pubDate><description>Москва</description>
      <source url='https://news.example'>Example</source>
    </item></channel></rss>""".encode()

    async def handler(request):
        return httpx.Response(200, content=body, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        articles = await GoogleNewsRSSProvider(client).fetch("Иван Иванов")
    assert articles[0].title == "Иван Иванов выступил"
    assert articles[0].source_name == "Example"
    assert articles[0].published_at == datetime(2026, 11, 10, 10, tzinfo=UTC)


@pytest.mark.asyncio
async def test_gdelt_429_preserves_retry_after():
    async def handler(request):
        return httpx.Response(429, headers={"Retry-After": "17"}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderHTTPError) as error:
            await GDELTProvider(client).fetch("Иван Иванов")
    assert error.value.status_code == 429
    assert error.value.retry_after == 17


@pytest.mark.asyncio
async def test_direct_rss_provider():
    body = "<rss><channel><item><title>Иван Иванов</title><link>https://x.test/1</link></item></channel></rss>".encode()

    async def handler(request):
        return httpx.Response(200, content=body, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        articles = await RSSProvider(client, [("Test", "https://x.test/feed")]).fetch("")
    assert articles[0].source_name == "Test"


@pytest.mark.asyncio
async def test_rss_continues_after_one_broken_feed(caplog):
    good_body = (
        "<rss><channel><item><title>Иван Иванов</title>"
        "<link>https://x.test/1</link></item></channel></rss>"
    ).encode()

    async def handler(request):
        if request.url.host == "broken.test":
            return httpx.Response(503, request=request)
        return httpx.Response(200, content=good_body, request=request)

    feeds = [
        ("Broken", "https://broken.test/feed"),
        ("Good 1", "https://good-one.test/feed"),
        ("Good 2", "https://good-two.test/feed"),
    ]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        articles = await RSSProvider(client, feeds).fetch("")
    assert len(articles) == 2
    assert "https://broken.test/feed" in caplog.text


@pytest.mark.asyncio
async def test_rss_reports_total_failure():
    async def handler(request):
        return httpx.Response(500, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(RSSProviderError) as error:
            await RSSProvider(client, ["https://broken.test/feed"]).fetch("")
    assert error.value.failed_urls == ("https://broken.test/feed",)


@pytest.mark.asyncio
async def test_rss_provider_limits_a_thousand_registered_feeds_to_one_batch():
    rows = [
        {
            "name": f"RSS {index}",
            "domain": f"rss-batch-{index}.example",
            "rss_url": f"https://rss-batch-{index}.example/feed.xml",
        }
        for index in range(1000)
    ]
    await bulk_import_sources(rows, batch_size=100)
    async with session_scope() as db:
        await db.execute(
            WebMediaSource.__table__.update().values(
                enabled=True,
                next_poll_at=datetime.now(UTC),
            )
        )
        await db.execute(
            WebMediaEndpoint.__table__.update().values(enabled=True)
        )
        await db.commit()

    requests: list[str] = []

    async def handler(request):
        requests.append(str(request.url))
        return httpx.Response(
            200,
            content=(
                "<rss><channel><item><title>Batch item</title>"
                f"<link>{request.url}/article</link></item></channel></rss>"
            ),
            request=request,
        )

    feeds = [
        (f"RSS {index}", f"https://rss-batch-{index}.example/feed.xml")
        for index in range(1000)
    ]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        articles = await RSSProvider(client, feeds, max_concurrency=3).fetch()

    assert len(requests) == 3
    assert len(articles) == 3


@pytest.mark.asyncio
async def test_rss_provider_respects_source_interval_and_retries_after_error():
    source = await upsert_source(
        name="Scheduled RSS",
        domain="scheduled-rss.example",
        enabled=True,
        poll_interval_seconds=300,
    )
    endpoint = await upsert_endpoint(
        source.id,
        "https://scheduled-rss.example/feed.xml",
        kind="rss",
    )
    requests: list[str] = []
    failed = True

    async def handler(request):
        nonlocal failed
        requests.append(str(request.url))
        if failed:
            return httpx.Response(503, request=request)
        return httpx.Response(
            200,
            content=(
                "<rss><channel><item><title>Recovered</title>"
                "<link>https://scheduled-rss.example/article</link>"
                "</item></channel></rss>"
            ),
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        provider = RSSProvider(
            client,
            [("Scheduled RSS", "https://scheduled-rss.example/feed.xml")],
            max_concurrency=1,
        )
        with pytest.raises(RSSProviderError):
            await provider.fetch()
        assert requests == ["https://scheduled-rss.example/feed.xml"]

        async with session_scope() as db:
            failed_source = await db.get(WebMediaSource, source.id)
            failed_endpoint = await db.get(WebMediaEndpoint, endpoint.id)
            assert failed_source.failure_count == 1
            next_poll_at = failed_source.next_poll_at
            if next_poll_at.tzinfo is None:
                next_poll_at = next_poll_at.replace(tzinfo=UTC)
            assert next_poll_at > datetime.now(UTC)
            assert failed_endpoint.polling_started_at is None
            await db.commit()

        # A not-yet-due source is not fetched again.
        await provider.fetch()
        assert requests == ["https://scheduled-rss.example/feed.xml"]
        failed = False
        async with session_scope() as db:
            failed_source = await db.get(WebMediaSource, source.id)
            failed_source.next_poll_at = datetime.now(UTC) - timedelta(seconds=1)
            await db.commit()
        await provider.fetch()
        assert requests == [
            "https://scheduled-rss.example/feed.xml",
            "https://scheduled-rss.example/feed.xml",
        ]

    async with session_scope() as db:
        recovered_source = await db.get(WebMediaSource, source.id)
        recovered_endpoint = await db.get(WebMediaEndpoint, endpoint.id)
    assert recovered_source.failure_count == 0
    assert recovered_endpoint.polling_started_at is None


@pytest.mark.asyncio
async def test_rss_provider_recovers_stale_claim_after_restart():
    source = await upsert_source(
        name="Restart RSS",
        domain="restart-rss.example",
        enabled=True,
    )
    endpoint = await upsert_endpoint(
        source.id,
        "https://restart-rss.example/feed.xml",
        kind="rss",
    )
    stale_at = datetime.now(UTC) - timedelta(minutes=16)
    async with session_scope() as db:
        source_row = await db.get(WebMediaSource, source.id)
        endpoint_row = await db.get(WebMediaEndpoint, endpoint.id)
        source_row.polling_started_at = stale_at
        endpoint_row.polling_started_at = stale_at
        source_row.next_poll_at = datetime.now(UTC)
        await db.commit()

    async def handler(request):
        return httpx.Response(
            200,
            content=(
                "<rss><channel><item><title>Restarted</title>"
                "<link>https://restart-rss.example/article</link>"
                "</item></channel></rss>"
            ),
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        articles = await RSSProvider(
            client,
            [("Restart RSS", "https://restart-rss.example/feed.xml")],
            max_concurrency=1,
        ).fetch()

    assert [article.url for article in articles] == [
        "https://restart-rss.example/article"
    ]
    async with session_scope() as db:
        endpoint_row = await db.get(WebMediaEndpoint, endpoint.id)
    assert endpoint_row.polling_started_at is None


@pytest.mark.asyncio
async def test_sitemap_provider_processes_nested_index():
    root = "https://sitemap.example/sitemap.xml"
    child = "https://sitemap.example/news-index.xml"
    leaf = "https://sitemap.example/news.xml"
    bodies = {
        root: f"<sitemapindex><sitemap><loc>{child}</loc></sitemap></sitemapindex>",
        child: f"<sitemapindex><sitemap><loc>{leaf}</loc></sitemap></sitemapindex>",
        leaf: (
            "<urlset xmlns:news='https://www.google.com/schemas/sitemap-news/0.9'>"
            "<url><loc>https://sitemap.example/news/1</loc>"
            "<lastmod>2026-11-11T10:00:00+00:00</lastmod>"
            "<news:title>Главная новость</news:title></url></urlset>"
        ),
    }

    async def handler(request):
        return httpx.Response(
            200,
            content=bodies[str(request.url)],
            headers={"ETag": '"stable"'},
            request=request,
        )

    source = await upsert_source(
        name="Sitemap",
        domain="sitemap.example",
        enabled=True,
    )
    endpoint = await upsert_endpoint(source.id, root, kind="sitemap")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        articles = await SitemapProvider(client, max_concurrency=1)._fetch_endpoint(endpoint)

    assert [article.url for article in articles] == ["https://sitemap.example/news/1"]
    assert articles[0].published_at == datetime(2026, 11, 11, 10, tzinfo=UTC)


@pytest.mark.asyncio
async def test_sitemap_index_304_reuses_children_and_polls_them():
    root = "https://sitemap-cache.example/sitemap.xml"
    child = "https://sitemap-cache.example/news-index.xml"
    leaf = "https://sitemap-cache.example/news.xml"
    phase = 1
    requested: list[str] = []

    async def handler(request):
        url = str(request.url)
        requested.append(url)
        if phase == 2 and url == root:
            return httpx.Response(304, headers={"ETag": '"root-v1"'}, request=request)
        if phase == 2 and url == child:
            content = (
                "<urlset><url><loc>https://sitemap-cache.example/news/2</loc>"
                "<lastmod>2026-11-11T11:00:00+00:00</lastmod>"
                "<title>Вторая новость</title></url></urlset>"
            ).encode()
            return httpx.Response(200, content=content, request=request)
        bodies = {
            root: f"<sitemapindex><sitemap><loc>{child}</loc></sitemap></sitemapindex>",
            child: f"<sitemapindex><sitemap><loc>{leaf}</loc></sitemap></sitemapindex>",
            leaf: (
                "<urlset><url><loc>https://sitemap-cache.example/news/1</loc>"
                "<lastmod>2026-11-11T10:00:00+00:00</lastmod></url></urlset>"
            ),
        }
        return httpx.Response(200, content=bodies[url].encode(), request=request)

    source = await upsert_source(
        name="Sitemap cache",
        domain="sitemap-cache.example",
        enabled=True,
    )
    endpoint = await upsert_endpoint(source.id, root, kind="sitemap")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        first = await SitemapProvider(client, max_concurrency=1)._fetch_endpoint(endpoint)
        assert [article.url for article in first] == [
            "https://sitemap-cache.example/news/1"
        ]

        async with session_scope() as db:
            root_endpoint = await db.scalar(
                select(WebMediaEndpoint).where(WebMediaEndpoint.url == root)
            )
            assert root_endpoint.child_sitemap_urls == f'["{child}"]'
            source_row = await db.get(WebMediaSource, source.id)
            source_row.next_poll_at = datetime.now(UTC)
            await db.commit()

        requested.clear()
        phase = 2
        async with session_scope() as db:
            root_endpoint = await db.scalar(
                select(WebMediaEndpoint).where(WebMediaEndpoint.url == root)
            )
        second = await SitemapProvider(client, max_concurrency=1)._fetch_endpoint(root_endpoint)

    assert requested == [root, child]
    assert [article.url for article in second] == [
        "https://sitemap-cache.example/news/2"
    ]


@pytest.mark.asyncio
async def test_sitemap_poll_error_releases_endpoint_lease():
    async def handler(request):
        return httpx.Response(503, request=request)

    source = await upsert_source(
        name="Broken sitemap",
        domain="broken-sitemap.example",
        enabled=True,
    )
    endpoint = await upsert_endpoint(
        source.id,
        "https://broken-sitemap.example/sitemap.xml",
        kind="sitemap",
    )
    async with session_scope() as db:
        await db.execute(
            WebMediaSource.__table__.update()
            .where(WebMediaSource.id != source.id)
            .values(next_poll_at=datetime(2026, 11, 12, tzinfo=UTC))
        )
        await db.commit()

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ProviderHTTPError):
            await SitemapProvider(client, max_concurrency=1).fetch()

    async with session_scope() as db:
        checked_endpoint = await db.get(WebMediaEndpoint, endpoint.id)
        checked_source = await db.get(WebMediaSource, source.id)
    assert checked_endpoint.polling_started_at is None
    assert checked_source.polling_started_at is None
