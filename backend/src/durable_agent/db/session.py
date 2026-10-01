"""Connection setup only. Callers choose transaction boundaries and dispose the engine."""

from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def postgres_url(database_url: str) -> URL:
    url = make_url(database_url)
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError("DATABASE_URL must use postgresql or postgresql+psycopg")
    return url.set(drivername="postgresql+psycopg")


def create_database_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(postgres_url(database_url), pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Return a per-task session context; callers explicitly await commit for writes."""
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
