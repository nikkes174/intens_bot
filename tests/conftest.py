import os
from pathlib import Path

import pytest
import pytest_asyncio

from app.database import close_database, init_database
import app.models  # noqa: F401


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
    from app.database import get_engine
    from sqlalchemy import text

    async with get_engine().begin() as conn:
        for table in (
            "deliveries",
            "detections",
            "exclusions",
            "sources",
            "mentions",
            "settings",
        ):
            await conn.execute(text(f"DELETE FROM {table}"))
    await close_database()
