from __future__ import annotations

import calendar
import email.utils
import logging
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import quote_plus

import feedparser
import httpx

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WebArticle:
    provider: str
    title: str
    url: str
    source_name: str
    published_at: datetime | None
    description: str = ""


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

    async def fetch(self, query: str = "") -> list[WebArticle]:
        articles: list[WebArticle] = []
        failed_urls: list[str] = []
        for source_name, url in self.feeds:
            try:
                response = await _request(self.client, self.name, url)
                parsed = feedparser.parse(response.content)
                if getattr(parsed, "bozo", False) and not parsed.entries:
                    raise ValueError("invalid RSS/Atom XML")
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
            except Exception as exc:  # noqa: BLE001 - one broken feed must not stop the batch
                failed_urls.append(url)
                logger.warning("RSS feed %s failed: %s", url, exc)
                continue
        if self.feeds and failed_urls and len(failed_urls) == len(self.feeds):
            raise RSSProviderError(
                f"All RSS feeds failed ({len(failed_urls)}/{len(self.feeds)})",
                failed_urls,
            )
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
