from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import select

from app.database import session_scope
from app.models import WebMediaEndpoint, WebMediaSource
from app.services.web_monitor.media_registry import upsert_endpoint, upsert_source
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
