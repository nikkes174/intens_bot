import httpx

from app.services.web_monitor.rss_discovery import discover_feeds
from app.services.web_monitor.source_discovery import parse_sitemap_xml


def _feed_body() -> bytes:
    return """<rss><channel><item>
        <title>Иван Иванов сообщил о событии</title>
        <link>https://example.ru/news/1</link>
    </item></channel></rss>""".encode()


async def test_rss_discovery_requires_a_parseable_feed():
    async def handler(request):
        if request.url.path == "/":
            return httpx.Response(
                200,
                headers={"Content-Type": "text/html"},
                content=(
                    b'<html><head><link rel="alternate" type="application/rss+xml" '
                    b'href="/rss.xml"></head></html>'
                ),
                request=request,
            )
        if request.url.path == "/rss.xml":
            return httpx.Response(200, content=_feed_body(), request=request)
        return httpx.Response(200, content=b"<html>not a feed</html>", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        feeds = await discover_feeds(client, "https://example.ru")

    assert feeds == ["https://example.ru/rss.xml"]


def test_sitemap_index_and_google_news_sitemap_are_parsed():
    index = b"""<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <sitemap><loc>https://example.ru/news-sitemap.xml</loc></sitemap>
    </sitemapindex>"""
    parsed_index = parse_sitemap_xml(index)
    assert parsed_index.kind == "index"
    assert parsed_index.sitemap_urls == ("https://example.ru/news-sitemap.xml",)

    urlset = """<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
      xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">
      <url>
        <loc>https://example.ru/news/1</loc>
        <lastmod>2026-11-11T10:00:00Z</lastmod>
        <news:news><news:publication>
          <news:publication_date>2026-11-11T09:30:00Z</news:publication_date>
          <news:title>Главная новость</news:title>
        </news:publication></news:news>
      </url>
    </urlset>""".encode()
    parsed_urlset = parse_sitemap_xml(urlset)
    assert parsed_urlset.kind == "urlset"
    assert parsed_urlset.entries[0].url == "https://example.ru/news/1"
    assert parsed_urlset.entries[0].title == "Главная новость"
    assert parsed_urlset.entries[0].published_at is not None
