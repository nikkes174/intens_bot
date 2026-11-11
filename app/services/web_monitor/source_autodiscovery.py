"""Bounded automatic source discovery from Google News and GDELT results."""

from __future__ import annotations

import asyncio
import re
from datetime import timedelta
from urllib.parse import urlsplit

import httpx

from app.services.web_monitor.media_registry import (
    claim_pending_candidates,
    ensure_candidate,
    finish_candidate,
    get_source,
    normalize_domain,
    save_source_check,
    upsert_endpoint,
    upsert_source,
)
from app.services.web_monitor.providers import WebArticle
from app.services.web_monitor.source_discovery import SourceDiscoveryResult, inspect_source

_CYRILLIC_RE = re.compile(r"[А-Яа-яЁё]")
_NEWS_RE = re.compile(
    r"(?:новост|событ|сообщ|пресс|происшеств|политик|экономик|обществен|"
    r"news|press|article)",
    re.IGNORECASE,
)
_SPAM_RE = re.compile(
    r"(?:купить|цена|скидк|каталог|казино|ставк|кредит|промокод|"
    r"заработ|партнёрск|партнерск)",
    re.IGNORECASE,
)


def domain_from_url(url: str) -> str | None:
    try:
        return normalize_domain(urlsplit(url).hostname or "")
    except ValueError:
        return None


def _looks_russian_information_resource(
    domain: str,
    result: SourceDiscoveryResult,
    *,
    name: str = "",
) -> bool:
    # A .ru/.рф suffix is only a weak signal. Require several distinct sample
    # publications, dates, a news/information signal and Russian-language
    # evidence. This keeps discovery conservative for catalogs, shops and SEO
    # pages while allowing Russian-language resources on any TLD.
    entries = [entry for entry in result.sample_entries if entry.url]
    if not result.methods or len({entry.url for entry in entries}) < 2:
        return False
    dated_count = sum(
        1
        for entry in entries
        if entry.published_at is not None or entry.lastmod is not None
    )
    if dated_count < 2:
        return False
    text = " ".join(
        [
            name,
            *(entry.title for entry in entries),
            *(entry.url for entry in entries),
        ]
    )
    if not _CYRILLIC_RE.search(text) or _SPAM_RE.search(text):
        return False
    if not _NEWS_RE.search(text):
        return False
    titles = [entry.title.casefold().strip() for entry in entries if entry.title.strip()]
    return len(titles) < 2 or len(set(titles)) > 1


async def observe_article_source(article: WebArticle) -> int | None:
    """Persist an unknown provider domain without doing network work."""

    if article.provider not in {"google_news", "gdelt", "sitemap", "rss"}:
        return None
    domain = domain_from_url(article.url)
    if domain is None:
        return None
    return await ensure_candidate(
        domain,
        name=article.source_name or domain,
        source_url=article.url,
        discovered_by=article.provider,
    )


async def _register_verified(
    *,
    domain: str,
    name: str,
    discovered_by: str,
    result: SourceDiscoveryResult,
) -> bool:
    if not _looks_russian_information_resource(domain, result, name=name):
        return False
    source = await upsert_source(
        name=name or domain,
        domain=domain,
        category="other",
        enabled=True,
        status="ok",
        discovery_methods=result.methods,
        origin="autodiscovery",
    )
    for feed_url in result.feeds:
        kind = "atom" if "atom" in feed_url.casefold() else "rss"
        await upsert_endpoint(
            source.id,
            feed_url,
            kind=kind,
            discovered_by=discovered_by,
        )
    for sitemap_url in result.sitemaps:
        await upsert_endpoint(
            source.id,
            sitemap_url,
            kind="sitemap",
            discovered_by=discovered_by,
        )
    for page_url in result.news_pages:
        await upsert_endpoint(
            source.id,
            page_url,
            kind="news_page",
            discovered_by=discovered_by,
        )
    await save_source_check(
        source.id,
        status="ok",
        method="discovery",
        result=", ".join(result.methods),
    )
    return True


async def discover_and_register_domain(
    client: httpx.AsyncClient,
    domain: str,
    *,
    name: str = "",
    discovered_by: str = "admin",
) -> tuple[bool, SourceDiscoveryResult]:
    normalized = normalize_domain(domain)
    result = await inspect_source(client, normalized)
    registered = await _register_verified(
        domain=normalized,
        name=name or normalized,
        discovered_by=discovered_by,
        result=result,
    )
    return registered, result


async def process_pending_candidates(
    client: httpx.AsyncClient,
    *,
    limit: int | None = None,
) -> int:
    candidates = await claim_pending_candidates(limit or 5)
    processed = 0
    for candidate in candidates:
        processed += 1
        try:
            result = await inspect_source(client, candidate.domain)
            registered = await _register_verified(
                domain=candidate.domain,
                name=candidate.name,
                discovered_by=candidate.discovered_by,
                result=result,
            )
            if registered:
                await finish_candidate(
                    candidate.id,
                    status="approved",
                    reason=f"Подтверждены методы: {', '.join(result.methods)}",
                )
            elif result.methods:
                await finish_candidate(
                    candidate.id,
                    status="pending_review",
                    reason="Метод публикаций найден, но российская информационная направленность не подтверждена",
                )
            else:
                await finish_candidate(
                    candidate.id,
                    status="temporary_error" if candidate.attempts < 3 else "rejected",
                    reason="; ".join(result.errors) or "RSS/Atom и sitemap не найдены",
                    retry_after=timedelta(hours=6) if candidate.attempts < 3 else None,
                )
        except Exception as exc:  # noqa: BLE001 - persist every failed candidate
            await finish_candidate(
                candidate.id,
                status="temporary_error" if candidate.attempts < 3 else "rejected",
                reason=str(exc),
                retry_after=timedelta(hours=6) if candidate.attempts < 3 else None,
            )
    return processed


async def check_source_now(client: httpx.AsyncClient, source_id: int) -> SourceDiscoveryResult:
    source = await get_source(source_id)
    if source is None:
        raise ValueError("Источник не найден")
    result = await inspect_source(client, source.domain)
    if result.methods:
        await _register_verified(
            domain=source.domain,
            name=source.name,
            discovered_by="manual",
            result=result,
        )
    else:
        await save_source_check(
            source.id,
            status="error",
            method="manual",
            error="; ".join(result.errors) or "RSS/Atom и sitemap не найдены",
        )
    return result


async def check_registered_sources(
    client: httpx.AsyncClient,
    *,
    limit: int = 5,
) -> int:
    from app.services.web_monitor.media_registry import list_sources

    sources, _ = await list_sources(page=0, page_size=max(1, limit))
    semaphore = asyncio.Semaphore(max(1, min(limit, 10)))

    async def check(source) -> None:
        async with semaphore:
            try:
                await check_source_now(client, source.id)
            except Exception as exc:  # noqa: BLE001 - isolate one source
                await save_source_check(
                    source.id,
                    status="error",
                    method="manual",
                    error=str(exc),
                )

    await asyncio.gather(*(check(source) for source in sources))
    return len(sources)
