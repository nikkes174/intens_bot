import httpx

from app.services.web_monitor.rss_discovery import PublicDocument, discover_feeds
from app.services.web_monitor.source_discovery import (
    crawl_sitemap_tree,
    parse_sitemap_xml,
)


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


async def test_sitemap_tree_follows_nested_indexes_and_ignores_cycles():
    index_url = "https://example.ru/sitemap.xml"
    nested_url = "https://example.ru/nested.xml"
    leaf_url = "https://example.ru/news.xml"
    bodies = {
        index_url: f"""<sitemapindex>
          <sitemap><loc>{nested_url}</loc></sitemap>
          <sitemap><loc>{index_url}</loc></sitemap>
        </sitemapindex>""".encode(),
        nested_url: f"""<sitemapindex>
          <sitemap><loc>{leaf_url}</loc></sitemap>
          <sitemap><loc>{nested_url}</loc></sitemap>
        </sitemapindex>""".encode(),
        leaf_url: b"""<urlset><url>
          <loc>https://example.ru/news/1</loc>
          <lastmod>2026-11-11T10:00:00+00:00</lastmod>
        </url></urlset>""",
    }
    calls = []

    async def fetcher(url):
        calls.append(url)
        content = bodies[url]
        return (
            PublicDocument(
                url=url,
                status_code=200,
                content=content,
                headers={},
                content_hash="hash-" + url.rsplit("/", 1)[-1],
            ),
            True,
        )

    async with httpx.AsyncClient() as client:
        tree = await crawl_sitemap_tree(
            client,
            index_url,
            fetcher=fetcher,
            max_depth=4,
            max_documents=10,
        )

    assert calls == [index_url, nested_url, leaf_url]
    assert [entry.url for entry in tree.entries] == ["https://example.ru/news/1"]
    assert not tree.errors


async def test_sitemap_tree_limits_documents_and_total_bytes():
    urls = [f"https://example.ru/{index}.xml" for index in ("one", "two", "three")]

    async def fetcher(url):
        position = urls.index(url)
        children = (
            f"<sitemap><loc>{urls[position + 1]}</loc></sitemap>"
            if position + 1 < len(urls)
            else ""
        )
        content = f"<sitemapindex>{children}</sitemapindex>".encode()
        return (
            PublicDocument(
                url=url,
                status_code=200,
                content=content,
                headers={},
                content_hash=url,
            ),
            True,
        )

    async with httpx.AsyncClient() as client:
        tree = await crawl_sitemap_tree(
            client,
            urls[0],
            fetcher=fetcher,
            max_documents=1,
            max_bytes=10_000,
        )

    assert len(tree.documents) == 1
    assert any("лимит документов" in error for error in tree.errors)
