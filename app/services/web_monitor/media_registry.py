"""Persistent, unbounded registry of internet information sources.

The registry is intentionally separate from the legacy Russian-media settings.
That keeps old administrative controls working while allowing discovered and
imported sources to have multiple independent feeds and sitemaps.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import func, select

from app.config import get_settings
from app.database import session_scope
from app.models import (
    Setting,
    WebArticleRecord,
    WebDelivery,
    WebDetection,
    WebMediaCandidate,
    WebMediaEndpoint,
    WebMediaSource,
    WebMediaSourceCheck,
)

# Public name used by registry integrations; the legacy module keeps its
# immutable MediaSource definition for backwards compatibility.
MediaSource = WebMediaSource

SOURCE_CATEGORIES = {
    "federal": "Федеральные",
    "regional": "Региональные",
    "local": "Городские и районные",
    "business": "Бизнес",
    "technology": "Технологии",
    "industry": "Отраслевые",
    "press": "Пресс-центры",
    "other": "Другие",
}

SOURCE_PRIORITY = {
    "federal": 0,
    "business": 1,
    "technology": 2,
    "industry": 3,
    "regional": 4,
    "local": 5,
    "press": 6,
    "other": 7,
}


def normalize_domain(value: str) -> str:
    """Return a stable host-only domain for uniqueness and lookups."""

    raw = (value or "").strip()
    if not raw:
        raise ValueError("Домен не указан")
    candidate = raw if "://" in raw else f"https://{raw}"
    parts = urlsplit(candidate)
    hostname = (parts.hostname or "").casefold().rstrip(".")
    if not hostname or any(char.isspace() for char in hostname):
        raise ValueError("Некорректный домен")
    try:
        hostname = hostname.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise ValueError("Некорректный домен") from exc
    hostname = hostname.removeprefix("www.")
    if "." not in hostname and hostname not in {"localhost"}:
        raise ValueError("Нужен публичный домен")
    return hostname


def _decode_methods(value: str | None) -> list[str]:
    try:
        decoded = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(decoded, list):
        return []
    return sorted({str(item) for item in decoded if str(item).strip()})


def _encode_methods(values: Iterable[str]) -> str:
    return json.dumps(sorted({str(value) for value in values if str(value).strip()}))


def _source_priority(source: WebMediaSource) -> int:
    return SOURCE_PRIORITY.get(source.category, SOURCE_PRIORITY["other"])


def _validate_category(category: str | None) -> str:
    value = (category or "other").strip().casefold()
    if value not in SOURCE_CATEGORIES:
        return "other"
    return value


async def _upsert_source(
    db,
    *,
    name: str,
    domain: str,
    region: str | None = None,
    category: str = "other",
    enabled: bool = False,
    status: str = "unknown",
    rss_url: str | None = None,
    discovery_methods: Iterable[str] = (),
    origin: str = "catalog",
    managed_by_user: bool = False,
    poll_interval_seconds: int | None = None,
) -> WebMediaSource:
    normalized_domain = normalize_domain(domain)
    source = await db.scalar(
        select(WebMediaSource).where(WebMediaSource.domain == normalized_domain)
    )
    if source is None:
        source = WebMediaSource(
            name=(name or normalized_domain).strip()[:255],
            domain=normalized_domain,
            region=(region or None),
            category=_validate_category(category),
            enabled=enabled,
            status=status,
            rss_url=rss_url,
            discovery_methods=_encode_methods(discovery_methods),
            origin=origin,
            managed_by_user=managed_by_user,
            poll_interval_seconds=max(
                30,
                poll_interval_seconds or get_settings().web_russian_media_interval,
            ),
            next_poll_at=datetime.now(UTC) if enabled else None,
        )
        db.add(source)
        await db.flush()
        return source

    # Imports and migrations must not overwrite an administrator's choices.
    source_methods = _decode_methods(source.discovery_methods)
    source.discovery_methods = _encode_methods([*source_methods, *discovery_methods])
    if not source.managed_by_user:
        if name and not source.name:
            source.name = name.strip()[:255]
        if region and not source.region:
            source.region = region
        if category:
            source.category = _validate_category(category)
        if rss_url and not source.rss_url:
            source.rss_url = rss_url
        if origin in {"env", "autodiscovery"}:
            source.enabled = enabled
            source.status = status
            if enabled:
                source.next_poll_at = datetime.now(UTC)
    return source


async def upsert_source(
    *,
    name: str,
    domain: str,
    region: str | None = None,
    category: str = "other",
    enabled: bool = False,
    status: str = "unknown",
    rss_url: str | None = None,
    discovery_methods: Iterable[str] = (),
    origin: str = "catalog",
    managed_by_user: bool = False,
    poll_interval_seconds: int | None = None,
) -> WebMediaSource:
    async with session_scope() as db:
        source = await _upsert_source(
            db,
            name=name,
            domain=domain,
            region=region,
            category=category,
            enabled=enabled,
            status=status,
            rss_url=rss_url,
            discovery_methods=discovery_methods,
            origin=origin,
            managed_by_user=managed_by_user,
            poll_interval_seconds=poll_interval_seconds,
        )
        await db.commit()
        await db.refresh(source)
        db.expunge(source)
        return source


async def upsert_endpoint(
    source_id: int,
    url: str,
    *,
    kind: str,
    enabled: bool = True,
    discovered_by: str = "manual",
) -> WebMediaEndpoint:
    normalized_url = (url or "").strip()
    if not normalized_url or urlsplit(normalized_url).scheme not in {"http", "https"}:
        raise ValueError("Некорректный URL источника")
    if kind not in {"rss", "atom", "sitemap", "news_page"}:
        raise ValueError("Неизвестный тип endpoint")
    async with session_scope() as db:
        endpoint = await db.scalar(
            select(WebMediaEndpoint).where(
                WebMediaEndpoint.source_id == source_id,
                WebMediaEndpoint.url == normalized_url,
            )
        )
        if endpoint is None:
            endpoint = WebMediaEndpoint(
                source_id=source_id,
                url=normalized_url,
                kind=kind,
                enabled=enabled,
                discovered_by=discovered_by,
            )
            db.add(endpoint)
        else:
            endpoint.kind = kind
            endpoint.enabled = enabled
            if discovered_by:
                endpoint.discovered_by = discovered_by
        await db.commit()
        await db.refresh(endpoint)
        db.expunge(endpoint)
        return endpoint


async def bulk_import_sources(rows: Iterable[dict[str, str]], *, batch_size: int = 500) -> dict[str, int]:
    """Import an arbitrary-size iterable without materialising it in memory."""

    batch_size = max(1, batch_size)
    imported = 0
    duplicates = 0
    rejected = 0
    async with session_scope() as db:
        pending = 0
        for row in rows:
            try:
                domain = normalize_domain(row.get("domain", ""))
                existing = await db.scalar(
                    select(WebMediaSource.id).where(WebMediaSource.domain == domain)
                )
                if existing is not None:
                    duplicates += 1
                    continue
                rss_url = (row.get("rss_url") or "").strip()
                if rss_url and urlsplit(rss_url).scheme not in {"http", "https"}:
                    raise ValueError("Некорректный RSS URL")
                source = await _upsert_source(
                    db,
                    name=row.get("name", "") or domain,
                    domain=domain,
                    region=row.get("region") or None,
                    category=row.get("category", "other"),
                    enabled=False,
                    status="unknown",
                    rss_url=rss_url or None,
                    discovery_methods=["import"],
                    origin="import",
                )
                candidate_exists = await db.scalar(
                    select(WebMediaCandidate.id).where(
                        WebMediaCandidate.domain == domain
                    )
                )
                if candidate_exists is None:
                    db.add(
                        WebMediaCandidate(
                            domain=domain,
                            name=(row.get("name", "") or domain)[:255],
                            region=row.get("region") or None,
                            category=_validate_category(row.get("category")),
                            source_url=(f"https://{domain}"),
                            discovered_by="import",
                            status="pending",
                            next_check_at=datetime.now(UTC),
                        )
                    )
                if rss_url:
                    db.add(
                        WebMediaEndpoint(
                            source_id=source.id,
                            url=rss_url,
                            kind="rss",
                            enabled=False,
                            discovered_by="import",
                        )
                    )
                imported += 1
                pending += 1
                if pending >= batch_size:
                    await db.commit()
                    pending = 0
            except (TypeError, ValueError):
                rejected += 1
        await db.commit()
    return {"imported": imported, "duplicates": duplicates, "rejected": rejected}


async def get_source(source_id: int) -> WebMediaSource | None:
    async with session_scope() as db:
        source = await db.get(WebMediaSource, source_id)
        if source is not None:
            db.expunge(source)
        return source


async def list_sources(
    *,
    page: int = 0,
    page_size: int = 20,
    search: str = "",
    region: str | None = None,
    category: str | None = None,
    status: str | None = None,
) -> tuple[list[WebMediaSource], int]:
    page = max(0, page)
    page_size = min(100, max(1, page_size))
    async with session_scope() as db:
        query = select(WebMediaSource)
        count_query = select(func.count(WebMediaSource.id))
        if search.strip():
            pattern = f"%{search.strip()}%"
            condition = WebMediaSource.name.ilike(pattern) | WebMediaSource.domain.ilike(pattern)
            query = query.where(condition)
            count_query = count_query.where(condition)
        if region:
            query = query.where(WebMediaSource.region == region)
            count_query = count_query.where(WebMediaSource.region == region)
        if category:
            query = query.where(WebMediaSource.category == _validate_category(category))
            count_query = count_query.where(WebMediaSource.category == _validate_category(category))
        if status:
            query = query.where(WebMediaSource.status == status)
            count_query = count_query.where(WebMediaSource.status == status)
        result = await db.execute(
            query.order_by(WebMediaSource.category, WebMediaSource.name)
            .offset(page * page_size)
            .limit(page_size)
        )
        items = list(result.scalars().all())
        total = int(await db.scalar(count_query) or 0)
        for item in items:
            db.expunge(item)
        return items, total


async def get_source_endpoints(
    source_id: int,
    *,
    kinds: Sequence[str] | None = None,
    enabled_only: bool = False,
) -> list[WebMediaEndpoint]:
    async with session_scope() as db:
        query = select(WebMediaEndpoint).where(WebMediaEndpoint.source_id == source_id)
        if kinds:
            query = query.where(WebMediaEndpoint.kind.in_(kinds))
        if enabled_only:
            query = query.where(WebMediaEndpoint.enabled.is_(True))
        result = await db.execute(query.order_by(WebMediaEndpoint.id))
        endpoints = list(result.scalars().all())
        for endpoint in endpoints:
            db.expunge(endpoint)
        return endpoints


async def get_enabled_feed_definitions() -> list[tuple[str, str]]:
    async with session_scope() as db:
        result = await db.execute(
            select(WebMediaSource, WebMediaEndpoint)
            .join(WebMediaEndpoint, WebMediaEndpoint.source_id == WebMediaSource.id)
            .where(
                WebMediaSource.enabled.is_(True),
                WebMediaEndpoint.enabled.is_(True),
                WebMediaEndpoint.kind.in_(["rss", "atom"]),
            )
        )
        pairs = [
            (source.name or source.domain, endpoint.url)
            for source, endpoint in result.all()
        ]
        pairs.sort(key=lambda item: item[0].casefold())
        return pairs


async def claim_due_feed_definitions(
    *,
    limit: int,
    include_catalog: bool = True,
    user_urls: set[str] | None = None,
) -> list[tuple[str, str]]:
    """Claim due RSS sources and return their feeds for one polling batch.

    The claim is persisted, so a second scheduler instance or a process that
    restarts mid-request will not poll the same source concurrently. Stale
    claims are released after fifteen minutes.
    """

    if limit <= 0:
        return []
    now = datetime.now(UTC)
    stale_before = now - timedelta(minutes=15)
    user_url_values = set()
    for url in user_urls or set():
        raw_url = url.strip()
        if raw_url:
            user_url_values.update({raw_url, raw_url.rstrip("/"), raw_url.rstrip("/") + "/"})
    async with session_scope() as db:
        await db.execute(
            WebMediaSource.__table__.update()
            .where(WebMediaSource.polling_started_at < stale_before)
            .values(polling_started_at=None)
        )
        await db.execute(
            WebMediaEndpoint.__table__.update()
            .where(WebMediaEndpoint.polling_started_at < stale_before)
            .values(polling_started_at=None)
        )
        conditions = [
            WebMediaSource.enabled.is_(True),
            WebMediaEndpoint.enabled.is_(True),
            WebMediaEndpoint.kind.in_(["rss", "atom"]),
            (
                WebMediaSource.next_poll_at.is_(None)
                | (WebMediaSource.next_poll_at <= now)
            ),
            WebMediaSource.polling_started_at.is_(None),
        ]
        if not include_catalog:
            if not user_url_values:
                return []
            conditions.append(WebMediaEndpoint.url.in_(user_url_values))
        source_result = await db.execute(
            select(WebMediaSource.id)
            .join(WebMediaEndpoint, WebMediaEndpoint.source_id == WebMediaSource.id)
            .where(*conditions)
            .distinct()
            .order_by(WebMediaSource.next_poll_at, WebMediaSource.id)
            .limit(limit)
        )
        source_ids = [source_id for (source_id,) in source_result.all()]
        if not source_ids:
            return []
        await db.execute(
            WebMediaSource.__table__.update()
            .where(WebMediaSource.id.in_(source_ids))
            .values(polling_started_at=now)
        )
        endpoint_result = await db.execute(
            select(WebMediaSource, WebMediaEndpoint)
            .join(WebMediaEndpoint, WebMediaEndpoint.source_id == WebMediaSource.id)
            .where(
                WebMediaSource.id.in_(source_ids),
                WebMediaSource.enabled.is_(True),
                WebMediaEndpoint.enabled.is_(True),
                WebMediaEndpoint.kind.in_(["rss", "atom"]),
            )
            .order_by(WebMediaSource.id, WebMediaEndpoint.id)
        )
        pairs = []
        for source, endpoint in endpoint_result.all():
            endpoint.polling_started_at = now
            pairs.append((source.name or source.domain, endpoint.url))
        await db.commit()
        return pairs


async def get_enabled_sitemap_endpoints() -> list[WebMediaEndpoint]:
    return await get_enabled_endpoints("sitemap")


async def claim_due_endpoints(
    kind: str,
    *,
    limit: int,
    urls: Iterable[str] | None = None,
) -> list[WebMediaEndpoint]:
    """Claim due endpoints of one kind for a bounded polling batch."""

    if limit <= 0:
        return []
    now = datetime.now(UTC)
    stale_before = now - timedelta(minutes=15)
    allowed_urls = set()
    for url in urls or ():
        raw_url = str(url).strip()
        if raw_url:
            allowed_urls.update(
                {raw_url, raw_url.rstrip("/"), raw_url.rstrip("/") + "/"}
            )
    async with session_scope() as db:
        await db.execute(
            WebMediaSource.__table__.update()
            .where(WebMediaSource.polling_started_at < stale_before)
            .values(polling_started_at=None)
        )
        await db.execute(
            WebMediaEndpoint.__table__.update()
            .where(WebMediaEndpoint.polling_started_at < stale_before)
            .values(polling_started_at=None)
        )
        conditions = [
            WebMediaSource.enabled.is_(True),
            WebMediaEndpoint.enabled.is_(True),
            WebMediaEndpoint.kind == kind,
            (
                WebMediaSource.next_poll_at.is_(None)
                | (WebMediaSource.next_poll_at <= now)
            ),
            WebMediaSource.polling_started_at.is_(None),
        ]
        if allowed_urls:
            conditions.append(WebMediaEndpoint.url.in_(allowed_urls))
        result = await db.execute(
            select(WebMediaSource.id)
            .join(WebMediaEndpoint, WebMediaEndpoint.source_id == WebMediaSource.id)
            .where(*conditions)
            .distinct()
            .order_by(WebMediaSource.next_poll_at, WebMediaSource.id)
            .limit(limit)
        )
        source_ids = [source_id for (source_id,) in result.all()]
        if not source_ids:
            return []
        endpoint_conditions = [
            WebMediaSource.id.in_(source_ids),
            WebMediaSource.enabled.is_(True),
            WebMediaEndpoint.enabled.is_(True),
            WebMediaEndpoint.kind == kind,
        ]
        if allowed_urls:
            endpoint_conditions.append(WebMediaEndpoint.url.in_(allowed_urls))
        endpoint_result = await db.execute(
            select(WebMediaEndpoint)
            .join(WebMediaSource, WebMediaSource.id == WebMediaEndpoint.source_id)
            .where(*endpoint_conditions)
            .order_by(WebMediaEndpoint.id)
            .limit(limit)
        )
        endpoints = list(endpoint_result.scalars().all())
        selected_source_ids = {endpoint.source_id for endpoint in endpoints}
        if not selected_source_ids:
            return []
        await db.execute(
            WebMediaSource.__table__.update()
            .where(WebMediaSource.id.in_(selected_source_ids))
            .values(polling_started_at=now)
        )
        for endpoint in endpoints:
            endpoint.polling_started_at = now
        await db.commit()
        for endpoint in endpoints:
            db.expunge(endpoint)
        return endpoints


async def get_registered_endpoint_urls(kind: str) -> set[str]:
    """Return endpoint URLs known to the registry, including disabled ones."""

    async with session_scope() as db:
        result = await db.execute(
            select(WebMediaEndpoint.url).where(WebMediaEndpoint.kind == kind)
        )
        return {url for (url,) in result.all()}


async def get_enabled_endpoints(kind: str) -> list[WebMediaEndpoint]:
    async with session_scope() as db:
        result = await db.execute(
            select(WebMediaEndpoint)
            .join(WebMediaSource, WebMediaSource.id == WebMediaEndpoint.source_id)
            .where(
                WebMediaSource.enabled.is_(True),
                WebMediaEndpoint.enabled.is_(True),
                WebMediaEndpoint.kind == kind,
            )
            .order_by(WebMediaEndpoint.id)
        )
        endpoints = list(result.scalars().all())
        for endpoint in endpoints:
            db.expunge(endpoint)
        return endpoints


async def set_source_enabled(source_id: int, enabled: bool, *, by_user: bool = True) -> bool:
    async with session_scope() as db:
        source = await db.get(WebMediaSource, source_id)
        if source is None:
            return False
        source.enabled = enabled
        if not enabled:
            source.status = "disabled"
            source.polling_started_at = None
        elif source.status == "disabled":
            source.status = "unknown"
            source.next_poll_at = datetime.now(UTC)
        if by_user:
            source.managed_by_user = True
        await db.commit()
        return True


async def set_source_enabled_by_domain(
    domain: str,
    enabled: bool,
    *,
    by_user: bool = True,
) -> bool:
    normalized = normalize_domain(domain)
    async with session_scope() as db:
        source = await db.scalar(
            select(WebMediaSource).where(WebMediaSource.domain == normalized)
        )
        if source is None:
            return False
        source.enabled = enabled
        if not enabled:
            source.status = "disabled"
            source.polling_started_at = None
        elif source.status == "disabled":
            source.status = "unknown"
            source.next_poll_at = datetime.now(UTC)
        if by_user:
            source.managed_by_user = True
        await db.commit()
        return True


async def save_source_check(
    source_id: int,
    *,
    status: str,
    method: str,
    error: str | None = None,
    endpoint_id: int | None = None,
    http_status: int | None = None,
    result: str | None = None,
    duration_ms: int | None = None,
) -> None:
    now = datetime.now(UTC)
    async with session_scope() as db:
        source = await db.get(WebMediaSource, source_id)
        if source is None:
            return
        source.status = status
        source.last_checked_at = now
        source.error = (error or "")[:2000] or None
        if status == "ok":
            source.last_success_at = now
        db.add(
            WebMediaSourceCheck(
                source_id=source_id,
                endpoint_id=endpoint_id,
                method=method,
                status=status,
                http_status=http_status,
                result=result,
                error=error,
                duration_ms=duration_ms,
                checked_at=now,
            )
        )
        await db.commit()


async def save_endpoint_check(
    endpoint_id: int,
    *,
    status: str,
    error: str | None = None,
    http_status: int | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    content_hash: str | None = None,
) -> None:
    now = datetime.now(UTC)
    async with session_scope() as db:
        endpoint = await db.get(WebMediaEndpoint, endpoint_id)
        if endpoint is None:
            return
        source = await db.get(WebMediaSource, endpoint.source_id)
        endpoint.status = status
        endpoint.last_checked_at = now
        endpoint.polling_started_at = None
        endpoint.error = (error or "")[:2000] or None
        if status == "ok":
            endpoint.last_success_at = now
        if etag is not None:
            endpoint.etag = etag
        if last_modified is not None:
            endpoint.last_modified = last_modified
        if content_hash is not None:
            endpoint.content_hash = content_hash
        if source is not None:
            if source.enabled:
                source.status = status
            source.last_checked_at = now
            source.last_polled_at = now
            source.error = (error or "")[:2000] or None
            if status == "ok":
                source.last_success_at = now
                source.failure_count = 0
            else:
                source.failure_count += 1
            backoff = min(8, 2 ** min(source.failure_count, 3))
            source.next_poll_at = now + timedelta(
                seconds=max(30, source.poll_interval_seconds * backoff)
            )
            db.add(
                WebMediaSourceCheck(
                    source_id=source.id,
                    endpoint_id=endpoint.id,
                    method=endpoint.kind,
                    status=status,
                    http_status=http_status,
                    error=error,
                    checked_at=now,
                )
            )
            active_leases = await db.scalar(
                select(func.count(WebMediaEndpoint.id)).where(
                    WebMediaEndpoint.source_id == source.id,
                    WebMediaEndpoint.polling_started_at.is_not(None),
                )
            )
            if not active_leases:
                source.polling_started_at = None
        await db.commit()


async def save_sitemap_children(endpoint_id: int, urls: Iterable[str]) -> None:
    """Persist the last parsed children of a sitemap index for HTTP 304s."""

    normalized = list(dict.fromkeys(str(url).strip() for url in urls if str(url).strip()))
    async with session_scope() as db:
        endpoint = await db.get(WebMediaEndpoint, endpoint_id)
        if endpoint is None:
            return
        endpoint.child_sitemap_urls = json.dumps(normalized, ensure_ascii=False)
        await db.commit()


async def release_endpoint_poll(endpoint_id: int) -> None:
    """Release a persisted endpoint lease, including cancellation paths."""

    async with session_scope() as db:
        endpoint = await db.get(WebMediaEndpoint, endpoint_id)
        if endpoint is None:
            return
        endpoint.polling_started_at = None
        source = await db.get(WebMediaSource, endpoint.source_id)
        if source is not None:
            active_leases = await db.scalar(
                select(func.count(WebMediaEndpoint.id)).where(
                    WebMediaEndpoint.source_id == source.id,
                    WebMediaEndpoint.polling_started_at.is_not(None),
                )
            )
            if not active_leases:
                source.polling_started_at = None
        await db.commit()


async def save_endpoint_statuses(
    statuses: dict[str, dict[str, str]],
) -> None:
    """Persist one provider batch in one transaction for large registries."""

    if not statuses:
        return
    now = datetime.now(UTC)
    urls = list(statuses)
    async with session_scope() as db:
        result = await db.execute(
            select(WebMediaEndpoint).where(WebMediaEndpoint.url.in_(urls))
        )
        endpoints = {endpoint.url: endpoint for endpoint in result.scalars().all()}
        source_ids = {endpoint.source_id for endpoint in endpoints.values()}
        sources = {}
        if source_ids:
            source_result = await db.execute(
                select(WebMediaSource).where(WebMediaSource.id.in_(source_ids))
            )
            sources = {source.id: source for source in source_result.scalars().all()}
        source_states: dict[int, list[tuple[str, str | None]]] = {}
        for url, payload in statuses.items():
            endpoint = endpoints.get(url)
            if endpoint is None:
                continue
            status = payload.get("status", "error")
            error = payload.get("error") or None
            endpoint.status = status
            endpoint.last_checked_at = now
            endpoint.polling_started_at = None
            endpoint.error = error[:2000] if error else None
            source_states.setdefault(endpoint.source_id, []).append((status, error))
            source = sources.get(endpoint.source_id)
            if source is not None and status == "ok":
                endpoint.last_success_at = now
            if source is not None:
                db.add(
                    WebMediaSourceCheck(
                        source_id=source.id,
                        endpoint_id=endpoint.id,
                        method=endpoint.kind,
                        status=status,
                        error=error,
                        checked_at=now,
                    )
                )
        for source_id, states in source_states.items():
            source = sources.get(source_id)
            if source is None:
                continue
            successful = any(status == "ok" for status, _ in states)
            if source.enabled:
                source.status = "ok" if successful else "error"
            source.last_checked_at = now
            source.last_polled_at = now
            source.error = None if successful else "; ".join(error or "" for _, error in states)
            if successful:
                source.last_success_at = now
                source.failure_count = 0
            else:
                source.failure_count += 1
            backoff = min(8, 2 ** min(source.failure_count, 3))
            source.next_poll_at = now + timedelta(
                seconds=max(30, source.poll_interval_seconds * backoff)
            )
            active_leases = await db.scalar(
                select(func.count(WebMediaEndpoint.id)).where(
                    WebMediaEndpoint.source_id == source.id,
                    WebMediaEndpoint.polling_started_at.is_not(None),
                )
            )
            if not active_leases:
                source.polling_started_at = None
        await db.commit()


async def ensure_candidate(
    domain: str,
    *,
    name: str = "",
    source_url: str | None = None,
    discovered_by: str = "provider",
) -> int | None:
    normalized_domain = normalize_domain(domain)
    async with session_scope() as db:
        if await db.scalar(
            select(WebMediaSource.id).where(WebMediaSource.domain == normalized_domain)
        ):
            return None
        candidate = await db.scalar(
            select(WebMediaCandidate).where(WebMediaCandidate.domain == normalized_domain)
        )
        if candidate is None:
            candidate = WebMediaCandidate(
                domain=normalized_domain,
                name=(name or normalized_domain)[:255],
                source_url=source_url,
                discovered_by=discovered_by,
                status="pending",
                next_check_at=datetime.now(UTC),
            )
            db.add(candidate)
            await db.commit()
            await db.refresh(candidate)
            return candidate.id
        if name and not candidate.name:
            candidate.name = name[:255]
        if source_url and not candidate.source_url:
            candidate.source_url = source_url
        if candidate.status in {"rejected", "temporary_error"}:
            candidate.status = "pending"
            candidate.next_check_at = datetime.now(UTC)
        await db.commit()
        return candidate.id


async def claim_pending_candidates(limit: int) -> list[WebMediaCandidate]:
    if limit <= 0:
        return []
    now = datetime.now(UTC)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    stale_before = now - timedelta(minutes=15)
    async with session_scope() as db:
        stale_result = await db.execute(
            select(WebMediaCandidate).where(
                WebMediaCandidate.status == "checking",
                WebMediaCandidate.last_checked_at < stale_before,
            )
        )
        for candidate in stale_result.scalars().all():
            candidate.status = "temporary_error"
            candidate.next_check_at = now
        checked_today = await db.scalar(
            select(func.count(WebMediaCandidate.id)).where(
                WebMediaCandidate.last_checked_at >= day_start
            )
        ) or 0
        remaining = max(0, get_settings().web_source_discovery_daily_limit - int(checked_today))
        if remaining <= 0:
            return []
        query = (
            select(WebMediaCandidate)
            .where(WebMediaCandidate.status.in_(["pending", "temporary_error"]))
            .where(
                (WebMediaCandidate.next_check_at.is_(None))
                | (WebMediaCandidate.next_check_at <= now)
            )
            .order_by(WebMediaCandidate.id)
            .limit(min(limit, remaining))
        )
        result = await db.execute(query)
        candidates = list(result.scalars().all())
        for candidate in candidates:
            candidate.status = "checking"
            candidate.attempts += 1
            candidate.last_checked_at = now
        await db.commit()
        for candidate in candidates:
            db.expunge(candidate)
        return candidates


async def finish_candidate(
    candidate_id: int,
    *,
    status: str,
    reason: str,
    retry_after: timedelta | None = None,
) -> None:
    async with session_scope() as db:
        candidate = await db.get(WebMediaCandidate, candidate_id)
        if candidate is None:
            return
        candidate.status = status
        candidate.reason = reason[:2000]
        candidate.next_check_at = (
            datetime.now(UTC) + retry_after if retry_after is not None else None
        )
        await db.commit()


async def registry_summary() -> dict[str, int]:
    async with session_scope() as db:
        total = int(await db.scalar(select(func.count(WebMediaSource.id))) or 0)
        categories = {}
        for key in SOURCE_CATEGORIES:
            categories[key] = int(
                await db.scalar(
                    select(func.count(WebMediaSource.id)).where(
                        WebMediaSource.category == key
                    )
                )
                or 0
            )
        rss = int(
            await db.scalar(
                select(func.count(WebMediaEndpoint.id))
                .join(WebMediaSource, WebMediaSource.id == WebMediaEndpoint.source_id)
                .where(
                    WebMediaSource.enabled.is_(True),
                    WebMediaEndpoint.enabled.is_(True),
                    WebMediaEndpoint.kind.in_(["rss", "atom"]),
                    WebMediaEndpoint.status == "ok",
                )
            )
            or 0
        )
        sitemaps = int(
            await db.scalar(
                select(func.count(WebMediaEndpoint.id))
                .join(WebMediaSource, WebMediaSource.id == WebMediaEndpoint.source_id)
                .where(
                    WebMediaSource.enabled.is_(True),
                    WebMediaEndpoint.enabled.is_(True),
                    WebMediaEndpoint.kind == "sitemap",
                    WebMediaEndpoint.status == "ok",
                )
            )
            or 0
        )
        aggregators = int(
            await db.scalar(
                select(func.count(WebMediaSource.id)).where(
                    WebMediaSource.status == "aggregator_only",
                )
            )
            or 0
        )
        candidate_counts = {
            f"candidate_{candidate_status}": int(
                await db.scalar(
                    select(func.count(WebMediaCandidate.id)).where(
                        WebMediaCandidate.status == candidate_status
                    )
                )
                or 0
            )
            for candidate_status in (
                "pending",
                "checking",
                "approved",
                "pending_review",
                "temporary_error",
                "rejected",
            )
        }
        return {
            "total": total,
            "working": int(
                await db.scalar(
                    select(func.count(WebMediaSource.id)).where(
                        WebMediaSource.enabled.is_(True),
                        WebMediaSource.status == "ok",
                    )
                )
                or 0
            ),
            "pending": int(
                await db.scalar(
                    select(func.count(WebMediaSource.id)).where(
                        WebMediaSource.enabled.is_(True),
                        WebMediaSource.status.in_(["unknown", "pending"]),
                    )
                )
                or 0
            ),
            "errors": int(
                await db.scalar(
                    select(func.count(WebMediaSource.id)).where(
                        WebMediaSource.enabled.is_(True),
                        WebMediaSource.status == "error",
                    )
                )
                or 0
            ),
            "disabled": int(
                await db.scalar(
                    select(func.count(WebMediaSource.id)).where(
                        WebMediaSource.enabled.is_(False)
                    )
                )
                or 0
            ),
            "rss": rss,
            "sitemaps": sitemaps,
            "aggregators": aggregators,
            **candidate_counts,
            **{f"category_{key}": value for key, value in categories.items()},
        }


async def registry_regions() -> list[str]:
    async with session_scope() as db:
        result = await db.execute(
            select(WebMediaSource.region)
            .where(WebMediaSource.region.is_not(None), WebMediaSource.region != "")
            .distinct()
            .order_by(WebMediaSource.region)
        )
        return [value for (value,) in result.all() if value]


async def registry_statistics() -> dict[str, object]:
    """Return real counters and bounded match aggregates for the admin panel."""

    from datetime import timedelta

    day_start = datetime.now(UTC) - timedelta(days=1)
    async with session_scope() as db:
        detection_times = await db.execute(
            select(WebArticleRecord.published_at, WebArticleRecord.first_seen_at).where(
                WebArticleRecord.first_seen_at >= day_start,
                WebArticleRecord.published_at.is_not(None),
            )
        )
        delays = [
            max(
                0.0,
                (
                    (first_seen if first_seen.tzinfo else first_seen.replace(tzinfo=UTC))
                    - (published_at if published_at.tzinfo else published_at.replace(tzinfo=UTC))
                ).total_seconds(),
            )
            for published_at, first_seen in detection_times.all()
            if published_at is not None and first_seen is not None
        ]
        top_sources_result = await db.execute(
            select(
                WebArticleRecord.source_name,
                func.count(WebDetection.id).label("matches"),
            )
            .join(WebDetection, WebDetection.article_id == WebArticleRecord.id)
            .where(WebDetection.created_at >= day_start)
            .group_by(WebArticleRecord.source_name)
            .order_by(func.count(WebDetection.id).desc())
            .limit(10)
        )
        top_sources = [
            {"source_name": source_name or "Не указан", "matches": int(matches)}
            for source_name, matches in top_sources_result.all()
        ]
        return {
            "publications_24h": int(
                await db.scalar(
                    select(func.count(WebArticleRecord.id)).where(
                        WebArticleRecord.first_seen_at >= day_start
                    )
                )
                or 0
            ),
            "full_texts_24h": int(
                await db.scalar(
                    select(func.count(WebArticleRecord.id)).where(
                        WebArticleRecord.first_seen_at >= day_start,
                        WebArticleRecord.extraction_status == "success",
                    )
                )
                or 0
            ),
            "matches_24h": int(
                await db.scalar(
                    select(func.count(WebDetection.id)).where(
                        WebDetection.created_at >= day_start
                    )
                )
                or 0
            ),
            "sent_24h": int(
                await db.scalar(
                    select(func.count(WebDelivery.id)).where(
                        WebDelivery.delivered_at >= day_start
                    )
                )
                or 0
            ),
            "new_sources_24h": int(
                await db.scalar(
                    select(func.count(WebMediaSource.id)).where(
                        WebMediaSource.created_at >= day_start
                    )
                )
                or 0
            ),
            "average_detection_time_seconds": (
                round(sum(delays) / len(delays), 2) if delays else None
            ),
            "top_match_sources": top_sources,
        }


async def migrate_legacy_registry() -> None:
    """Copy the built-in catalog and user RSS settings exactly once per domain."""

    from app.services.web_monitor.providers import parse_rss_urls
    from app.services.web_monitor.russian_sources import RUSSIAN_MEDIA

    async with session_scope() as db:
        for definition in RUSSIAN_MEDIA:
            enabled_row = await db.get(Setting, f"web_russian_media_enabled:{definition.domain}")
            status_row = await db.get(Setting, f"web_russian_media_status:{definition.domain}")
            status = "unknown"
            if status_row is not None:
                try:
                    status = str(json.loads(status_row.value).get("status") or "unknown")
                except (TypeError, ValueError):
                    status = "error"
            if definition.rss_url is None and status in {"unknown", "unavailable"}:
                status = "aggregator_only"
            source = await _upsert_source(
                db,
                name=definition.name,
                domain=definition.domain,
                category=definition.category,
                region=definition.region,
                enabled=(
                    enabled_row is None
                    or enabled_row.value.casefold() in {"1", "true", "yes", "on"}
                ),
                status=status,
                rss_url=definition.rss_url,
                discovery_methods=["catalog"],
                origin="legacy",
            )
            if not source.managed_by_user:
                source.enabled = (
                    enabled_row is None
                    or enabled_row.value.casefold() in {"1", "true", "yes", "on"}
                )
                if not source.enabled:
                    source.status = "disabled"
            if definition.rss_url:
                existing = await db.scalar(
                    select(WebMediaEndpoint).where(
                        WebMediaEndpoint.source_id == source.id,
                        WebMediaEndpoint.url == definition.rss_url,
                    )
                )
                if existing is None:
                    db.add(
                        WebMediaEndpoint(
                            source_id=source.id,
                            url=definition.rss_url,
                            kind="rss",
                            enabled=source.enabled,
                            discovered_by="legacy",
                            status=status,
                        )
                    )
        for source_name, url in parse_rss_urls(get_settings().web_rss_urls):
            try:
                domain = normalize_domain(url)
            except ValueError:
                continue
            source = await _upsert_source(
                db,
                name=source_name,
                domain=domain,
                category="other",
                enabled=True,
                status="unknown",
                rss_url=url,
                discovery_methods=["env"],
                origin="env",
            )
            existing = await db.scalar(
                select(WebMediaEndpoint).where(
                    WebMediaEndpoint.source_id == source.id,
                    WebMediaEndpoint.url == url,
                )
            )
            if existing is None:
                db.add(
                    WebMediaEndpoint(
                        source_id=source.id,
                        url=url,
                        kind="rss",
                        enabled=True,
                        discovered_by="env",
                    )
                )
        await db.commit()
