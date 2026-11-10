from datetime import UTC, datetime

import httpx
import pytest

from app.services.web_monitor.providers import (
    GDELTProvider,
    GoogleNewsRSSProvider,
    ProviderHTTPError,
    RSSProvider,
    RSSProviderError,
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
