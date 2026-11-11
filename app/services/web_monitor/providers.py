from __future__ import annotations

import asyncio
import calendar
import email.utils
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import monotonic
from typing import Any, Protocol
from urllib.parse import quote_plus, urlsplit

import feedparser
import httpx

from app.config import get_settings
from app.services.web_monitor.media_registry import (
    claim_due_endpoints,
    get_registered_endpoint_urls,
    release_endpoint_poll,
    save_endpoint_check,
    save_endpoint_statuses,
    save_sitemap_children,
    upsert_endpoint,
)
from app.services.web_monitor.rss_discovery import fetch_public_document
from app.services.web_monitor.source_discovery import (
    SitemapFetchResult,
    SitemapParseResult,
    crawl_sitemap_tree,
    extract_news_page_links,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WebArticle:
    provider: str
    title: str
    url: str
    source_name: str
    published_at: datetime | None
    description: str = ""
    full_text: str = ""


class ProviderHTTPError(RuntimeError):
    def __init__(self, provider: str, status_code: int, message: str = "", retry_after: float | None = None):
        self.provider = provider
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(message or f"{provider} returned HTTP {status_code}")


class RSSProviderError(ProviderHTTPError):
    def __init__(self, message: str, failed_urls: Iterable[str]):
        super().__init__("rss", 0, message)
        self.failed_urls = tuple(failed_urls)


class WebProvider(Protocol):
    name: str
    min_interval: float

    async def fetch(self, query: str) -> list[WebArticle]: ...


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            retry_at = email.utils.parsedate_to_datetime(value)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


async def _request(client: httpx.AsyncClient, provider: str, url: str, **kwargs: Any) -> httpx.Response:
    try:
        response = await client.get(url, **kwargs)
    except httpx.HTTPError as exc:
        raise ProviderHTTPError(provider, 0, str(exc)) from exc
    if response.status_code == 429:
        raise ProviderHTTPError(provider, 429, retry_after=_retry_after(response))
    if response.status_code >= 400:
        raise ProviderHTTPError(provider, response.status_code)
    return response


def _as_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, tuple):
        try:
            return datetime.fromtimestamp(calendar.timegm(value), tz=UTC)
        except (OverflowError, TypeError, ValueError):
            return None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        for parser in (
            lambda: datetime.fromisoformat(
                raw[:-1] + "+00:00" if raw.endswith("Z") else raw
            ),
            lambda: email.utils.parsedate_to_datetime(raw),
            lambda: datetime.strptime(raw, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC),
        ):
            try:
                parsed = parser()
                return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
            except (TypeError, ValueError, OverflowError):
                continue
    return None


class GoogleNewsRSSProvider:
    name = "google_news"
    min_interval = 60.0

    def __init__(self, client: httpx.AsyncClient, language: str = "ru", country: str = "RU"):
        self.client = client
        self.language = language
        self.country = country

    async def fetch(self, query: str) -> list[WebArticle]:
        url = (
            "https://news.google.com/rss/search?q="
            f"{quote_plus(query)}&hl={self.language}&gl={self.country}&ceid={self.country}:{self.language}"
        )
        response = await _request(self.client, self.name, url)
        parsed = feedparser.parse(response.content)
        articles: list[WebArticle] = []
        for entry in parsed.entries:
            link = str(entry.get("link") or "").strip()
            if not link:
                continue
            source = entry.get("source") or {}
            source_name = source.get("title", "Google News") if hasattr(source, "get") else "Google News"
            articles.append(
                WebArticle(
                    provider=self.name,
                    title=str(entry.get("title") or "").strip(),
                    url=link,
                    source_name=str(source_name or "Google News"),
                    published_at=_as_datetime(entry.get("published_parsed") or entry.get("published")),
                    description=str(entry.get("summary") or entry.get("description") or "").strip(),
                )
            )
        return articles


class GDELTProvider:
    name = "gdelt"

    def __init__(self, client: httpx.AsyncClient, min_interval: float = 60.0, max_records: int = 50):
        self.client = client
        self.min_interval = max(30.0, min_interval)
        self.max_records = max(1, max_records)
        self.endpoint = "https://api.gdeltproject.org/api/v2/doc/doc"

    async def fetch(self, query: str) -> list[WebArticle]:
        params = {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "maxrecords": str(self.max_records),
            "sort": "datedesc",
        }
        try:
            response = await self.client.get(self.endpoint, params=params)
        except httpx.HTTPError as exc:
            raise ProviderHTTPError(self.name, 0, str(exc)) from exc
        if response.status_code == 429:
            raise ProviderHTTPError(self.name, 429, retry_after=_retry_after(response))
        if response.status_code >= 400:
            raise ProviderHTTPError(self.name, response.status_code)
        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderHTTPError(self.name, response.status_code, "Invalid JSON response") from exc
        articles: list[WebArticle] = []
        for item in payload.get("articles", []) if isinstance(payload, dict) else []:
            if not isinstance(item, dict) or not item.get("url"):
                continue
            articles.append(
                WebArticle(
                    provider=self.name,
                    title=str(item.get("title") or "").strip(),
                    url=str(item["url"]).strip(),
                    source_name=str(item.get("domain") or item.get("sourcecountry") or "GDELT"),
                    published_at=_as_datetime(item.get("seendate") or item.get("published")),
                    description=str(item.get("snippet") or item.get("description") or "").strip(),
                )
            )
        return articles


class RSSProvider:
    name = "rss"

    def __init__(
        self,
        client: httpx.AsyncClient,
        feeds: Iterable[str | tuple[str, str]],
        min_interval: float = 60.0,
        max_concurrency: int | None = None,
    ):
        self.client = client
        self.min_interval = max(30.0, min_interval)
        self.feeds: list[tuple[str, str]] = []
        for feed in feeds:
            if isinstance(feed, tuple):
                source_name, url = feed
            else:
                source_name, url = "RSS", feed
            if str(url).strip():
                self.feeds.append((str(source_name).strip() or "RSS", str(url).strip()))
        self._enabled_urls: set[str] | None = None
        self.feed_status: dict[str, dict[str, str]] = {}
        self.last_batch_status: dict[str, dict[str, str]] = {}
        self.max_concurrency = max(
            1,
            max_concurrency or get_settings().web_source_poll_concurrency,
        )
        self._domain_locks: dict[str, asyncio.Lock] = {}
        self._domain_last_request: dict[str, float] = {}
        self._url_locks: dict[str, asyncio.Lock] = {}
        self._external_next_poll: dict[str, float] = {}
        self._external_failure_count: dict[str, int] = {}

    @staticmethod
    def _feed_key(url: str) -> str:
        return url.strip().rstrip("/").casefold()

    def set_enabled_urls(self, urls: Iterable[str] | None) -> None:
        self._enabled_urls = None if urls is None else {self._feed_key(url) for url in urls}

    def set_feeds(self, feeds: Iterable[str | tuple[str, str]]) -> None:
        known: list[tuple[str, str]] = []
        seen: set[str] = set()
        for feed in feeds:
            if isinstance(feed, tuple):
                source_name, url = feed
            else:
                source_name, url = "RSS", feed
            normalized_url = str(url).strip()
            key = self._feed_key(normalized_url)
            if normalized_url and key not in seen:
                seen.add(key)
                known.append((str(source_name).strip() or "RSS", normalized_url))
        self.feeds = known

    def _active_feeds(self) -> list[tuple[str, str]]:
        if self._enabled_urls is None:
            return list(self.feeds)
        return [
            (source_name, url)
            for source_name, url in self.feeds
            if self._feed_key(url) in self._enabled_urls
        ]

    async def _respect_domain_rate(self, url: str) -> None:
        domain = (urlsplit(url).hostname or "").casefold()
        lock = self._domain_locks.setdefault(domain, asyncio.Lock())
        async with lock:
            now = monotonic()
            previous = self._domain_last_request.get(domain)
            if previous is not None:
                await asyncio.sleep(max(0.0, 0.2 - (now - previous)))
            self._domain_last_request[domain] = monotonic()

    async def _fetch_feed(self, source_name: str, url: str) -> list[WebArticle]:
        await self._respect_domain_rate(url)
        response = await _request(self.client, self.name, url)
        parsed = feedparser.parse(response.content)
        if getattr(parsed, "bozo", False) and not parsed.entries:
            raise ValueError("invalid RSS/Atom XML")
        articles: list[WebArticle] = []
        for entry in parsed.entries:
            link = str(entry.get("link") or "").strip()
            if not link:
                continue
            entry_source = entry.get("source") or {}
            resolved_source = (
                entry_source.get("title") if hasattr(entry_source, "get") else None
            ) or source_name
            articles.append(
                WebArticle(
                    provider=self.name,
                    title=str(entry.get("title") or "").strip(),
                    url=link,
                    source_name=str(resolved_source),
                    published_at=_as_datetime(
                        entry.get("published_parsed") or entry.get("published")
                    ),
                    description=str(
                        entry.get("summary") or entry.get("description") or ""
                    ).strip(),
                )
            )
        return articles

    async def fetch(self, query: str = "") -> list[WebArticle]:
        articles: list[WebArticle] = []
        failed_urls: list[str] = []
        self.last_batch_status = {}
        active_feeds = self._active_feeds()
        if not active_feeds:
            return articles

        active_urls = [url for _, url in active_feeds]
        claimed_endpoints = await claim_due_endpoints(
            "rss",
            limit=self.max_concurrency,
            urls=active_urls,
        )
        try:
            selected_endpoints = []
            seen_endpoint_keys: set[str] = set()
            for endpoint in claimed_endpoints:
                key = self._feed_key(endpoint.url)
                if key in seen_endpoint_keys or len(selected_endpoints) >= self.max_concurrency:
                    continue
                seen_endpoint_keys.add(key)
                selected_endpoints.append(endpoint)
            claimed_keys = {self._feed_key(endpoint.url) for endpoint in selected_endpoints}
            registered_urls = await get_registered_endpoint_urls("rss")
            registered_keys = {self._feed_key(url) for url in registered_urls}
            now = monotonic()
            external_feeds = [
                feed
                for feed in active_feeds
                if self._feed_key(feed[1]) not in claimed_keys
                and self._feed_key(feed[1]) not in registered_keys
                and now >= self._external_next_poll.get(self._feed_key(feed[1]), 0)
            ]
            free_slots = max(0, self.max_concurrency - len(selected_endpoints))
            external_feeds = external_feeds[:free_slots]
            jobs = [
                (
                    next(
                        (
                            source_name
                            for source_name, feed_url in active_feeds
                            if self._feed_key(feed_url) == self._feed_key(endpoint.url)
                        ),
                        "RSS",
                    ),
                    endpoint.url,
                    endpoint.id,
                )
                for endpoint in selected_endpoints
            ]
            jobs.extend((*feed, None) for feed in external_feeds)
            if not jobs:
                return articles
            queue: asyncio.Queue[tuple[str, str, int | None]] = asyncio.Queue(
                maxsize=max(1, self.max_concurrency * 2)
            )
            workers = []

            async def worker() -> None:
                while True:
                    source_name, url, endpoint_id = await queue.get()
                    try:
                        lock = self._url_locks.setdefault(
                            self._feed_key(url), asyncio.Lock()
                        )
                        async with lock:
                            feed_articles = await self._fetch_feed(source_name, url)
                        articles.extend(feed_articles)
                        status = {"status": "ok", "error": ""}
                        self.feed_status[url] = status
                        self.last_batch_status[url] = status
                        if endpoint_id is None:
                            key = self._feed_key(url)
                            self._external_failure_count[key] = 0
                            self._external_next_poll[key] = monotonic() + self.min_interval
                    except Exception as exc:  # noqa: BLE001 - one feed must not stop the batch
                        failed_urls.append(url)
                        status = {"status": "error", "error": str(exc)}
                        self.feed_status[url] = status
                        self.last_batch_status[url] = status
                        logger.warning("RSS feed %s failed: %s", url, exc)
                        if endpoint_id is None:
                            key = self._feed_key(url)
                            failures = self._external_failure_count.get(key, 0) + 1
                            self._external_failure_count[key] = failures
                            self._external_next_poll[key] = monotonic() + min(
                                self.min_interval * 2**min(failures, 3),
                                900,
                            )
                    finally:
                        queue.task_done()

            for _ in range(min(self.max_concurrency, len(jobs))):
                workers.append(asyncio.create_task(worker()))
            try:
                for job in jobs:
                    await queue.put(job)
                await queue.join()
                await save_endpoint_statuses(self.last_batch_status)
            finally:
                for task in workers:
                    task.cancel()
                await asyncio.gather(*workers, return_exceptions=True)
            if jobs and failed_urls and len(failed_urls) == len(jobs):
                raise RSSProviderError(
                    f"All RSS feeds failed ({len(failed_urls)}/{len(jobs)})",
                    failed_urls,
                )
            return articles
        finally:
            for endpoint in claimed_endpoints:
                try:
                    await asyncio.shield(release_endpoint_poll(endpoint.id))
                except Exception as exc:  # noqa: BLE001 - lease cleanup is best effort
                    logger.error("Failed to release RSS lease %s: %s", endpoint.url, exc)


class SitemapProvider:
    """Poll verified news sitemaps with bounded concurrency and validators."""

    name = "sitemap"
    min_interval = 300.0

    def __init__(self, client: httpx.AsyncClient, max_concurrency: int | None = None):
        self.client = client
        self.max_concurrency = max(
            1,
            max_concurrency or get_settings().web_source_poll_concurrency,
        )
        self.endpoint_status: dict[str, dict[str, str]] = {}
        self._url_locks: dict[str, asyncio.Lock] = {}

    async def _fetch_endpoint(self, endpoint) -> list[WebArticle]:
        endpoint_ids = {endpoint.url: endpoint.id}

        async def fetch_document(url: str):
            lock = self._url_locks.setdefault(url, asyncio.Lock())
            async with lock:
                cached_endpoint = endpoint
                if url != endpoint.url:
                    cached_endpoint = await upsert_endpoint(
                        endpoint.source_id,
                        url,
                        kind="sitemap",
                        enabled=True,
                        discovered_by="sitemap_index",
                    )
                endpoint_ids[url] = cached_endpoint.id
                cached_parse = None
                try:
                    child_urls = json.loads(cached_endpoint.child_sitemap_urls or "[]")
                except (TypeError, ValueError):
                    child_urls = []
                if isinstance(child_urls, list) and child_urls:
                    cached_parse = SitemapParseResult(
                        kind="index",
                        sitemap_urls=tuple(str(value) for value in child_urls),
                    )
                headers = {}
                if cached_endpoint.etag:
                    headers["If-None-Match"] = cached_endpoint.etag
                if cached_endpoint.last_modified:
                    headers["If-Modified-Since"] = cached_endpoint.last_modified
                try:
                    document = await fetch_public_document(
                        self.client,
                        url,
                        conditional_headers=headers,
                    )
                except Exception as exc:
                    await save_endpoint_check(
                        cached_endpoint.id,
                        status="error",
                        error=str(exc),
                    )
                    raise
                unchanged = document.status_code == 304 or (
                    cached_endpoint.content_hash
                    and cached_endpoint.content_hash == document.content_hash
                )
                await save_endpoint_check(
                    cached_endpoint.id,
                    status="ok",
                    http_status=document.status_code,
                    etag=document.headers.get("etag"),
                    last_modified=document.headers.get("last-modified"),
                    content_hash=document.content_hash or cached_endpoint.content_hash,
                )
                return SitemapFetchResult(
                    document=document,
                    changed=not unchanged,
                    cached_parse=cached_parse,
                )

        tree = await crawl_sitemap_tree(
            self.client,
            endpoint.url,
            fetcher=fetch_document,
        )
        for document in tree.documents:
            if not document.changed:
                continue
            parsed = document.parsed
            child_urls = (
                parsed.sitemap_urls
                if parsed is not None and parsed.kind == "index"
                else ()
            )
            endpoint_id = endpoint_ids.get(document.url)
            if endpoint_id is not None:
                await save_sitemap_children(endpoint_id, child_urls)
        domain = urlsplit(endpoint.url).hostname or "sitemap"
        articles = [
            WebArticle(
                provider=self.name,
                title=entry.title or entry.url,
                url=entry.url,
                source_name=domain,
                published_at=entry.published_at or entry.lastmod,
            )
            for entry in tree.entries
        ]
        return articles


    async def fetch(self, query: str = "") -> list[WebArticle]:
        if not get_settings().web_sitemap_enabled:
            return []
        endpoints = await claim_due_endpoints(
            "sitemap",
            limit=self.max_concurrency,
        )
        if not endpoints:
            return []
        queue: asyncio.Queue = asyncio.Queue(maxsize=max(1, self.max_concurrency * 2))
        articles: list[WebArticle] = []
        failures: list[str] = []

        async def worker() -> None:
            while True:
                endpoint = await queue.get()
                try:
                    articles.extend(await self._fetch_endpoint(endpoint))
                    self.endpoint_status[endpoint.url] = {"status": "ok", "error": ""}
                except Exception as exc:  # noqa: BLE001 - isolate one sitemap
                    failures.append(endpoint.url)
                    self.endpoint_status[endpoint.url] = {"status": "error", "error": str(exc)}
                    logger.warning("Sitemap %s failed: %s", endpoint.url, exc)
                    await save_endpoint_check(endpoint.id, status="error", error=str(exc))
                finally:
                    try:
                        await asyncio.shield(release_endpoint_poll(endpoint.id))
                    except Exception as exc:  # noqa: BLE001 - lease cleanup is best effort
                        logger.error("Failed to release sitemap lease %s: %s", endpoint.url, exc)
                    queue.task_done()

        workers = [
            asyncio.create_task(worker())
            for _ in range(min(self.max_concurrency, len(endpoints)))
        ]
        for endpoint in endpoints:
            await queue.put(endpoint)
        await queue.join()
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        if failures and len(failures) == len(endpoints):
            raise ProviderHTTPError(self.name, 0, "All sitemaps failed")
        return articles


class PublicNewsPageProvider:
    """Optional, shallow polling of explicitly approved news pages."""

    name = "news_page"
    min_interval = 900.0

    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.max_concurrency = max(1, get_settings().web_source_poll_concurrency)

    async def fetch(self, query: str = "") -> list[WebArticle]:
        if not get_settings().web_public_news_pages_enabled:
            return []
        from app.services.web_monitor.media_registry import get_enabled_endpoints

        # The registry helper is intentionally narrow; query news pages here
        # so the feature remains off unless an administrator explicitly enables it.
        endpoints = await get_enabled_endpoints("news_page")
        articles: list[WebArticle] = []
        queue: asyncio.Queue = asyncio.Queue(maxsize=max(1, self.max_concurrency * 2))

        async def worker() -> None:
            while True:
                endpoint = await queue.get()
                try:
                    document = await fetch_public_document(self.client, endpoint.url)
                    for link in extract_news_page_links(document):
                        articles.append(
                            WebArticle(
                                provider=self.name,
                                title=link.rsplit("/", 1)[-1].replace("-", " "),
                                url=link,
                                source_name=urlsplit(endpoint.url).hostname or "news page",
                                published_at=None,
                            )
                        )
                except Exception as exc:  # noqa: BLE001 - isolate one approved page
                    logger.warning("News page %s failed: %s", endpoint.url, exc)
                finally:
                    queue.task_done()

        workers = [
            asyncio.create_task(worker())
            for _ in range(min(self.max_concurrency, len(endpoints)))
        ]
        for endpoint in endpoints:
            await queue.put(endpoint)
        await queue.join()
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        return articles


def parse_rss_urls(raw: str) -> list[tuple[str, str]]:
    """Parse WEB_RSS_URLS without treating a malformed entry as a URL."""

    result: list[tuple[str, str]] = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        if "|" in item:
            source_name, url = item.split("|", 1)
            result.append((source_name.strip() or "RSS", url.strip()))
        else:
            result.append(("RSS", item))
    return result


# Compatibility-friendly names for integrations that do not care about the
# transport detail in the provider class name.
GoogleNewsProvider = GoogleNewsRSSProvider
RSSFeedProvider = RSSProvider
