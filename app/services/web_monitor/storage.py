from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy import func, select

from app.config import get_settings
from app.database import session_scope
from app.models import (
    Setting,
    WebArticleRecord,
    WebDelivery,
    WebDetection,
    WebWatch,
)
from app.services.web_monitor.matcher import WebMatch, find_web_match, normalize_text
from app.services.web_monitor.providers import WebArticle

TRACKING_KEYS = {
    "fbclid",
    "gclid",
    "dclid",
    "msclkid",
    "mc_cid",
    "mc_eid",
    "_ga",
}
TITLE_DEDUP_WINDOW = timedelta(hours=6)


def normalize_url(url: str) -> str:
    """Canonicalize an article URL while retaining identity-bearing query args."""

    raw = (url or "").strip()
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    hostname = (parts.hostname or "").lower()
    if not scheme or not hostname:
        return raw
    try:
        port = parts.port
    except ValueError:
        port = None
    host = hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        host = f"{host}:{port}"
    query_items = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in TRACKING_KEYS
    ]
    # Ordering query parameters makes equivalent links from different feeds
    # deduplicate, while values themselves are preserved.
    query_items.sort()
    path = parts.path or "/"
    if len(path) > 1:
        path = path.rstrip("/")
    return urlunsplit((scheme, host, path, urlencode(query_items), ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _normalize_article_field(value: str | None) -> str:
    return normalize_text(value).strip(" -–—|·")


def _normalize_source(value: str | None) -> str:
    source = _normalize_article_field(value)
    source = re.sub(r"^https?://", "", source)
    return source.removeprefix("www.").rstrip("/")


@dataclass(frozen=True)
class IngestResult:
    article_id: int
    detection_ids: tuple[int, ...]
    delivery_ids: tuple[int, ...]
    created_article: bool


async def get_watches(enabled_only: bool = True) -> list[WebWatch]:
    async with session_scope() as db:
        query = select(WebWatch).order_by(WebWatch.id)
        if enabled_only:
            query = query.where(WebWatch.enabled.is_(True))
        result = await db.execute(query)
        watches = list(result.scalars().all())
        for watch in watches:
            db.expunge(watch)
        return watches


async def get_watch(watch_id: int) -> WebWatch | None:
    async with session_scope() as db:
        watch = await db.get(WebWatch, watch_id)
        if watch is not None:
            db.expunge(watch)
        return watch


async def create_watch(query: str, clarification: str | None = None) -> WebWatch:
    normalized = normalize_text(query)
    clarification_value = normalize_text(clarification) or None
    async with session_scope() as db:
        watch = WebWatch(query=normalized, clarification=clarification_value, enabled=True)
        db.add(watch)
        await db.commit()
        await db.refresh(watch)
        db.expunge(watch)
        return watch


async def find_watch_by_query(query: str, clarification: str | None = None) -> WebWatch | None:
    normalized = normalize_text(query)
    clarification_value = normalize_text(clarification) or None
    async with session_scope() as db:
        result = await db.execute(
            select(WebWatch).where(WebWatch.query == normalized).where(
                WebWatch.clarification == clarification_value
            )
        )
        watch = result.scalar_one_or_none()
        if watch is not None:
            db.expunge(watch)
        return watch


async def upsert_article(article: WebArticle) -> tuple[WebArticleRecord, bool]:
    canonical = normalize_url(article.url)
    parsed = urlsplit(canonical)
    if not canonical or parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Article URL is empty")
    digest = url_hash(canonical)
    async with session_scope() as db:
        result = await db.execute(select(WebArticleRecord).where(WebArticleRecord.url_hash == digest))
        record = result.scalar_one_or_none()
        if record is None and article.published_at is not None:
            published_at = _utc(article.published_at)
            lower_bound = published_at - TITLE_DEDUP_WINDOW
            upper_bound = published_at + TITLE_DEDUP_WINDOW
            candidates_result = await db.execute(
                select(WebArticleRecord).where(
                    WebArticleRecord.published_at >= lower_bound,
                    WebArticleRecord.published_at <= upper_bound,
                )
            )
            normalized_title = _normalize_article_field(article.title)
            normalized_source = _normalize_source(article.source_name)
            for candidate in candidates_result.scalars().all():
                if (
                    normalized_title
                    and normalized_title == _normalize_article_field(candidate.title)
                    and normalized_source
                    and normalized_source == _normalize_source(candidate.source_name)
                ):
                    record = candidate
                    break
        created = record is None
        if record is None:
            record = WebArticleRecord(
                provider=article.provider,
                title=(article.title or "").strip(),
                url=article.url.strip(),
                canonical_url=canonical,
                url_hash=digest,
                source_name=(article.source_name or "").strip(),
                description=(article.description or "").strip(),
                published_at=_utc(article.published_at),
            )
            db.add(record)
        else:
            # Keep the first-seen timestamp and canonical identity.  Feeds may
            # enrich an item later, so fill missing metadata without replacing
            # the source that first discovered it.
            if not record.title and article.title:
                record.title = article.title.strip()
            if not record.description and article.description:
                record.description = article.description.strip()
            if not record.source_name and article.source_name:
                record.source_name = article.source_name.strip()
            if record.published_at is None:
                record.published_at = _utc(article.published_at)
        await db.commit()
        await db.refresh(record)
        db.expunge(record)
        return record, created


async def ingest_article(
    article: WebArticle,
    watches: Sequence[WebWatch] | None = None,
    *,
    notify_watch_ids: Iterable[int] = (),
    admin_id: int | None = None,
) -> IngestResult:
    """Persist an article, detections and (optionally) durable deliveries."""

    if watches is None:
        watches = await get_watches()
    notify_ids = set(notify_watch_ids)
    matched_watches: list[tuple[WebWatch, WebMatch]] = []
    for watch in watches:
        if not watch.enabled:
            continue
        match = find_web_match(
            f"{article.title}\n{article.description}",
            watch.query,
            watch.clarification,
        )
        if match is not None:
            matched_watches.append((watch, match))
    if not matched_watches:
        return IngestResult(0, (), (), False)
    record, created_article = await upsert_article(article)
    detection_ids: list[int] = []
    delivery_ids: list[int] = []
    admin_id = get_settings().admin_id if admin_id is None else admin_id
    first_seen_at = _utc(record.first_seen_at) or datetime.now(UTC)
    observation_window = timedelta(
        seconds=max(60, get_settings().web_monitor_interval * 2)
    )
    is_recent_article = created_article or (
        datetime.now(UTC) - observation_window <= first_seen_at <= datetime.now(UTC)
    )

    async with session_scope() as db:
        for watch, match in matched_watches:
            result = await db.execute(
                select(WebDetection).where(
                    WebDetection.article_id == record.id,
                    WebDetection.watch_id == watch.id,
                )
            )
            detection = result.scalar_one_or_none()
            is_new_detection = detection is None
            if is_new_detection:
                detection = WebDetection(
                    article_id=record.id,
                    watch_id=watch.id,
                    matched_text=match.matched_text,
                )
                db.add(detection)
                await db.flush()
            detection_ids.append(detection.id)
            # A delivery belongs to the first detection, not to every later
            # observation of the same detection.  This also prevents a
            # restart or enabling notifications from replaying old articles.
            if is_new_detection and is_recent_article and watch.id in notify_ids and admin_id:
                delivery_result = await db.execute(
                    select(WebDelivery).where(
                        WebDelivery.detection_id == detection.id,
                        WebDelivery.admin_id == admin_id,
                    )
                )
                delivery = delivery_result.scalar_one_or_none()
                if delivery is None:
                    delivery = WebDelivery(
                        detection_id=detection.id,
                        admin_id=admin_id,
                        status="pending",
                    )
                    db.add(delivery)
                    await db.flush()
                delivery_ids.append(delivery.id)
        await db.commit()
    return IngestResult(record.id, tuple(detection_ids), tuple(delivery_ids), created_article)


async def set_provider_initialized(watch_id: int, provider: str) -> None:
    await _set_state(f"web_initialized:{provider}:{watch_id}", "true")


async def provider_initialized(watch_id: int, provider: str) -> bool:
    return (await _get_state(f"web_initialized:{provider}:{watch_id}")) == "true"


async def set_provider_last_success(provider: str, value: datetime | None = None) -> None:
    value = value or datetime.now(UTC)
    await _set_state(f"web_last_success:{provider}", value.isoformat())


async def get_provider_last_success(provider: str) -> datetime | None:
    raw = await _get_state(f"web_last_success:{provider}")
    if not raw:
        return None
    try:
        return _utc(datetime.fromisoformat(raw))
    except ValueError:
        return None


async def _get_state(key: str) -> str | None:
    async with session_scope() as db:
        row = await db.get(Setting, key)
        return row.value if row else None


async def _set_state(key: str, value: str) -> None:
    async with session_scope() as db:
        row = await db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value=value))
        else:
            row.value = value
            row.updated_at = datetime.now(UTC)
        await db.commit()


def _webhook_event_key(provider: str, event_id: str) -> str:
    digest = hashlib.sha256(event_id.encode("utf-8")).hexdigest()
    return f"web_event:{provider}:{digest}"


async def get_webhook_event(provider: str, event_id: str) -> bool:
    return await _get_state(_webhook_event_key(provider, event_id)) == "true"


async def save_webhook_event(provider: str, event_id: str) -> None:
    await _set_state(_webhook_event_key(provider, event_id), "true")


async def get_web_counts() -> tuple[int, int, datetime | None]:
    async with session_scope() as db:
        watches = await db.scalar(select(func.count(WebWatch.id))) or 0
        articles = await db.scalar(select(func.count(WebArticleRecord.id))) or 0
        last = await db.scalar(select(func.max(WebArticleRecord.first_seen_at)))
    return int(watches), int(articles), _utc(last)


async def get_recent_articles(limit: int = 20) -> list[WebArticleRecord]:
    async with session_scope() as db:
        result = await db.execute(
            select(WebArticleRecord)
            .order_by(WebArticleRecord.first_seen_at.desc())
            .limit(max(1, limit))
        )
        records = list(result.scalars().all())
        for record in records:
            db.expunge(record)
        return records


async def recover_web_deliveries() -> list[int]:
    now = datetime.now(UTC)
    stale_before = now.timestamp() - 300
    ids: list[int] = []
    async with session_scope() as db:
        result = await db.execute(select(WebDelivery).where(WebDelivery.status == "processing"))
        for delivery in result.scalars().all():
            started = _utc(delivery.processing_started_at)
            if started is None or started.timestamp() < stale_before:
                delivery.status = "pending"
                delivery.processing_started_at = None
                if delivery.next_retry_at is None:
                    delivery.next_retry_at = now
        await db.commit()
        result = await db.execute(
            select(WebDelivery).where(WebDelivery.status.in_(["pending", "processing"]))
        )
        for delivery in result.scalars().all():
            if delivery.attempts < get_settings().max_attempts and (
                delivery.next_retry_at is None or _utc(delivery.next_retry_at) <= now
            ):
                ids.append(delivery.id)
    return ids


async def get_delivery_context(delivery_id: int):
    async with session_scope() as db:
        result = await db.execute(
            select(WebDelivery, WebDetection, WebArticleRecord, WebWatch)
            .join(WebDetection, WebDetection.id == WebDelivery.detection_id)
            .join(WebArticleRecord, WebArticleRecord.id == WebDetection.article_id)
            .join(WebWatch, WebWatch.id == WebDetection.watch_id)
            .where(WebDelivery.id == delivery_id)
        )
        row = result.first()
        if row is None:
            return None
        return tuple(row)


async def get_article_watch_context(article_id: int, admin_id: int):
    """Return all watches and deliveries for grouped article notification."""

    async with session_scope() as db:
        article = await db.get(WebArticleRecord, article_id)
        if article is None:
            return None
        result = await db.execute(
            select(WebDetection, WebWatch, WebDelivery)
            .join(WebWatch, WebWatch.id == WebDetection.watch_id)
            .outerjoin(
                WebDelivery,
                (WebDelivery.detection_id == WebDetection.id)
                & (WebDelivery.admin_id == admin_id),
            )
            .where(WebDetection.article_id == article_id)
        )
        rows = list(result.all())
        return article, rows


canonicalize_url = normalize_url
compute_url_hash = url_hash
