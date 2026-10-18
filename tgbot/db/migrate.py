"""Run Alembic migrations against the configured async database URL."""

import asyncio
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import create_async_engine


def run_migrations(database_url: str) -> None:
    project_root = Path(__file__).resolve().parents[2]
    config = Config(str(project_root / "alembic.ini"))
    config.set_main_option("script_location", str(project_root / "alembic"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

    async def inspect_schema() -> tuple[bool, bool]:
        engine = create_async_engine(database_url, pool_pre_ping=True)
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(
                    lambda sync_connection: (
                        inspect(sync_connection).has_table("users"),
                        inspect(sync_connection).has_table("alembic_version"),
                    )
                )
        finally:
            await engine.dispose()

    has_users, has_version_table = asyncio.run(inspect_schema())
    if has_users and not has_version_table:
        # Versions before Alembic already had the full 0001 schema created by create_all().
        # Stamp that baseline, then apply only forward migrations, preserving user data.
        command.stamp(config, "0001")
    command.upgrade(config, "head")
