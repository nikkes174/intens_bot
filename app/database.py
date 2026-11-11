from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_session_factory = None


def get_engine():
    global _engine
    if _engine is None:
        settings = get_settings()
        url = make_url(settings.database_url)
        if url.get_backend_name().startswith("sqlite"):
            database = url.database
            if database and database != ":memory:":
                Path(database).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_async_engine(
            settings.database_url,
            echo=False,
            future=True,
        )

        @event.listens_for(_engine.sync_engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return _engine


def get_session_factory():
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            get_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _session_factory


@asynccontextmanager
async def session_scope():
    async with get_session_factory()() as session:
        yield session


async def init_database():
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_sqlite)
    from app.services.web_monitor.media_registry import migrate_legacy_registry

    await migrate_legacy_registry()


def _migrate_sqlite(sync_conn):
    from sqlalchemy import inspect, text

    inspector = inspect(sync_conn)
    tables = set(inspector.get_table_names())
    if "deliveries" in tables:
        columns = {col["name"] for col in inspector.get_columns("deliveries")}
        if "processing_started_at" not in columns:
            sync_conn.execute(
                text("ALTER TABLE deliveries ADD COLUMN processing_started_at DATETIME")
            )
    if "detection_mentions" in tables:
        columns = {col["name"] for col in inspector.get_columns("detection_mentions")}
        if "keyword" not in columns:
            sync_conn.execute(
                text("ALTER TABLE detection_mentions ADD COLUMN keyword VARCHAR(255)")
            )
        if "mention_id" not in columns:
            sync_conn.execute(text("ALTER TABLE detection_mentions ADD COLUMN mention_id INTEGER"))
    if "web_article_records" in tables:
        columns = {col["name"] for col in inspector.get_columns("web_article_records")}
        additions = {
            "full_text": "ALTER TABLE web_article_records ADD COLUMN full_text TEXT NOT NULL DEFAULT ''",
            "extraction_status": (
                "ALTER TABLE web_article_records ADD COLUMN extraction_status "
                "VARCHAR(32) NOT NULL DEFAULT 'pending'"
            ),
            "extraction_attempts": (
                "ALTER TABLE web_article_records ADD COLUMN extraction_attempts "
                "INTEGER NOT NULL DEFAULT 0"
            ),
            "extraction_last_attempt_at": (
                "ALTER TABLE web_article_records ADD COLUMN extraction_last_attempt_at DATETIME"
            ),
            "extraction_next_attempt_at": (
                "ALTER TABLE web_article_records ADD COLUMN extraction_next_attempt_at DATETIME"
            ),
            "extraction_error": (
                "ALTER TABLE web_article_records ADD COLUMN extraction_error TEXT"
            ),
        }
        for column, statement in additions.items():
            if column not in columns:
                sync_conn.execute(text(statement))
    if "web_article_jobs" in tables:
        columns = {col["name"] for col in inspector.get_columns("web_article_jobs")}
        additions = {
            "notify_watch_ids": (
                "ALTER TABLE web_article_jobs ADD COLUMN notify_watch_ids "
                "TEXT NOT NULL DEFAULT '[]'"
            ),
            "processed_watch_ids": (
                "ALTER TABLE web_article_jobs ADD COLUMN processed_watch_ids "
                "TEXT NOT NULL DEFAULT '[]'"
            ),
            "status": (
                "ALTER TABLE web_article_jobs ADD COLUMN status "
                "VARCHAR(32) NOT NULL DEFAULT 'pending'"
            ),
            "attempts": (
                "ALTER TABLE web_article_jobs ADD COLUMN attempts "
                "INTEGER NOT NULL DEFAULT 0"
            ),
            "last_error": "ALTER TABLE web_article_jobs ADD COLUMN last_error TEXT",
            "next_retry_at": (
                "ALTER TABLE web_article_jobs ADD COLUMN next_retry_at DATETIME"
            ),
            "processing_started_at": (
                "ALTER TABLE web_article_jobs ADD COLUMN processing_started_at DATETIME"
            ),
            "created_at": (
                "ALTER TABLE web_article_jobs ADD COLUMN created_at DATETIME"
            ),
        }
        for column, statement in additions.items():
            if column not in columns:
                sync_conn.execute(text(statement))
    if "web_media_sources" in tables:
        columns = {col["name"] for col in inspector.get_columns("web_media_sources")}
        additions = {
            "poll_interval_seconds": (
                "ALTER TABLE web_media_sources ADD COLUMN poll_interval_seconds "
                "INTEGER NOT NULL DEFAULT 300"
            ),
            "next_poll_at": "ALTER TABLE web_media_sources ADD COLUMN next_poll_at DATETIME",
            "last_polled_at": "ALTER TABLE web_media_sources ADD COLUMN last_polled_at DATETIME",
            "failure_count": (
                "ALTER TABLE web_media_sources ADD COLUMN failure_count "
                "INTEGER NOT NULL DEFAULT 0"
            ),
            "polling_started_at": (
                "ALTER TABLE web_media_sources ADD COLUMN polling_started_at DATETIME"
            ),
        }
        for column, statement in additions.items():
            if column not in columns:
                sync_conn.execute(text(statement))
        sync_conn.execute(text("DROP INDEX IF EXISTS ix_web_media_sources_polling"))
        sync_conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_web_media_sources_polling "
                "ON web_media_sources (enabled, next_poll_at, status)"
            )
        )
    if "web_media_endpoints" in tables:
        columns = {col["name"] for col in inspector.get_columns("web_media_endpoints")}
        if "polling_started_at" not in columns:
            sync_conn.execute(
                text(
                    "ALTER TABLE web_media_endpoints ADD COLUMN polling_started_at DATETIME"
                )
            )


async def close_database():
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _session_factory = None


AsyncSessionLocal = get_session_factory


async def get_session():
    async with get_session_factory()() as session:
        yield session
