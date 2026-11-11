from __future__ import annotations

import email.utils
import hashlib
import hmac
import json
import logging
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from aiohttp import web

from app.config import get_settings
from app.services.monitor import notifications_enabled
from app.services.web_monitor.providers import WebArticle
from app.services.web_monitor.storage import get_watches, ingest_article

logger = logging.getLogger(__name__)
_runner: web.AppRunner | None = None


def configured_webhook_secrets() -> dict[str, str]:
    result: dict[str, str] = {}
    for item in (get_settings().web_webhook_secrets or "").split(","):
        if "=" not in item:
            continue
        provider, secret = item.split("=", 1)
        if provider.strip() and secret.strip():
            result[provider.strip()] = secret.strip()
    return result


def _authorized(provider: str, body: bytes, headers) -> bool:
    secret = configured_webhook_secrets().get(provider)
    if not secret:
        return False
    token = headers.get("X-Webhook-Token") or headers.get("Authorization", "")
    if token.lower().startswith("bearer "):
        token = token[7:]
    if token and hmac.compare_digest(token, secret):
        return True
    for header in ("X-Hub-Signature-256", "X-Webhook-Signature"):
        signature = headers.get(header, "")
        signature = signature.removeprefix("sha256=")
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if signature and hmac.compare_digest(signature, expected):
            return True
    signature = headers.get("X-Hub-Signature", "").removeprefix("sha1=")
    expected_sha1 = hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()
    return bool(signature) and hmac.compare_digest(signature, expected_sha1)


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value
        )
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    except ValueError:
        try:
            parsed = email.utils.parsedate_to_datetime(value)
            return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
        except (TypeError, ValueError, OverflowError):
            return None


def _xml_name(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1].casefold()


def _xml_child(element: ET.Element, *names: str) -> ET.Element | None:
    wanted = {name.casefold() for name in names}
    return next((child for child in element if _xml_name(child) in wanted), None)


def _xml_text(element: ET.Element | None) -> str:
    return " ".join("".join(element.itertext()).split()) if element is not None else ""


def _xml_link(element: ET.Element) -> str:
    link = _xml_child(element, "link")
    if link is None:
        return ""
    href = str(link.attrib.get("href") or "").strip()
    return href or _xml_text(link)


def parse_websub_xml(provider: str, body: bytes | str) -> list[WebArticle]:
    """Parse RSS 2.0 or Atom 1.0 pushed by a WebSub publisher."""

    raw = body.encode("utf-8") if isinstance(body, str) else body
    upper = raw.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        raise ValueError("DTD and entities are not accepted")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError("Invalid RSS/Atom XML") from exc

    root_name = _xml_name(root)
    if root_name == "rss":
        channel = _xml_child(root, "channel")
        container = channel if channel is not None else root
        entries = [child for child in container if _xml_name(child) == "item"]
        atom = False
    elif root_name == "feed":
        container = root
        entries = [child for child in container if _xml_name(child) == "entry"]
        atom = True
    else:
        raise ValueError("Expected RSS or Atom feed")

    articles: list[WebArticle] = []
    for entry in entries:
        title = _xml_text(_xml_child(entry, "title"))
        link = _xml_link(entry)
        if atom:
            description = _xml_text(_xml_child(entry, "summary", "content"))
            published = _xml_text(_xml_child(entry, "published", "updated"))
            source_element = _xml_child(entry, "source")
            source_name = _xml_text(_xml_child(source_element, "title")) if source_element is not None else provider
        else:
            description = _xml_text(_xml_child(entry, "description", "summary", "content"))
            published = _xml_text(_xml_child(entry, "pubDate", "published", "updated"))
            source_name = _xml_text(_xml_child(entry, "source")) or provider
        if not title or not link:
            continue
        articles.append(
            WebArticle(
                provider=provider,
                title=title,
                url=link,
                source_name=source_name or provider,
                published_at=_parse_datetime(published),
                description=description,
            )
        )
    if not articles:
        raise ValueError("RSS/Atom feed contains no valid publications")
    return articles


def parse_webhook_articles(provider: str, payload: Any) -> list[WebArticle]:
    if isinstance(payload, dict) and isinstance(payload.get("articles"), list):
        items = payload["articles"]
    elif isinstance(payload, list):
        items = payload
    else:
        items = [payload]
    articles: list[WebArticle] = []
    for item in items:
        if not isinstance(item, dict):
            raise TypeError("Each webhook article must be an object")
        url = str(item.get("url") or item.get("link") or "").strip()
        title = str(item.get("title") or "").strip()
        parsed_url = urlsplit(url)
        if (
            not url
            or parsed_url.scheme not in {"http", "https"}
            or not parsed_url.netloc
            or not title
        ):
            raise ValueError("Webhook article requires title and url")
        articles.append(
            WebArticle(
                provider=provider,
                title=title,
                url=url,
                source_name=str(item.get("source_name") or item.get("source") or provider).strip(),
                published_at=_parse_datetime(item.get("published_at") or item.get("published")),
                description=str(item.get("description") or item.get("summary") or "").strip(),
            )
        )
    return articles


def parse_webhook_body(provider: str, body: bytes, content_type: str = "") -> tuple[Any, list[WebArticle]]:
    """Decode a signed JSON webhook or signed RSS/Atom WebSub delivery."""

    if "xml" in content_type.casefold() or body.lstrip().startswith(b"<"):
        return None, parse_websub_xml(provider, body)
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid JSON webhook body") from exc
    return payload, parse_webhook_articles(provider, payload)


async def ingest_webhook(provider: str, payload: Any, event_id: str | None = None) -> int:
    if isinstance(payload, (bytes, bytearray, str)):
        raw = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
        decoded_payload, articles = parse_webhook_body(provider, raw)
    else:
        decoded_payload = payload
        articles = parse_webhook_articles(provider, payload)
    event_id = event_id or (
        decoded_payload.get("event_id") or decoded_payload.get("id")
        if isinstance(decoded_payload, dict)
        else None
    )
    if event_id:
        from app.services.web_monitor.storage import get_webhook_event, save_webhook_event

        if await get_webhook_event(provider, str(event_id)):
            return 0
    watches = await get_watches()
    notify_ids = {watch.id for watch in watches} if await notifications_enabled() else set()
    stored = 0
    for article in articles:
        result = await ingest_article(article, watches, notify_watch_ids=notify_ids)
        from app.services.web_monitor.delivery import enqueue_delivery

        for delivery_id in result.delivery_ids:
            await enqueue_delivery(delivery_id)
        stored += 1 if result.article_id else 0
    if event_id:
        await save_webhook_event(provider, str(event_id))
    return stored


async def webhook_handler(request: web.Request) -> web.StreamResponse:
    settings = get_settings()
    provider = request.match_info["provider"]
    if not settings.web_webhook_enabled:
        return web.json_response({"error": "webhooks disabled"}, status=404)
    if request.content_length is not None and request.content_length > settings.web_webhook_max_body:
        return web.json_response({"error": "request too large"}, status=413)
    body = await request.content.read(settings.web_webhook_max_body + 1)
    if len(body) > settings.web_webhook_max_body:
        return web.json_response({"error": "request too large"}, status=413)
    if not _authorized(provider, body, request.headers):
        return web.json_response({"error": "unauthorized"}, status=401)
    try:
        if "xml" in request.content_type.casefold() or body.lstrip().startswith(b"<"):
            stored = await ingest_webhook(
                provider,
                body,
                event_id=request.headers.get("X-Event-ID"),
            )
        else:
            payload = json.loads(body.decode("utf-8"))
            stored = await ingest_webhook(
                provider,
                payload,
                event_id=request.headers.get("X-Event-ID"),
            )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
        return web.json_response({"error": str(exc)}, status=400)
    except Exception:
        logger.exception("Webhook ingestion failed for %s", provider)
        return web.json_response({"error": "internal error"}, status=500)
    return web.json_response({"accepted": stored})


async def websub_confirmation(request: web.Request) -> web.StreamResponse:
    provider = request.match_info["provider"]
    mode = request.query.get("hub.mode")
    challenge = request.query.get("hub.challenge")
    token = request.query.get("hub.verify_token", "")
    secret = configured_webhook_secrets().get(provider)
    if not secret or mode not in {"subscribe", "unsubscribe"} or not challenge:
        return web.Response(status=403, text="forbidden")
    if not hmac.compare_digest(token, secret):
        return web.Response(status=403, text="forbidden")
    return web.Response(text=challenge, content_type="text/plain")


def create_webhook_app() -> web.Application:
    app = web.Application(client_max_size=get_settings().web_webhook_max_body)
    app.router.add_get("/webhooks/news/{provider}", websub_confirmation)
    app.router.add_post("/webhooks/news/{provider}", webhook_handler)
    return app


async def start_webhook_server() -> web.AppRunner | None:
    global _runner
    settings = get_settings()
    if not settings.web_webhook_enabled:
        return None
    if _runner is not None:
        return _runner
    app = create_webhook_app()
    _runner = web.AppRunner(app)
    await _runner.setup()
    site = web.TCPSite(_runner, settings.web_webhook_host, settings.web_webhook_port)
    await site.start()
    logger.info("Webhook server listening on %s:%s", settings.web_webhook_host, settings.web_webhook_port)
    return _runner


async def stop_webhook_server() -> None:
    global _runner
    if _runner is not None:
        await _runner.cleanup()
        _runner = None
