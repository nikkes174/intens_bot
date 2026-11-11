import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.database import session_scope
from app.models import WebArticleJob, WebArticleRecord, WebWatch
from app.services.web_monitor.providers import WebArticle
from app.services.web_monitor.scheduler import WebMonitor
from app.services.web_monitor.storage import save_article_job, upsert_article


class HundredArticleProvider:
    name = "rss"
    min_interval = 30

    async def fetch(self, query):
        return [
            WebArticle(
                provider=self.name,
                title=f"Иван Иванов: новость {index}",
                url=f"https://queue.example/articles/{index}",
                source_name="Queue test",
                published_at=datetime.now(UTC),
            )
            for index in range(100)
        ]


@pytest.mark.asyncio
async def test_article_queue_has_three_workers_and_provider_does_not_wait_for_text(
    monkeypatch,
):
    async with session_scope() as db:
        watch = WebWatch(query="иван иванов", enabled=True)
        db.add(watch)
        await db.commit()
        await db.refresh(watch)

    started = asyncio.Event()
    release = asyncio.Event()
    active = 0
    max_active = 0

    async def process_job(_job_id):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        started.set()
        await release.wait()
        active -= 1

    monitor = WebMonitor(providers=[HundredArticleProvider()])
    monkeypatch.setattr(monitor, "_process_article_job", process_job)
    try:
        watches = [watch]
        result = await monitor._check_provider(monitor.providers[0], watches)
        assert result is True
        await asyncio.wait_for(started.wait(), timeout=1)
        assert max_active <= 3
        async with session_scope() as db:
            assert await db.scalar(select(func.count(WebArticleRecord.id))) == 100
            assert await db.scalar(select(func.count(WebArticleJob.id))) == 100
        release.set()
        await asyncio.wait_for(monitor._article_queue.join(), timeout=10)
    finally:
        release.set()
        await monitor.close()


@pytest.mark.asyncio
async def test_queue_overflow_keeps_jobs_for_restart_recovery():
    monitor = WebMonitor(providers=[])
    monitor._article_queue = asyncio.Queue(maxsize=1)
    try:
        article_ids = []
        for index in range(3):
            record, _ = await upsert_article(
                WebArticle(
                    provider="rss",
                    title=f"Публикация {index}",
                    url=f"https://queue.example/recovery/{index}",
                    source_name="Queue test",
                    published_at=datetime.now(UTC),
                )
            )
            article_ids.append(
                await save_article_job(record.id, watch_ids=[1], notify_watch_ids=[])
            )
        assert await monitor._enqueue_article_job(article_ids[0]) is True
        assert await monitor._enqueue_article_job(article_ids[1]) is False
        assert await monitor._enqueue_article_job(article_ids[2]) is False

        restarted = WebMonitor(providers=[])
        try:
            await restarted._ensure_article_workers()
            await restarted._recover_article_jobs()
            assert restarted._article_queue.qsize() == 3
        finally:
            await restarted.close()
    finally:
        await monitor.close()


@pytest.mark.asyncio
async def test_article_workers_are_cancelled_on_shutdown():
    monitor = WebMonitor(providers=[])
    await monitor._ensure_article_workers()
    workers = list(monitor._article_worker_tasks)
    assert len(workers) == 3
    await monitor.close()
    assert all(worker.done() for worker in workers)


@pytest.mark.asyncio
async def test_same_article_from_two_providers_uses_one_durable_job():
    article = WebArticle(
        provider="google_news",
        title="Иван Иванов выступил на форуме",
        url="https://queue.example/google-link",
        source_name="Queue test",
        published_at=datetime.now(UTC),
    )
    first, _ = await upsert_article(article)
    second, _ = await upsert_article(
        WebArticle(
            provider="rss",
            title=article.title,
            url="https://queue.example/rss-link",
            source_name=article.source_name,
            published_at=article.published_at,
        )
    )
    first_job = await save_article_job(first.id, watch_ids=[1], notify_watch_ids=[])
    second_job = await save_article_job(second.id, watch_ids=[1], notify_watch_ids=[])
    assert first.id == second.id
    assert first_job == second_job
    async with session_scope() as db:
        assert await db.scalar(select(func.count(WebArticleJob.id))) == 1
