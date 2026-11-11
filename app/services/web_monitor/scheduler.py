from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from time import monotonic

import httpx

from app.config import get_settings
from app.services.monitor import notifications_enabled
from app.services.web_monitor.article_extractor import (
    PinnedPublicIPTransport,
    extract_article,
)
from app.services.web_monitor.media_registry import (
    claim_due_feed_definitions,
    save_endpoint_statuses,
)
from app.services.web_monitor.providers import (
    GDELTProvider,
    GoogleNewsRSSProvider,
    ProviderHTTPError,
    PublicNewsPageProvider,
    RSSProvider,
    SitemapProvider,
    WebProvider,
    parse_rss_urls,
)
from app.services.web_monitor.russian_sources import (
    merge_rss_feeds,
    save_source_status,
    source_for_url,
)
from app.services.web_monitor.source_autodiscovery import (
    domain_from_url,
    observe_article_source,
    process_pending_candidates,
)
from app.services.web_monitor.storage import (
    claim_article_extraction,
    claim_article_job,
    complete_article_job,
    finish_article_extraction,
    get_article_record,
    get_pending_article_jobs,
    get_watches,
    ingest_article,
    normalize_url,
    provider_initialized,
    release_article_extraction,
    release_article_job,
    retry_article_job,
    save_article_job,
    set_provider_initialized,
    set_provider_last_success,
    upsert_article,
)

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 30
ARTICLE_WORKER_COUNT = 3


@dataclass(frozen=True)
class ArticleQueueItem:
    job_id: int


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
            transport=PinnedPublicIPTransport(
                limits=httpx.Limits(
                    max_connections=max(1, settings.web_monitor_max_concurrency),
                    max_keepalive_connections=max(1, settings.web_monitor_max_concurrency),
                )
            ),
            limits=httpx.Limits(
                max_connections=max(1, settings.web_monitor_max_concurrency),
                max_keepalive_connections=max(1, settings.web_monitor_max_concurrency),
            ),
            trust_env=False,
        )
        self._user_rss_feeds = parse_rss_urls(settings.web_rss_urls)
        self._article_semaphore = asyncio.Semaphore(max(1, settings.web_article_max_concurrency))
        self._article_locks: dict[str, asyncio.Lock] = {}
        self._article_queue: asyncio.Queue[ArticleQueueItem] = asyncio.Queue(
            maxsize=max(3, settings.queue_size)
        )
        self._article_worker_tasks: list[asyncio.Task] = []
        self._queued_article_jobs: set[int] = set()
        self._active_article_jobs: set[int] = set()
        self._article_workers_started = False
        self._article_worker_count = min(
            ARTICLE_WORKER_COUNT,
            max(1, settings.web_article_max_concurrency),
        )
        self._autodiscovery_task: asyncio.Task | None = None
        default_providers = [
            GoogleNewsRSSProvider(self.client),
            GDELTProvider(self.client, min_interval=settings.web_gdelt_min_interval),
            RSSProvider(
                self.client,
                merge_rss_feeds(self._user_rss_feeds),
                min_interval=settings.web_russian_media_interval,
                max_concurrency=settings.web_source_poll_concurrency,
            ),
            SitemapProvider(
                self.client,
                max_concurrency=settings.web_source_poll_concurrency,
            ),
            PublicNewsPageProvider(self.client),
        ]
        self._registry_rss_enabled = providers is None
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
        self._provider_concurrency = max(1, settings.web_monitor_max_concurrency)
        self._semaphore = asyncio.Semaphore(self._provider_concurrency)

    @property
    def last_check_at(self) -> datetime | None:
        return self._last_check_at

    @property
    def last_success_at(self) -> dict[str, datetime]:
        return dict(self._last_success_at)

    async def close(self) -> None:
        if self._autodiscovery_task is not None:
            self._autodiscovery_task.cancel()
            await asyncio.gather(self._autodiscovery_task, return_exceptions=True)
            self._autodiscovery_task = None
        workers = list(self._article_worker_tasks)
        self._article_worker_tasks.clear()
        self._article_workers_started = False
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        self._queued_article_jobs.clear()
        self._active_article_jobs.clear()
        if self._owns_client:
            await self.client.aclose()

    def _is_due(self, provider: WebProvider) -> bool:
        now = monotonic()
        return now >= self._next_allowed.get(provider.name, 0) and now >= self._cooldown_until.get(
            provider.name, 0
        )

    def _schedule_next(self, provider: WebProvider) -> None:
        interval = 30.0 if provider.name == "rss" else provider.min_interval
        self._next_allowed[provider.name] = monotonic() + max(30.0, interval)

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

    async def _fetch_queries(self, provider: WebProvider, queries: list[str]):
        """Run provider queries through a bounded worker queue."""

        if not queries:
            return []
        queue: asyncio.Queue[str] = asyncio.Queue(
            maxsize=max(1, self._provider_concurrency * 2)
        )
        results = []

        async def worker() -> None:
            while True:
                query = await queue.get()
                try:
                    try:
                        results.append(await self._fetch(provider, query))
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 - isolate one provider query
                        results.append(exc)
                finally:
                    queue.task_done()

        workers = [
            asyncio.create_task(worker())
            for _ in range(min(self._provider_concurrency, len(queries)))
        ]
        for query in queries:
            await queue.put(query)
        await queue.join()
        for task in workers:
            task.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
        return results

    async def _refresh_russian_media_feeds(self) -> None:
        if self._rss_provider is None:
            return
        if not self._registry_rss_enabled:
            return
        settings = get_settings()
        feeds = await claim_due_feed_definitions(
            limit=settings.web_source_poll_concurrency,
            include_catalog=settings.web_russian_media_enabled,
            user_urls={url for _, url in self._user_rss_feeds},
        )
        if not feeds and not settings.web_russian_media_enabled:
            # Keep legacy user feeds available even before the registry
            # migration has run in an externally managed database.
            feeds = self._user_rss_feeds
        self._rss_provider.set_feeds(feeds)
        self._rss_provider.set_enabled_urls({url for _, url in feeds})

    async def _ensure_article_workers(self) -> None:
        if self._article_workers_started:
            return
        self._article_workers_started = True
        self._article_worker_tasks = [
            asyncio.create_task(
                self._article_worker(),
                name=f"web-article-worker-{index + 1}",
            )
            for index in range(self._article_worker_count)
        ]

    async def _recover_article_jobs(self) -> None:
        free_slots = self._article_queue.maxsize - self._article_queue.qsize()
        if free_slots <= 0:
            return
        for job_id, _, _ in await get_pending_article_jobs(
            free_slots,
            exclude_job_ids=self._active_article_jobs | self._queued_article_jobs,
        ):
            await self._enqueue_article_job(job_id)

    async def _schedule_source_autodiscovery(self) -> None:
        if not get_settings().web_source_autodiscovery_enabled:
            return
        if self._autodiscovery_task is not None and not self._autodiscovery_task.done():
            return
        if self._autodiscovery_task is not None:
            try:
                self._autodiscovery_task.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Source autodiscovery task failed")
        self._autodiscovery_task = asyncio.create_task(
            process_pending_candidates(
                self.client,
                limit=get_settings().web_source_max_candidates_per_run,
            ),
            name="web-source-autodiscovery",
        )

    async def _enqueue_article_job(self, job_id: int) -> bool:
        if job_id in self._queued_article_jobs or job_id in self._active_article_jobs:
            return True
        try:
            self._article_queue.put_nowait(ArticleQueueItem(job_id=job_id))
        except asyncio.QueueFull:
            # The durable WebArticleJob remains pending in the database and
            # will be picked up by _recover_article_jobs on a later cycle.
            return False
        self._queued_article_jobs.add(job_id)
        return True

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

    @staticmethod
    def _article_from_record(record):
        from app.services.web_monitor.providers import WebArticle

        return WebArticle(
            provider=record.provider,
            title=record.title,
            url=record.url,
            source_name=record.source_name,
            published_at=record.published_at,
            description=record.description,
            full_text=record.full_text,
        )

    async def _process_article_job(self, job_id: int) -> None:
        claimed = await claim_article_job(job_id)
        if claimed is None:
            return
        record, notify_ids = claimed
        article = self._article_from_record(record)
        try:
            watches = await get_watches()
            article = await self._prepare_article(article)
            latest_record = await get_article_record(record.id)
            if latest_record is None:
                await complete_article_job(job_id, (watch.id for watch in watches))
                return
            ingest_result = await ingest_article(
                article,
                watches,
                notify_watch_ids=notify_ids,
            )
            from app.services.web_monitor.delivery import enqueue_delivery

            for delivery_id in ingest_result.delivery_ids:
                await enqueue_delivery(delivery_id)

            extraction_pending = (
                get_settings().web_article_extraction_enabled
                and not latest_record.full_text
                and latest_record.extraction_status in {"pending", "processing", "retry"}
            )
            if extraction_pending:
                await retry_article_job(
                    job_id,
                    latest_record.extraction_error or "Текст статьи пока не извлечён",
                    latest_record.extraction_next_attempt_at,
                )
            else:
                await complete_article_job(job_id, (watch.id for watch in watches))
        except asyncio.CancelledError:
            await release_article_extraction(record.id)
            await release_article_job(job_id)
            raise
        except Exception as exc:
            logger.exception("Could not process article job %s", job_id)
            await retry_article_job(
                job_id,
                str(exc),
                datetime.now(UTC) + timedelta(seconds=60),
            )

    async def _article_worker(self) -> None:
        while True:
            item = await self._article_queue.get()
            self._queued_article_jobs.discard(item.job_id)
            self._active_article_jobs.add(item.job_id)
            try:
                await self._process_article_job(item.job_id)
            except asyncio.CancelledError:
                await release_article_job(item.job_id)
                raise
            except Exception:  # pragma: no cover - defensive worker boundary
                logger.exception("Web article worker failed for job %s", item.job_id)
            finally:
                self._active_article_jobs.discard(item.job_id)
                self._article_queue.task_done()

    async def _save_rss_statuses(self) -> None:
        if self._rss_provider is None:
            return
        statuses = self._rss_provider.last_batch_status
        for url, status in statuses.items():
            source = source_for_url(url)
            if source is None:
                continue
            await save_source_status(
                source.domain,
                ok=status.get("status") == "ok",
                error=status.get("error"),
            )
        await save_endpoint_statuses(statuses)

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
            if provider.name in {"rss", "sitemap", "news_page"}:
                queries = [""]
            results = await self._fetch_queries(provider, queries)
            if not results:
                return True
            provider_ok = True
            initialized_ids = {
                watch.id
                for watch in watches
                if await provider_initialized(watch.id, provider.name)
            }
            notify_ids = initialized_ids if await notifications_enabled() else set()
            await self._ensure_article_workers()
            observed_domains: set[str] = set()
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
                        record, _ = await upsert_article(article)
                        job_id = await save_article_job(
                            record.id,
                            notify_watch_ids=notify_ids,
                            watch_ids=(watch.id for watch in watches),
                        )
                        await self._enqueue_article_job(job_id)
                        domain = domain_from_url(article.url)
                        if (
                            provider.name in {"google_news", "gdelt"}
                            and domain
                            and domain not in observed_domains
                        ):
                            await observe_article_source(article)
                            observed_domains.add(domain)
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
        await self._schedule_source_autodiscovery()
        if not watches:
            return
        await self._ensure_article_workers()
        await self._recover_article_jobs()
        if self._rss_provider is None or self._is_due(self._rss_provider):
            await self._refresh_russian_media_feeds()
        # Provider checks are independent and errors are contained by each
        # provider task.  No unbounded background tasks are left behind.
        tasks = [
            asyncio.create_task(self._check_provider(provider, watches))
            for provider in self.providers
        ]
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
