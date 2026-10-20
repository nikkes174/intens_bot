from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy import event
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
            sync_conn.execute(
                text("ALTER TABLE detection_mentions ADD COLUMN mention_id INTEGER")
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
