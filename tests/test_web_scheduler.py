import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services.web_monitor.providers import WebArticle
from app.services.web_monitor.scheduler import WebMonitor


class SlowProvider:
    name = "test"
    min_interval = 30

    def __init__(self):
        self.active = 0
        self.max_active = 0

    async def fetch(self, query):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return [
            WebArticle(
                provider=self.name,
                title="Иван Иванов",
                url="https://test.example/article",
                source_name="Test",
                published_at=None,
            )
        ]


@pytest.mark.asyncio
async def test_same_provider_checks_are_not_parallel(monkeypatch):
    provider = SlowProvider()
    monitor = WebMonitor(providers=[provider])
    monkeypatch.setattr(
        "app.services.web_monitor.scheduler.provider_initialized",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "app.services.web_monitor.scheduler.notifications_enabled",
        AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        "app.services.web_monitor.scheduler.ingest_article",
        AsyncMock(return_value=SimpleNamespace(delivery_ids=())),
    )
    monkeypatch.setattr(
        "app.services.web_monitor.scheduler.set_provider_last_success",
        AsyncMock(),
    )
    monkeypatch.setattr(
        "app.services.web_monitor.scheduler.set_provider_initialized",
        AsyncMock(),
    )
    watches = [SimpleNamespace(id=1, query="иван иванов")]
    await asyncio.gather(
        monitor._check_provider(provider, watches),
        monitor._check_provider(provider, watches),
    )
    await monitor.close()
    assert provider.max_active == 1
