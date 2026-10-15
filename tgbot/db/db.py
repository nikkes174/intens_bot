from collections.abc import AsyncGenerator
from typing import Optional

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from tgbot.config import DbConfig


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self):
        self.engine = None
        self.session_factory: Optional[async_sessionmaker[AsyncSession]] = None

    def initialize(self, db_config: DbConfig):
        if self.engine is not None:
            return
        connect_args = {}
        if db_config.url.startswith("sqlite"):
            connect_args = {"check_same_thread": False}
        poolclass = NullPool if db_config.url.startswith("sqlite") else None
        self.engine = create_async_engine(
            db_config.url,
            future=True,
            echo=False,
            connect_args=connect_args,
            poolclass=poolclass,
        )
        self.session_factory = async_sessionmaker(
            self.engine,
            autoflush=False,
            expire_on_commit=False,
        )

    async def get_session(self) -> AsyncGenerator[AsyncSession, None]:
        if self.session_factory is None:
            raise RuntimeError("Database is not initialized. Call initialize() first.")
        async with self.session_factory() as session:
            yield session

    async def init_db(self):
        if self.engine is None:
            raise RuntimeError("Database is not initialized. Call initialize() first.")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    async def close(self):
        if self.engine is not None:
            await self.engine.dispose()
            self.engine = None
            self.session_factory = None


db = Database()


def get_engine(db_config: DbConfig):
    db.initialize(db_config)
    return db.engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    if db.session_factory is None:
        raise RuntimeError("Database engine is not initialized. Call get_engine() first.")
    return db.session_factory


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with db.get_session() as session:
        yield session


async def init_db(db_config: DbConfig):
    get_engine(db_config)
    await db.init_db()


async def close_db():
    await db.close()
