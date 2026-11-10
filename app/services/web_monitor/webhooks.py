from __future__ import annotations

import hashlib
import hmac
import json
import logging
from datetime import datetime
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
    return False


def _parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(
            value[:-1] + "+00:00" if value.endswith("Z") else value
        )
        return parsed
    except ValueError:
        return None


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


async def ingest_webhook(provider: str, payload: Any) -> int:
    event_id = payload.get("event_id") or payload.get("id") if isinstance(payload, dict) else None
    if event_id:
        from app.services.web_monitor.storage import get_webhook_event, save_webhook_event

        if await get_webhook_event(provider, str(event_id)):
            return 0
    articles = parse_webhook_articles(provider, payload)
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
        payload = json.loads(body.decode("utf-8"))
        stored = await ingest_webhook(provider, payload)
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
