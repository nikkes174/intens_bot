"""Regression tests for upgrading databases created before Alembic was enabled."""

import asyncio
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from tgbot.db.migrate import run_migrations


def make_config(database_url: str) -> Config:
    root = Path(__file__).resolve().parents[1]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


@pytest.mark.asyncio
async def test_existing_data_is_preserved_and_schema_is_upgraded(tmp_path):
    database_path = tmp_path / "legacy.db"
    database_url = f"sqlite+aiosqlite:///{database_path.as_posix()}"
    config = make_config(database_url)
    await asyncio.to_thread(command.upgrade, config, "0001")

    engine = create_async_engine(database_url)
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO users (id, telegram_user_id, username, payment_ok) "
                "VALUES (1, 123456789, 'legacy-user', 0)"
            )
        )
        await connection.execute(text("DROP TABLE alembic_version"))
    await engine.dispose()

    await asyncio.to_thread(run_migrations, database_url)
    # Repeated application startup must see the current version and stay idempotent.
    await asyncio.to_thread(run_migrations, database_url)

    engine = create_async_engine(database_url)
    async with engine.connect() as connection:
        columns = await connection.run_sync(
            lambda sync_connection: {
                column["name"] for column in inspect(sync_connection).get_columns("trainings")
            }
        )
        order_columns = await connection.run_sync(
            lambda sync_connection: {
                column["name"] for column in inspect(sync_connection).get_columns("orders")
            }
        )
        scheduled_columns = await connection.run_sync(
            lambda sync_connection: {
                column["name"]
                for column in inspect(sync_connection).get_columns("scheduled_messages")
            }
        )
        tables = await connection.run_sync(lambda conn: set(inspect(conn).get_table_names()))
        username = await connection.scalar(
            text("SELECT username FROM users WHERE telegram_user_id = 123456789")
        )
        version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
    await engine.dispose()

    assert "product_code" in columns
    assert "is_bundle" in columns
    assert {"product_components", "order_items", "scheduled_messages"} <= tables
    assert "payment_metadata_version" in order_columns
    assert "claimed_at" in scheduled_columns
    assert username == "legacy-user"
    assert version == "0003"

    await asyncio.to_thread(command.downgrade, config, "base")
    engine = create_async_engine(database_url)
    async with engine.connect() as connection:
        tables_after_downgrade = await connection.run_sync(
            lambda conn: set(inspect(conn).get_table_names())
        )
    await engine.dispose()
    assert "users" not in tables_after_downgrade
