from __future__ import annotations

import asyncio
import ipaddress
import socket
import time
from html.parser import HTMLParser
from typing import ClassVar
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpcore
import httpx
from httpcore._backends.auto import AutoBackend
from httpcore._backends.base import AsyncNetworkBackend

from app.config import get_settings

try:  # pragma: no cover - exercised when the optional runtime dependency is installed
    import trafilatura
except ImportError:  # pragma: no cover - fallback is covered in unit tests
    trafilatura = None


USER_AGENT = "intens_bot-web-monitor/1.0 (+public-article-monitor)"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
ROBOTS_CACHE_TTL = 3600.0
DOMAIN_MIN_INTERVAL = 0.2
_robots_cache: dict[str, tuple[float, bool]] = {}
_domain_rate_locks: dict[str, asyncio.Lock] = {}
_domain_last_request: dict[str, float] = {}


def _is_public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_global


async def _resolve_public(hostname: str, port: int) -> bool:
    return await _resolve_public_ip(hostname, port) is not None


async def _resolve_public_ip(hostname: str | bytes, port: int) -> str | None:
    """Resolve once and return the exact public address to connect to."""

    if isinstance(hostname, bytes):
        hostname = hostname.decode("ascii", errors="ignore")
    if hostname.casefold() in {
        "localhost",
        "localhost.localdomain",
    } or hostname.casefold().endswith(".localhost"):
        return None
    try:
        addresses = await asyncio.to_thread(
            socket.getaddrinfo,
            hostname,
            port,
            type=socket.SOCK_STREAM,
        )
    except (OSError, socket.gaierror):
        return None
    resolved = {item[4][0] for item in addresses if item[4]}
    if not resolved or not all(_is_public_ip(address) for address in resolved):
        return None
    return min(resolved)


async def _validate_public_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        hostname = parts.hostname
        if parts.scheme.casefold() not in {"http", "https"} or not hostname:
            return False
        if parts.username is not None or parts.password is not None or parts.fragment:
            return False
        try:
            port = parts.port or (443 if parts.scheme.casefold() == "https" else 80)
        except ValueError:
            return False
        if _is_public_ip(hostname):
            return True
        return await _resolve_public(hostname, port)
    except ValueError:
        return False


class _VisibleTextParser(HTMLParser):
    _ignored_tags: ClassVar[set[str]] = {
        "aside",
        "footer",
        "form",
        "header",
        "nav",
        "script",
        "style",
        "template",
    }
    _break_tags: ClassVar[set[str]] = {
        "article",
        "br",
        "div",
        "h1",
        "h2",
        "h3",
        "h4",
        "li",
        "main",
        "p",
        "section",
    }

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._ignored_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.casefold()
        if tag in self._ignored_tags:
            self._ignored_depth += 1
        if self._ignored_depth == 0 and tag in self._break_tags:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.casefold()
        if self._ignored_depth and tag in self._ignored_tags:
            self._ignored_depth -= 1
        if self._ignored_depth == 0 and tag in self._break_tags:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._ignored_depth == 0:
            self.parts.append(data)


def _fallback_extract(html: str) -> str | None:
    parser = _VisibleTextParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed HTML should not stop monitoring
        return None
    text = " ".join(" ".join(parser.parts).split())
    return text or None


def _extract_html(html: str) -> str | None:
    if trafilatura is not None:
        try:
            text = trafilatura.extract(
                html,
                include_comments=False,
                include_tables=False,
                favor_precision=True,
                output_format="txt",
            )
        except Exception:  # noqa: BLE001 - extractor failure has a safe fallback
            text = None
        if text:
            return " ".join(text.split())
    return _fallback_extract(html)


class _PinnedPublicIPBackend(AsyncNetworkBackend):
    """Connect to the address resolved immediately before opening the socket."""

    def __init__(self) -> None:
        self._backend = AutoBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options=None,
    ):
        address = await _resolve_public_ip(host, port)
        if address is None:
            raise OSError("Target host did not resolve to a public IP address")
        # httpcore receives the original host separately for TLS SNI and the
        # HTTP Host header; only the TCP destination is replaced with the
        # address just validated.  This closes the DNS-rebinding TOCTOU gap.
        return await self._backend.connect_tcp(
            address,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(self, path: str, timeout: float | None = None, socket_options=None):
        raise OSError("Unix sockets are not allowed for article extraction")

    async def sleep(self, seconds: float) -> None:
        await self._backend.sleep(seconds)


class PinnedPublicIPTransport(httpx.AsyncHTTPTransport):
    """HTTP transport with no environment proxy and IP-pinned connections."""

    def __init__(self, *, limits: httpx.Limits | None = None) -> None:
        from httpx._config import create_ssl_context

        limits = limits or httpx.Limits(max_connections=3, max_keepalive_connections=3)
        ssl_context = create_ssl_context(verify=True, cert=None, trust_env=False)
        super().__init__(
            verify=ssl_context,
            trust_env=False,
            proxy=None,
            limits=limits,
        )
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl_context,
            max_connections=limits.max_connections,
            max_keepalive_connections=limits.max_keepalive_connections,
            keepalive_expiry=limits.keepalive_expiry,
            http1=True,
            http2=False,
            network_backend=_PinnedPublicIPBackend(),
        )


async def _read_response(response: httpx.Response) -> bytes | None:
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > MAX_RESPONSE_BYTES:
                return None
        except ValueError:
            return None
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.aiter_bytes():
        size += len(chunk)
        if size > MAX_RESPONSE_BYTES:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


async def _fetch_robots(client: httpx.AsyncClient, article_url: str, timeout: float) -> bool:
    parts = urlsplit(article_url)
    origin = f"{parts.scheme.casefold()}://{parts.netloc.casefold()}"
    cached = _robots_cache.get(origin)
    now = time.monotonic()
    if cached and now - cached[0] < ROBOTS_CACHE_TTL:
        return cached[1]
    robots_url = f"{origin}/robots.txt"
    if not await _validate_public_url(robots_url):
        _robots_cache[origin] = (now, False)
        return False
    try:
        async with client.stream(
            "GET",
            robots_url,
            headers={"User-Agent": USER_AGENT, "Accept": "text/plain"},
            follow_redirects=False,
            timeout=timeout,
        ) as response:
            if response.status_code == 404:
                allowed = True
            elif response.status_code != 200:
                allowed = False
            else:
                content = await _read_response(response)
                if content is None:
                    allowed = False
                else:
                    encoding = response.encoding or "utf-8"
                    parser = RobotFileParser()
                    parser.parse(content.decode(encoding, errors="replace").splitlines())
                    allowed = parser.can_fetch(USER_AGENT, article_url)
    except (httpx.HTTPError, UnicodeError, OSError):
        allowed = False
    _robots_cache[origin] = (now, allowed)
    return allowed


async def _respect_domain_rate(url: str) -> None:
    parts = urlsplit(url)
    domain = parts.netloc.casefold()
    lock = _domain_rate_locks.setdefault(domain, asyncio.Lock())
    async with lock:
        now = time.monotonic()
        last_request = _domain_last_request.get(domain)
        if last_request is not None:
            await asyncio.sleep(max(0.0, DOMAIN_MIN_INTERVAL - (now - last_request)))
        _domain_last_request[domain] = time.monotonic()


async def extract_article(client: httpx.AsyncClient, url: str) -> str | None:
    """Extract public article text without bypassing site access controls."""

    transport = getattr(client, "_transport", None)
    if not isinstance(transport, (PinnedPublicIPTransport, httpx.MockTransport)):
        # A normal AsyncClient may resolve through a proxy or perform its own
        # DNS lookup after validation.  Refuse it instead of weakening the
        # connection-level SSRF guarantee.
        return None
    settings = get_settings()
    timeout = max(1.0, float(settings.web_article_timeout))
    current_url = url.strip()
    previous_scheme = ""
    try:
        for _ in range(MAX_REDIRECTS + 1):
            if not await _validate_public_url(current_url):
                return None
            current_parts = urlsplit(current_url)
            if previous_scheme == "https" and current_parts.scheme.casefold() != "https":
                return None
            await _respect_domain_rate(current_url)
            if not await _fetch_robots(client, current_url, timeout):
                return None
            await _respect_domain_rate(current_url)
            try:
                async with client.stream(
                    "GET",
                    current_url,
                    headers={
                        "User-Agent": USER_AGENT,
                        "Accept": "text/html,application/xhtml+xml;q=0.9",
                    },
                    follow_redirects=False,
                    timeout=timeout,
                ) as response:
                    if 300 <= response.status_code < 400:
                        location = response.headers.get("Location")
                        if not location:
                            return None
                        previous_scheme = current_parts.scheme.casefold()
                        current_url = urljoin(current_url, location)
                        continue
                    if response.status_code in {401, 403, 407, 429} or response.status_code >= 400:
                        return None
                    content_type = response.headers.get("Content-Type", "").casefold()
                    if content_type and not any(
                        value in content_type for value in ("text/html", "application/xhtml+xml")
                    ):
                        return None
                    content = await _read_response(response)
                    if content is None:
                        return None
                    encoding = response.encoding or "utf-8"
                    html = content.decode(encoding, errors="replace")
                    return _extract_html(html)
            except (httpx.HTTPError, UnicodeError, OSError):
                return None
        return None
    except Exception:  # noqa: BLE001 - public extraction is best effort
        return None
