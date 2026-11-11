from __future__ import annotations

import asyncio
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from time import monotonic

import httpx

from app.config import get_settings
from app.services.monitor import notifications_enabled
from app.services.web_monitor.article_extractor import extract_article
from app.services.web_monitor.providers import (
    GDELTProvider,
    GoogleNewsRSSProvider,
    ProviderHTTPError,
    RSSProvider,
    WebProvider,
    parse_rss_urls,
)
from app.services.web_monitor.russian_sources import (
    get_enabled_sources,
    merge_rss_feeds,
    save_source_status,
    source_for_url,
)
from app.services.web_monitor.storage import (
    claim_article_extraction,
    finish_article_extraction,
    get_watches,
    ingest_article,
    normalize_url,
    provider_initialized,
    set_provider_initialized,
    set_provider_last_success,
    upsert_article,
)

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 30


class WebMonitor:
    def __init__(
        self,
        providers: list[WebProvider] | None = None,
        client: httpx.AsyncClient | None = None,
    ):
        settings = get_settings()
        self._owns_client = client is None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(max(1, settings.web_monitor_timeout)),
            limits=httpx.Limits(
                max_connections=max(1, settings.web_monitor_max_concurrency),
                max_keepalive_connections=max(1, settings.web_monitor_max_concurrency),
            ),
        )
        self._user_rss_feeds = parse_rss_urls(settings.web_rss_urls)
        self._article_semaphore = asyncio.Semaphore(max(1, settings.web_article_max_concurrency))
        self._article_locks: dict[str, asyncio.Lock] = {}
        default_providers = [
            GoogleNewsRSSProvider(self.client),
            GDELTProvider(self.client, min_interval=settings.web_gdelt_min_interval),
            RSSProvider(
                self.client,
                merge_rss_feeds(self._user_rss_feeds),
                min_interval=settings.web_russian_media_interval,
            ),
        ]
        self.providers = default_providers if providers is None else providers
        self._rss_provider = next(
            (provider for provider in self.providers if isinstance(provider, RSSProvider)),
            None,
        )
        self._provider_locks = {provider.name: asyncio.Lock() for provider in self.providers}
        self._next_allowed: dict[str, float] = {}
        self._failure_count: dict[str, int] = {}
        self._cooldown_until: dict[str, float] = {}
        self._last_check_at: datetime | None = None
        self._last_success_at: dict[str, datetime] = {}
        self._semaphore = asyncio.Semaphore(max(1, settings.web_monitor_max_concurrency))

    @property
    def last_check_at(self) -> datetime | None:
        return self._last_check_at

    @property
    def last_success_at(self) -> dict[str, datetime]:
        return dict(self._last_success_at)

    async def close(self) -> None:
        if self._owns_client:
            await self.client.aclose()

    def _is_due(self, provider: WebProvider) -> bool:
        now = monotonic()
        return now >= self._next_allowed.get(provider.name, 0) and now >= self._cooldown_until.get(
            provider.name, 0
        )

    def _schedule_next(self, provider: WebProvider) -> None:
        self._next_allowed[provider.name] = monotonic() + max(30.0, provider.min_interval)

    def _backoff(self, provider: WebProvider, retry_after: float | None = None) -> None:
        failures = self._failure_count.get(provider.name, 0) + 1
        self._failure_count[provider.name] = failures
        delay = retry_after if retry_after is not None else min(2**failures, 300)
        self._cooldown_until[provider.name] = monotonic() + max(1.0, delay)

    def _success(self, provider: WebProvider) -> None:
        self._failure_count[provider.name] = 0
        self._cooldown_until.pop(provider.name, None)

    async def _fetch(self, provider: WebProvider, query: str):
        async with self._semaphore:
            return await provider.fetch(query)

    async def _refresh_russian_media_feeds(self) -> None:
        if self._rss_provider is None:
            return
        enabled_urls = {url for _, url in self._user_rss_feeds}
        if get_settings().web_russian_media_enabled:
            enabled_urls.update(
                source.rss_url
                for source in await get_enabled_sources()
                if source.rss_url
            )
        self._rss_provider.set_enabled_urls(enabled_urls)

    async def _prepare_article(self, article):
        """Persist identity, then enrich an article outside any DB transaction."""

        canonical_url = normalize_url(article.url)
        lock = self._article_locks.setdefault(canonical_url, asyncio.Lock())
        async with lock:
            record, _ = await upsert_article(article)
            if record.full_text:
                return replace(article, full_text=record.full_text)
            settings = get_settings()
            if not settings.web_article_extraction_enabled:
                return article
            async with self._article_semaphore:
                if not await claim_article_extraction(record.id):
                    return article
                try:
                    full_text = await extract_article(self.client, record.canonical_url)
                    error = None if full_text else "Текст статьи не извлечён"
                except Exception as exc:  # noqa: BLE001 - one site must not stop the queue
                    full_text = None
                    error = str(exc)
                    logger.warning(
                        "Article extraction failed for %s: %s",
                        record.canonical_url,
                        exc,
                    )
                await finish_article_extraction(record.id, full_text, error)
                return replace(article, full_text=full_text or "")

    async def _save_rss_statuses(self) -> None:
        if self._rss_provider is None:
            return
        for url, status in self._rss_provider.feed_status.items():
            source = source_for_url(url)
            if source is None:
                continue
            await save_source_status(
                source.domain,
                ok=status.get("status") == "ok",
                error=status.get("error"),
            )

    async def _check_provider(self, provider: WebProvider, watches) -> bool:
        lock = self._provider_locks.setdefault(provider.name, asyncio.Lock())
        if lock.locked() or not self._is_due(provider):
            return False
        async with lock:
            if not self._is_due(provider):
                return False
            self._schedule_next(provider)
            queries = [watch.query for watch in watches]
            # RSS feeds are fetched once per cycle; search APIs need one query
            # per watch so the source can perform proper relevance filtering.
            if provider.name == "rss":
                queries = [""]
            tasks = [asyncio.create_task(self._fetch(provider, query)) for query in queries]
            if not tasks:
                return True
            results = await asyncio.gather(*tasks, return_exceptions=True)
            provider_ok = True
            initialized_ids = {
                watch.id
                for watch in watches
                if await provider_initialized(watch.id, provider.name)
            }
            notify_ids = initialized_ids if await notifications_enabled() else set()
            for result in results:
                if isinstance(result, BaseException):
                    provider_ok = False
                    retry_after = result.retry_after if isinstance(result, ProviderHTTPError) else None
                    self._backoff(provider, retry_after)
                    logger.warning("Web provider %s failed: %s", provider.name, result)
                    continue
                for article in result:
                    if not self._is_fresh(article.published_at):
                        continue
                    try:
                        article = await self._prepare_article(article)
                        ingest_result = await ingest_article(
                            article,
                            watches,
                            notify_watch_ids=notify_ids,
                        )
                        # Storage remains independent from the worker; only
                        # durable delivery ids are handed to its in-process
                        # queue after the transaction has committed.
                        from app.services.web_monitor.delivery import enqueue_delivery

                        for delivery_id in ingest_result.delivery_ids:
                            await enqueue_delivery(delivery_id)
                    except Exception:
                        provider_ok = False
                        logger.exception("Could not store article from %s", provider.name)
            await self._save_rss_statuses()
            if provider_ok:
                self._success(provider)
                now = datetime.now(UTC)
                self._last_success_at[provider.name] = now
                await set_provider_last_success(provider.name, now)
                for watch in watches:
                    await set_provider_initialized(watch.id, provider.name)
            return provider_ok

    def _is_fresh(self, published_at: datetime | None) -> bool:
        if published_at is None:
            return True
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=UTC)
        cutoff = datetime.now(UTC) - timedelta(
            hours=max(1, get_settings().web_monitor_freshness_hours)
        )
        return published_at >= cutoff

    async def check(self) -> None:
        if not get_settings().web_monitor_enabled:
            return
        watches = await get_watches()
        self._last_check_at = datetime.now(UTC)
        if not watches:
            return
        await self._refresh_russian_media_feeds()
        # Provider checks are independent and errors are contained by each
        # provider task.  No unbounded background tasks are left behind.
        tasks = [asyncio.create_task(self._check_provider(provider, watches)) for provider in self.providers]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


_monitor: WebMonitor | None = None
_scheduler_running = False


def get_web_monitor() -> WebMonitor:
    global _monitor
    if _monitor is None:
        _monitor = WebMonitor()
    return _monitor


def configure_web_monitor(monitor: WebMonitor | None) -> None:
    global _monitor
    _monitor = monitor


async def check_web_sources() -> None:
    await get_web_monitor().check()


async def close_web_monitor() -> None:
    global _monitor
    if _monitor is not None:
        await _monitor.close()
        _monitor = None


async def run_web_monitor() -> None:
    """Run the 30-second supervisor without allowing provider errors to escape."""

    global _scheduler_running
    if _scheduler_running:
        logger.warning("Web monitor scheduler is already running")
        return
    _scheduler_running = True
    try:
        while True:
            started = monotonic()
            try:
                await check_web_sources()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Web monitoring failed")
            elapsed = monotonic() - started
            interval = max(CHECK_INTERVAL, get_settings().web_monitor_interval)
            await asyncio.sleep(max(0, interval - elapsed))
    finally:
        _scheduler_running = False
