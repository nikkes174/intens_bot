from datetime import UTC, datetime

import httpx
import pytest

from app.services.web_monitor.providers import (
    GDELTProvider,
    GoogleNewsRSSProvider,
    ProviderHTTPError,
    RSSProvider,
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
