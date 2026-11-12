import os
from pathlib import Path

import pytest
import pytest_asyncio

import app.models  # noqa: F401
from app.database import close_database, init_database


@pytest.fixture(scope="session", autouse=True)
def test_env():
    data_dir = Path.cwd() / ".pytest-tmp" / "test-data"
    data_dir.mkdir(parents=True, exist_ok=True)
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{data_dir / 'test.db'}"
    os.environ["BOT_TOKEN"] = "123:abc"
    os.environ["ADMIN_ID"] = "1"
    os.environ["API_ID"] = "1"
    os.environ["API_HASH"] = "hash"
    os.environ["SESSION_PATH"] = str(data_dir / "test.session")
    import app.config as config_module
    import app.database as db_module

    config_module._settings = None
    db_module._engine = None
    db_module._session_factory = None
    return data_dir


@pytest_asyncio.fixture(autouse=True)
async def reset_db():
    await init_database()
    yield

    from sqlalchemy import text

    from app.database import get_engine

    async with get_engine().begin() as conn:
        for table in (
            "web_deliveries",
            "web_detections",
            "web_article_jobs",
            "web_article_records",
            "web_watches",
            "web_media_source_checks",
            "web_media_endpoints",
            "web_media_candidates",
            "web_media_sources",
            "deliveries",
            "detections",
            "exclusions",
            "sources",
            "removed_sources",
            "mentions",
            "settings",
        ):
            await conn.execute(text(f"DELETE FROM {table}"))
        await conn.execute(text("DELETE FROM settings WHERE key LIKE 'web_%'"))
    await close_database()
