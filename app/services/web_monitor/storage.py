from __future__ import annotations

import hashlib
import json
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
    WebArticleJob,
    WebArticleRecord,
    WebDelivery,
    WebDetection,
    WebWatch,
)
from app.services.web_monitor.matcher import (
    WebMatch,
    find_web_match,
    normalize_text,
    normalize_watch_type,
    validate_watch_query,
)
from app.services.web_monitor.providers import WebArticle
from app.services.web_monitor.text import clean_article_text

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
# A feed may publish an item shortly before a user creates a watch and deliver
# it only on the next polling cycle.  Keep this allowance independent from the
# scheduler interval so delayed indexing is not mistaken for an old article.
WATCH_START_GRACE = timedelta(minutes=5)
EXTRACTION_MAX_ATTEMPTS = 3
EXTRACTION_RETRY_DELAYS = (60, 300)
ARTICLE_JOB_MAX_ATTEMPTS = 5
ARTICLE_JOB_STALE_AFTER = timedelta(minutes=5)


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
    source = (value or "").strip()
    source = re.sub(r"^https?://", "", source, flags=re.IGNORECASE)
    source = clean_article_text(source)
    source = re.sub(r"^https?://", "", source, flags=re.IGNORECASE)
    return normalize_text(source.removeprefix("www.").rstrip("/"))


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


async def create_watch(
    query: str,
    clarification: str | None = None,
    watch_type: str = "person",
) -> WebWatch:
    normalized_type = normalize_watch_type(watch_type)
    normalized = validate_watch_query(query, normalized_type)
    clarification_value = normalize_text(clarification) or None
    async with session_scope() as db:
        watch = WebWatch(
            query=normalized,
            watch_type=normalized_type,
            clarification=clarification_value,
            enabled=True,
        )
        db.add(watch)
        await db.commit()
        await db.refresh(watch)
        db.expunge(watch)
        return watch


async def find_watch_by_query(
    query: str,
    clarification: str | None = None,
    watch_type: str = "person",
) -> WebWatch | None:
    normalized_type = normalize_watch_type(watch_type)
    normalized = validate_watch_query(query, normalized_type)
    clarification_value = normalize_text(clarification) or None
    async with session_scope() as db:
        result = await db.execute(
            select(WebWatch)
            .where(WebWatch.query == normalized)
            .where(WebWatch.watch_type == normalized_type)
            .where(WebWatch.clarification == clarification_value)
        )
        watch = result.scalar_one_or_none()
        if watch is not None:
            db.expunge(watch)
        return watch


async def upsert_article(article: WebArticle) -> tuple[WebArticleRecord, bool]:
    cleaned_title = clean_article_text(article.title)
    cleaned_description = clean_article_text(article.description)
    cleaned_full_text = clean_article_text(article.full_text)
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
            normalized_title = _normalize_article_field(cleaned_title)
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
                title=cleaned_title,
                url=article.url.strip(),
                canonical_url=canonical,
                url_hash=digest,
                source_name=(article.source_name or "").strip(),
                description=cleaned_description,
                full_text=cleaned_full_text,
                published_at=_utc(article.published_at),
                extraction_status="success" if cleaned_full_text else "pending",
            )
            db.add(record)
        else:
            # Keep the first-seen timestamp and canonical identity.  Feeds may
            # enrich an item later, so fill missing metadata without replacing
            # the source that first discovered it.
            if not record.title and cleaned_title:
                record.title = cleaned_title
            if not record.description and cleaned_description:
                record.description = cleaned_description
            if not record.source_name and article.source_name:
                record.source_name = article.source_name.strip()
            if not record.full_text and cleaned_full_text:
                record.full_text = cleaned_full_text
                record.extraction_status = "success"
                record.extraction_error = None
                record.extraction_next_attempt_at = None
            if record.published_at is None:
                record.published_at = _utc(article.published_at)
        await db.commit()
        await db.refresh(record)
        db.expunge(record)
        return record, created


async def get_article_record(article_id: int) -> WebArticleRecord | None:
    async with session_scope() as db:
        record = await db.get(WebArticleRecord, article_id)
        if record is not None:
            db.expunge(record)
        return record


def _decode_id_set(value: str | None) -> set[int]:
    try:
        decoded = json.loads(value or "[]")
    except (TypeError, ValueError):
        return set()
    if not isinstance(decoded, list):
        return set()
    return {int(item) for item in decoded if str(item).isdigit()}


def _encode_id_set(values: Iterable[int]) -> str:
    return json.dumps(sorted({int(value) for value in values}), separators=(",", ":"))


async def save_article_job(
    article_id: int,
    *,
    notify_watch_ids: Iterable[int] = (),
    watch_ids: Iterable[int] = (),
) -> int:
    """Persist or refresh the durable article-processing job."""

    notify_ids = {int(value) for value in notify_watch_ids}
    current_watch_ids = {int(value) for value in watch_ids}
    async with session_scope() as db:
        job = await db.scalar(
            select(WebArticleJob).where(WebArticleJob.article_id == article_id)
        )
        if job is None:
            job = WebArticleJob(
                article_id=article_id,
                notify_watch_ids=_encode_id_set(notify_ids),
                status="pending",
            )
            db.add(job)
        else:
            stored_notify_ids = _decode_id_set(job.notify_watch_ids)
            job.notify_watch_ids = _encode_id_set(stored_notify_ids | notify_ids)
            processed_watch_ids = _decode_id_set(getattr(job, "processed_watch_ids", "[]"))
            if (
                job.status in {"done", "failed"}
                and current_watch_ids - processed_watch_ids
            ):
                job.status = "pending"
                job.attempts = 0
                job.last_error = None
                job.next_retry_at = None
        await db.commit()
        await db.refresh(job)
        return job.id


async def claim_article_job(
    job_id: int,
) -> tuple[WebArticleRecord, set[int]] | None:
    """Atomically claim a durable job and return its article payload."""

    now = datetime.now(UTC)
    async with session_scope() as db:
        job = await db.get(WebArticleJob, job_id)
        if job is None:
            return None
        next_retry_at = _utc(job.next_retry_at)
        if job.status in {"done", "failed"} or (
            next_retry_at is not None and next_retry_at > now
        ):
            return None
        processing_started_at = _utc(job.processing_started_at)
        if (
            job.status == "processing"
            and processing_started_at is not None
            and now - processing_started_at < ARTICLE_JOB_STALE_AFTER
        ):
            return None
        if job.attempts >= ARTICLE_JOB_MAX_ATTEMPTS:
            job.status = "failed"
            await db.commit()
            return None
        record = await db.get(WebArticleRecord, job.article_id)
        if record is None:
            job.status = "failed"
            job.last_error = "Статья задания не найдена"
            await db.commit()
            return None
        job.status = "processing"
        job.attempts += 1
        job.processing_started_at = now
        job.next_retry_at = None
        job.last_error = None
        await db.commit()
        db.expunge(record)
        return record, _decode_id_set(job.notify_watch_ids)


async def get_pending_article_jobs(
    limit: int,
    exclude_job_ids: Iterable[int] = (),
) -> list[tuple[int, WebArticleRecord, set[int]]]:
    """Return durable jobs ready to enter the in-process bounded queue."""

    if limit <= 0:
        return []
    now = datetime.now(UTC)
    stale_before = now - ARTICLE_JOB_STALE_AFTER
    excluded_ids = {int(value) for value in exclude_job_ids}
    async with session_scope() as db:
        stale_query = select(WebArticleJob).where(
            WebArticleJob.status == "processing",
            WebArticleJob.processing_started_at < stale_before,
        )
        if excluded_ids:
            stale_query = stale_query.where(WebArticleJob.id.not_in(excluded_ids))
        stale_result = await db.execute(stale_query)
        for job in stale_result.scalars().all():
            job.status = "pending"
            job.processing_started_at = None
            job.next_retry_at = now
        await db.commit()
        result = await db.execute(
            select(WebArticleJob, WebArticleRecord)
            .join(WebArticleRecord, WebArticleRecord.id == WebArticleJob.article_id)
            .where(WebArticleJob.status.in_(["pending", "retry"]))
            .where(
                WebArticleJob.id.not_in(excluded_ids)
                if excluded_ids
                else WebArticleJob.id.is_not(None)
            )
            .where(
                (WebArticleJob.next_retry_at.is_(None))
                | (WebArticleJob.next_retry_at <= now)
            )
            .order_by(WebArticleJob.id)
            .limit(limit)
        )
        jobs: list[tuple[int, WebArticleRecord, set[int]]] = []
        for job, record in result.all():
            db.expunge(record)
            jobs.append((job.id, record, _decode_id_set(job.notify_watch_ids)))
        return jobs


async def complete_article_job(job_id: int, processed_watch_ids: Iterable[int]) -> None:
    async with session_scope() as db:
        job = await db.get(WebArticleJob, job_id)
        if job is None:
            return
        processed = _decode_id_set(getattr(job, "processed_watch_ids", "[]"))
        processed.update(int(value) for value in processed_watch_ids)
        job.processed_watch_ids = _encode_id_set(processed)
        job.status = "done"
        job.processing_started_at = None
        job.next_retry_at = None
        job.last_error = None
        await db.commit()


async def retry_article_job(
    job_id: int,
    error: str,
    next_retry_at: datetime | None = None,
) -> None:
    async with session_scope() as db:
        job = await db.get(WebArticleJob, job_id)
        if job is None:
            return
        job.status = "failed" if job.attempts >= ARTICLE_JOB_MAX_ATTEMPTS else "retry"
        job.processing_started_at = None
        job.next_retry_at = _utc(next_retry_at)
        job.last_error = error[:2000]
        await db.commit()


async def release_article_job(job_id: int) -> None:
    """Return an in-flight job to the durable queue during shutdown."""

    async with session_scope() as db:
        job = await db.get(WebArticleJob, job_id)
        if job is not None and job.status == "processing":
            job.status = "retry"
            job.processing_started_at = None
            job.next_retry_at = datetime.now(UTC)
            await db.commit()


async def release_article_extraction(article_id: int) -> None:
    async with session_scope() as db:
        record = await db.get(WebArticleRecord, article_id)
        if record is not None and record.extraction_status == "processing":
            record.extraction_status = "retry"
            record.extraction_next_attempt_at = datetime.now(UTC)
            record.extraction_error = "Обработка прервана при остановке мониторинга"
            await db.commit()


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
            "\n".join(
                filter(
                    None,
                    [article.title, article.description, article.full_text],
                )
            ),
            watch.query,
            watch.clarification,
            getattr(watch, "watch_type", "person") or "person",
        )
        if match is not None:
            matched_watches.append((watch, match))
    if not matched_watches:
        return IngestResult(0, (), (), False)
    record, created_article = await upsert_article(article)
    detection_ids: list[int] = []
    delivery_ids: list[int] = []
    configured_admin_ids = (
        get_settings().admin_ids if admin_id is None else (admin_id,)
    )
    admin_ids = tuple(dict.fromkeys(item for item in configured_admin_ids if item > 0))
    published_at = _utc(record.published_at)
    first_seen_at = _utc(record.first_seen_at)
    article_time = published_at or first_seen_at

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
            watch_started_at = _utc(watch.created_at)
            should_notify = (
                is_new_detection
                and watch.id in notify_ids
                and admin_ids
                and article_time is not None
                and watch_started_at is not None
                and article_time >= watch_started_at - WATCH_START_GRACE
            )
            # A delivery belongs to the first detection, not to every later
            # observation of the same detection.  This also prevents a
            # restart or enabling notifications from replaying old articles.
            if should_notify:
                for target_admin_id in admin_ids:
                    delivery_result = await db.execute(
                        select(WebDelivery).where(
                            WebDelivery.detection_id == detection.id,
                            WebDelivery.admin_id == target_admin_id,
                        )
                    )
                    delivery = delivery_result.scalar_one_or_none()
                    if delivery is None:
                        delivery = WebDelivery(
                            detection_id=detection.id,
                            admin_id=target_admin_id,
                            status="pending",
                        )
                        db.add(delivery)
                        await db.flush()
                    delivery_ids.append(delivery.id)
        await db.commit()
    return IngestResult(record.id, tuple(detection_ids), tuple(delivery_ids), created_article)


async def claim_article_extraction(article_id: int) -> bool:
    """Claim one persisted article for extraction, with bounded retries."""

    now = datetime.now(UTC)
    async with session_scope() as db:
        record = await db.get(WebArticleRecord, article_id)
        if record is None or record.full_text:
            return False
        if record.extraction_attempts >= EXTRACTION_MAX_ATTEMPTS:
            return False
        next_attempt = _utc(record.extraction_next_attempt_at)
        if next_attempt is not None and next_attempt > now:
            return False
        last_attempt = _utc(record.extraction_last_attempt_at)
        if (
            record.extraction_status == "processing"
            and last_attempt is not None
            and now - last_attempt < timedelta(minutes=5)
        ):
            return False
        record.extraction_status = "processing"
        record.extraction_attempts += 1
        record.extraction_last_attempt_at = now
        record.extraction_next_attempt_at = None
        record.extraction_error = None
        await db.commit()
        return True


async def finish_article_extraction(
    article_id: int,
    full_text: str | None,
    error: str | None = None,
) -> None:
    """Persist extraction output without holding a network operation in a DB transaction."""

    now = datetime.now(UTC)
    text = (full_text or "").strip()
    async with session_scope() as db:
        record = await db.get(WebArticleRecord, article_id)
        if record is None:
            return
        if text:
            record.full_text = text
            record.extraction_status = "success"
            record.extraction_error = None
            record.extraction_next_attempt_at = None
        else:
            attempts = int(record.extraction_attempts or 0)
            if attempts >= EXTRACTION_MAX_ATTEMPTS:
                record.extraction_status = "failed"
                record.extraction_next_attempt_at = None
            else:
                record.extraction_status = "retry"
                delay_index = min(attempts - 1, len(EXTRACTION_RETRY_DELAYS) - 1)
                delay = EXTRACTION_RETRY_DELAYS[max(0, delay_index)]
                record.extraction_next_attempt_at = now + timedelta(seconds=delay)
            record.extraction_error = (error or "Пустой текст статьи")[:2000]
        await db.commit()


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
