import httpx
import pytest

from app.services.web_monitor.providers import RSSProvider
from app.services.web_monitor.russian_sources import (
    RUSSIAN_MEDIA,
    get_source_enabled,
    merge_rss_feeds,
    set_source_enabled,
)


def test_catalog_contains_requested_media_and_unique_domains():
    names = {source.name for source in RUSSIAN_MEDIA}
    assert names == {
        "РБК",
        "ТАСС",
        "РИА Новости",
        "Интерфакс",
        "Коммерсантъ",
        "Ведомости",
        "Известия",
        "Газета.Ru",
        "Lenta.ru",
        "Российская газета",
        "Forbes Russia",
        "Фонтанка",
    }
    assert len({source.domain for source in RUSSIAN_MEDIA}) == 12


def test_catalog_and_user_rss_feeds_are_merged_without_duplicate_urls():
    feeds = merge_rss_feeds(
        [
            ("User copy", "https://tass.ru/rss/v2.xml"),
            ("User feed", "https://custom.example/feed.xml"),
        ]
    )
    urls = [url.rstrip("/").casefold() for _, url in feeds]
    assert urls.count("https://tass.ru/rss/v2.xml") == 1
    assert "https://custom.example/feed.xml" in urls


async def test_russian_media_toggle_is_persistent():
    tass = next(source for source in RUSSIAN_MEDIA if source.domain == "tass.ru")
    assert await get_source_enabled(tass) is True
    await set_source_enabled("tass.ru", False)
    assert await get_source_enabled(tass) is False
    await set_source_enabled("tass.ru", True)
    assert await get_source_enabled(tass) is True


@pytest.mark.asyncio
async def test_disabled_feed_is_not_polled():
    requests = []

    async def handler(request):
        requests.append(str(request.url))
        return httpx.Response(
            200,
            content=b"<rss><channel><item><title>News</title><link>https://example.com/a</link></item></channel></rss>",
            request=request,
        )

    feeds = RSSProvider(
        httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        [
            ("Disabled", "https://disabled.example/feed"),
            ("Enabled", "https://enabled.example/feed"),
        ],
    )
    feeds.set_enabled_urls({"https://enabled.example/feed"})
    try:
        await feeds.fetch()
    finally:
        await feeds.client.aclose()
    assert requests == ["https://enabled.example/feed"]
