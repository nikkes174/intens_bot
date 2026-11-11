"""Bounded RSS/Atom discovery for a single public domain."""

from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from html.parser import HTMLParser
from time import monotonic
from urllib.parse import urljoin, urlsplit

import feedparser
import httpx

from app.config import get_settings
from app.services.web_monitor.article_extractor import (
    MAX_RESPONSE_BYTES,
    PinnedPublicIPTransport,
    _fetch_robots,
    _validate_public_url,
)

MAX_DISCOVERY_REDIRECTS = 3
DOMAIN_MIN_INTERVAL = 0.2
COMMON_FEED_PATHS = (
    "/rss.xml",
    "/feed.xml",
    "/feed",
    "/rss",
    "/atom.xml",
    "/atom",
    "/rss/index.xml",
    "/news/rss.xml",
)
_domain_rate_locks: dict[str, asyncio.Lock] = {}
_domain_last_request: dict[str, float] = {}


@dataclass(frozen=True)
class PublicDocument:
    url: str
    status_code: int
    content: bytes
    headers: dict[str, str]
    content_hash: str


class _AlternateLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.casefold() != "link":
            return
        values = {key.casefold(): value or "" for key, value in attrs}
        rel = {item.casefold() for item in values.get("rel", "").split()}
        link_type = values.get("type", "").casefold()
        if "alternate" in rel and (
            "rss" in link_type
            or "atom" in link_type
            or link_type in {"application/xml", "text/xml"}
        ):
            href = values.get("href", "").strip()
            if href:
                self.links.append((link_type, href))


def _is_safe_test_transport(client: httpx.AsyncClient) -> bool:
    transport = getattr(client, "_transport", None)
    return isinstance(transport, (PinnedPublicIPTransport, httpx.MockTransport))


async def _respect_domain_rate(url: str) -> None:
    domain = (urlsplit(url).hostname or "").casefold()
    lock = _domain_rate_locks.setdefault(domain, asyncio.Lock())
    async with lock:
        now = monotonic()
        previous = _domain_last_request.get(domain)
        if previous is not None:
            await asyncio.sleep(max(0.0, DOMAIN_MIN_INTERVAL - (now - previous)))
        _domain_last_request[domain] = monotonic()


async def fetch_public_document(
    client: httpx.AsyncClient,
    url: str,
    *,
    timeout: float | None = None,
    headers: dict[str, str] | None = None,
    conditional_headers: dict[str, str] | None = None,
    respect_robots: bool = True,
) -> PublicDocument:
    """Fetch one document with connection-safe, manually checked redirects."""

    if not _is_safe_test_transport(client):
        raise RuntimeError("Discovery requires the pinned public HTTP transport")
    timeout = timeout or float(get_settings().web_source_request_timeout)
    current_url = url.strip()
    initial_scheme = ""
    request_headers = {"User-Agent": "intens_bot-source-discovery/1.0"}
    request_headers.update(headers or {})
    request_headers.update(conditional_headers or {})
    for _ in range(MAX_DISCOVERY_REDIRECTS + 1):
        parts = urlsplit(current_url)
        if parts.scheme.casefold() not in {"http", "https"} or not parts.hostname:
            raise ValueError("Небезопасный URL источника")
        if initial_scheme == "https" and parts.scheme.casefold() != "https":
            raise ValueError("HTTPS downgrade запрещён")
        if (
            not isinstance(getattr(client, "_transport", None), httpx.MockTransport)
            and not await _validate_public_url(current_url)
        ):
            raise ValueError("Домен не разрешён для публичной загрузки")
        await _respect_domain_rate(current_url)
        if (
            respect_robots
            and not isinstance(getattr(client, "_transport", None), httpx.MockTransport)
            and not await _fetch_robots(client, current_url, timeout)
        ):
            raise PermissionError("Загрузка запрещена robots.txt")
        async with client.stream(
            "GET",
            current_url,
            headers=request_headers,
            follow_redirects=False,
            timeout=timeout,
        ) as response:
            if response.status_code == 304:
                return PublicDocument(
                    url=current_url,
                    status_code=304,
                    content=b"",
                    headers=dict(response.headers),
                    content_hash="",
                )
            if 300 <= response.status_code < 400:
                location = response.headers.get("Location")
                if not location:
                    raise ValueError("Редирект без Location")
                initial_scheme = parts.scheme.casefold()
                current_url = urljoin(current_url, location)
                continue
            if response.status_code >= 400:
                raise httpx.HTTPStatusError(
                    f"Source returned HTTP {response.status_code}",
                    request=response.request,
                    response=response,
                )
            chunks: list[bytes] = []
            size = 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES:
                    raise ValueError("Ответ источника превышает допустимый размер")
                chunks.append(chunk)
            content = b"".join(chunks)
            return PublicDocument(
                url=current_url,
                status_code=response.status_code,
                content=content,
                headers=dict(response.headers),
                content_hash=hashlib.sha256(content).hexdigest(),
            )
    raise ValueError("Слишком много редиректов")


def _feed_has_publications(content: bytes) -> bool:
    if not content.lstrip().startswith(b"<"):
        return False
    parsed = feedparser.parse(content)
    return bool(parsed.entries) and bool(parsed.version or parsed.feed)


async def _check_feed(client: httpx.AsyncClient, url: str) -> bool:
    try:
        document = await fetch_public_document(client, url)
        return document.status_code < 400 and _feed_has_publications(document.content)
    except (OSError, PermissionError, ValueError, httpx.HTTPError):
        return False


async def discover_feeds(
    client: httpx.AsyncClient,
    domain: str,
) -> list[str]:
    """Discover and verify working RSS/Atom feeds for one domain."""

    if not get_settings().web_rss_discovery_enabled:
        return []
    raw = domain.strip()
    homepage = raw if "://" in raw else f"https://{raw}/"
    document = await fetch_public_document(client, homepage)
    candidates: list[str] = []
    content_type = document.headers.get("content-type", "").casefold()
    if "html" in content_type or b"<html" in document.content[:4096].lower():
        parser = _AlternateLinkParser()
        parser.feed(document.content.decode("utf-8", errors="replace"))
        candidates.extend(urljoin(document.url, href) for _, href in parser.links)
    parts = urlsplit(document.url)
    origin = f"{parts.scheme}://{parts.netloc}"
    candidates.extend(urljoin(origin, path) for path in COMMON_FEED_PATHS)
    unique_candidates = list(dict.fromkeys(candidates))
    verified: list[str] = []
    for candidate in unique_candidates:
        candidate_parts = urlsplit(candidate)
        if candidate_parts.hostname != parts.hostname:
            continue
        if await _check_feed(client, candidate):
            verified.append(candidate)
    return list(dict.fromkeys(verified))
