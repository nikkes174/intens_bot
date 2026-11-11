"""Discovery and parsing helpers for news sitemaps and public news pages."""

from __future__ import annotations

import calendar
import email.utils
import re
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

import feedparser
import httpx

from app.config import get_settings
from app.services.web_monitor.rss_discovery import PublicDocument, fetch_public_document

_SITEMAP_PATHS = ("/sitemap.xml", "/sitemap_index.xml")
_SITEMAP_LINE_RE = re.compile(r"^\s*sitemap:\s*(\S+)\s*$", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class SitemapEntry:
    url: str
    lastmod: datetime | None = None
    published_at: datetime | None = None
    title: str = ""


@dataclass(frozen=True)
class SitemapParseResult:
    kind: str
    sitemap_urls: tuple[str, ...] = ()
    entries: tuple[SitemapEntry, ...] = ()


@dataclass(frozen=True)
class SitemapDocument:
    url: str
    depth: int
    document: PublicDocument
    parsed: SitemapParseResult | None
    changed: bool


@dataclass(frozen=True)
class SitemapFetchResult:
    """A sitemap response and an optional parse cached for unchanged content."""

    document: PublicDocument
    changed: bool
    cached_parse: SitemapParseResult | None = None


@dataclass(frozen=True)
class SitemapTreeResult:
    documents: tuple[SitemapDocument, ...] = ()
    entries: tuple[SitemapEntry, ...] = ()
    errors: tuple[str, ...] = ()
    total_bytes: int = 0


@dataclass(frozen=True)
class SourceDiscoveryResult:
    domain: str
    feeds: tuple[str, ...] = ()
    sitemaps: tuple[str, ...] = ()
    news_pages: tuple[str, ...] = ()
    sample_entries: tuple[SitemapEntry, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def methods(self) -> tuple[str, ...]:
        methods = []
        if self.feeds:
            methods.append("rss")
        if self.sitemaps:
            methods.append("sitemap")
        if self.news_pages:
            methods.append("news_page")
        return tuple(methods)

    @property
    def publication_count(self) -> int:
        return len({entry.url for entry in self.sample_entries if entry.url})

    @property
    def dated_publication_count(self) -> int:
        return sum(
            1
            for entry in self.sample_entries
            if entry.published_at is not None or entry.lastmod is not None
        )


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].casefold()


def _parse_datetime(value: str | None) -> datetime | None:
    raw = (value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _feed_entry_datetime(entry) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if isinstance(parsed, tuple):
        try:
            return datetime.fromtimestamp(calendar.timegm(parsed), tz=UTC)
        except (OverflowError, TypeError, ValueError):
            return None
    raw = entry.get("published") or entry.get("updated")
    if isinstance(raw, str):
        try:
            value = email.utils.parsedate_to_datetime(raw)
        except (TypeError, ValueError, OverflowError):
            return None
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    return None


def _safe_xml(content: bytes):
    # ElementTree is used only after rejecting DTD/entity declarations.  This
    # keeps the dependency footprint small while preventing XML entity attacks.
    lowered = content[:65536].lower()
    if b"<!doctype" in lowered or b"<!entity" in lowered:
        raise ValueError("DTD and external entities are not allowed")
    return ElementTree.fromstring(content)


def parse_sitemap_xml(content: bytes) -> SitemapParseResult:
    root = _safe_xml(content)
    root_name = _local_name(root.tag)
    if root_name == "sitemapindex":
        urls = []
        for node in root:
            for child in node.iter():
                if _local_name(child.tag) == "loc" and (child.text or "").strip():
                    urls.append(child.text.strip())
                    break
        return SitemapParseResult(kind="index", sitemap_urls=tuple(dict.fromkeys(urls)))
    if root_name != "urlset":
        raise ValueError("Expected sitemap index or urlset")
    entries: list[SitemapEntry] = []
    for node in root:
        values = {_local_name(child.tag): (child.text or "").strip() for child in node.iter()}
        url = values.get("loc", "")
        if not url:
            continue
        published_at = _parse_datetime(values.get("publication_date"))
        entries.append(
            SitemapEntry(
                url=url,
                lastmod=_parse_datetime(values.get("lastmod")),
                published_at=published_at,
                title=values.get("title") or values.get("name", ""),
            )
        )
    return SitemapParseResult(kind="urlset", entries=tuple(entries))


def _canonical_sitemap_url(value: str) -> str:
    parts = urlsplit((value or "").strip())
    if (
        parts.scheme.casefold() not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
    ):
        raise ValueError("Небезопасный URL sitemap")
    return urlunsplit(
        (
            parts.scheme.casefold(),
            parts.netloc.casefold(),
            parts.path or "/",
            parts.query,
            "",
        )
    )


async def crawl_sitemap_tree(
    client: httpx.AsyncClient,
    root_url: str,
    *,
    fetcher: Callable[[str], Awaitable[SitemapFetchResult]] | None = None,
    max_depth: int | None = None,
    max_documents: int | None = None,
    max_bytes: int | None = None,
) -> SitemapTreeResult:
    """Fetch a bounded sitemap tree, including nested indexes and cycles safely."""

    settings = get_settings()
    max_depth = max_depth or settings.web_source_max_sitemap_depth
    max_documents = max_documents or settings.web_source_max_sitemap_documents
    max_bytes = max_bytes or settings.web_source_max_sitemap_bytes
    fetcher = fetcher or (
        lambda url: _fetch_sitemap_document(client, url)
    )
    pending = deque([(_canonical_sitemap_url(root_url), 0)])
    visited: set[str] = set()
    documents: list[SitemapDocument] = []
    entries: list[SitemapEntry] = []
    errors: list[str] = []
    total_bytes = 0
    while pending and len(documents) < max_documents:
        sitemap_url, depth = pending.popleft()
        if sitemap_url in visited:
            continue
        visited.add(sitemap_url)
        if depth > max_depth:
            errors.append(f"{sitemap_url}: превышена глубина sitemap")
            continue
        try:
            fetched = await fetcher(sitemap_url)
            if isinstance(fetched, SitemapFetchResult):
                result = fetched
            else:
                # Keep the helper compatible with callers using the original
                # (document, changed) callback contract.
                document, changed = fetched
                result = SitemapFetchResult(document=document, changed=changed)
            document = result.document
            changed = result.changed
            total_bytes += len(document.content)
            if total_bytes > max_bytes:
                errors.append("Превышен общий лимит данных sitemap")
                break
            parsed = result.cached_parse
            if changed and document.status_code != 304:
                try:
                    parsed = parse_sitemap_xml(document.content)
                except (ElementTree.ParseError, ValueError) as exc:
                    if depth == 0:
                        raise
                    errors.append(f"{sitemap_url}: {exc}")
                    continue
            documents.append(
                SitemapDocument(
                    url=sitemap_url,
                    depth=depth,
                    document=document,
                    parsed=parsed,
                    changed=changed,
                )
            )
            if parsed is None:
                continue
            if changed:
                entries.extend(parsed.entries)
            for child_url in parsed.sitemap_urls:
                try:
                    canonical_child = _canonical_sitemap_url(child_url)
                except ValueError as exc:
                    errors.append(f"{sitemap_url}: {exc}")
                    continue
                if canonical_child not in visited:
                    pending.append((canonical_child, depth + 1))
        except (OSError, PermissionError, ValueError, httpx.HTTPError) as exc:
            if depth == 0:
                raise
            errors.append(f"{sitemap_url}: {exc}")
    if pending and len(documents) >= max_documents:
        errors.append("Достигнут лимит документов sitemap")
    return SitemapTreeResult(
        documents=tuple(documents),
        entries=tuple(entries[: settings.web_source_max_sitemap_entries]),
        errors=tuple(errors),
        total_bytes=total_bytes,
    )


async def _fetch_sitemap_document(
    client: httpx.AsyncClient,
    url: str,
) -> SitemapFetchResult:
    return SitemapFetchResult(
        document=await fetch_public_document(client, url),
        changed=True,
    )


async def _fetch_robots_sitemaps(client: httpx.AsyncClient, origin: str) -> list[str]:
    try:
        document = await fetch_public_document(
            client,
            f"{origin}/robots.txt",
            respect_robots=False,
        )
    except (OSError, PermissionError, ValueError, httpx.HTTPError):
        return []
    text = document.content.decode("utf-8", errors="replace")
    return [match.group(1) for match in _SITEMAP_LINE_RE.finditer(text)]


async def discover_sitemaps(
    client: httpx.AsyncClient,
    domain: str,
) -> tuple[list[str], list[SitemapEntry], list[str]]:
    """Find verified sitemap endpoints and a bounded sample of publications."""

    if not get_settings().web_sitemap_enabled:
        return [], [], []
    homepage = domain if "://" in domain else f"https://{domain}/"
    parts = urlsplit(homepage)
    origin = f"{parts.scheme}://{parts.netloc}"
    candidates = [urljoin(origin, path) for path in _SITEMAP_PATHS]
    candidates.extend(await _fetch_robots_sitemaps(client, origin))
    candidates = list(dict.fromkeys(candidates))[: get_settings().web_source_max_sitemaps_per_domain]
    entries: list[SitemapEntry] = []
    verified: list[str] = []
    errors: list[str] = []
    seen_documents: set[str] = set()
    remaining_documents = get_settings().web_source_max_sitemap_documents
    remaining_bytes = get_settings().web_source_max_sitemap_bytes
    for candidate in candidates:
        if remaining_documents <= 0 or remaining_bytes <= 0:
            break
        try:
            canonical_candidate = _canonical_sitemap_url(candidate)
        except ValueError as exc:
            errors.append(f"{candidate}: {exc}")
            continue
        if canonical_candidate in seen_documents:
            continue
        try:
            tree = await crawl_sitemap_tree(
                client,
                canonical_candidate,
                max_documents=remaining_documents,
                max_bytes=remaining_bytes,
            )
        except (OSError, PermissionError, ValueError, httpx.HTTPError) as exc:
            errors.append(f"{candidate}: {exc}")
            continue
        verified.extend(document.url for document in tree.documents)
        seen_documents.update(document.url for document in tree.documents)
        entries.extend(tree.entries)
        errors.extend(tree.errors)
        remaining_documents -= len(tree.documents)
        remaining_bytes -= tree.total_bytes
        if len(entries) >= get_settings().web_source_max_sitemap_entries:
            break
    return (
        list(dict.fromkeys(verified))[: get_settings().web_source_max_sitemaps_per_domain],
        entries[: get_settings().web_source_max_sitemap_entries],
        errors,
    )


class _ArticleLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.casefold() != "a":
            return
        href = dict(attrs).get("href")
        if href:
            self.links.append(href.strip())


def _public_news_links(document: PublicDocument) -> list[str]:
    parser = _ArticleLinkParser()
    parser.feed(document.content.decode("utf-8", errors="replace"))
    parts = urlsplit(document.url)
    links = []
    for href in parser.links:
        absolute = urljoin(document.url, href)
        target = urlsplit(absolute)
        if (
            target.scheme in {"http", "https"}
            and target.netloc == parts.netloc
            and target.path.rstrip("/") not in {"", "/"}
        ):
            links.append(absolute)
    return list(dict.fromkeys(links))[:20]


def extract_news_page_links(document: PublicDocument) -> list[str]:
    return _public_news_links(document)


async def inspect_source(client: httpx.AsyncClient, domain: str) -> SourceDiscoveryResult:
    """Run bounded discovery for one domain without crawling the whole site."""

    normalized_domain = urlsplit(
        domain if "://" in domain else f"https://{domain}"
    ).hostname or domain
    feeds: list[str] = []
    sitemaps: list[str] = []
    entries: list[SitemapEntry] = []
    errors: list[str] = []
    try:
        if get_settings().web_rss_discovery_enabled:
            from app.services.web_monitor.rss_discovery import discover_feeds

            feeds = await discover_feeds(client, domain)
            for feed_url in feeds[:3]:
                try:
                    feed_document = await fetch_public_document(client, feed_url)
                    parsed_feed = feedparser.parse(feed_document.content)
                    entries.extend(
                        SitemapEntry(
                            url=str(entry.get("link") or feed_url),
                            published_at=_feed_entry_datetime(entry),
                            title=str(entry.get("title") or ""),
                        )
                        for entry in parsed_feed.entries[:5]
                    )
                except (OSError, PermissionError, ValueError, httpx.HTTPError) as exc:
                    errors.append(f"RSS sample {feed_url}: {exc}")
    except (OSError, PermissionError, ValueError, httpx.HTTPError) as exc:
        errors.append(f"RSS: {exc}")
    try:
        sitemaps, sitemap_entries, sitemap_errors = await discover_sitemaps(client, domain)
        entries.extend(sitemap_entries)
        errors.extend(sitemap_errors)
    except (OSError, PermissionError, ValueError, httpx.HTTPError) as exc:
        errors.append(f"sitemap: {exc}")
    news_pages: list[str] = []
    if (
        get_settings().web_public_news_pages_enabled
        and not feeds
        and not sitemaps
    ):
        homepage = domain if "://" in domain else f"https://{domain}/"
        try:
            document = await fetch_public_document(client, homepage)
            news_pages = _public_news_links(document)
        except (OSError, PermissionError, ValueError, httpx.HTTPError) as exc:
            errors.append(f"news_page: {exc}")
    return SourceDiscoveryResult(
        domain=normalized_domain.casefold(),
        feeds=tuple(feeds),
        sitemaps=tuple(sitemaps),
        news_pages=tuple(news_pages),
        sample_entries=tuple(entries[: get_settings().web_source_max_sitemap_entries]),
        errors=tuple(errors),
    )
