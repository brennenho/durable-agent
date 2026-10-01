"""Service-owned connection pool and per-task async sessions."""

from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def postgres_url(database_url: str) -> URL:
    url = make_url(database_url)
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError("DATABASE_URL must use postgresql or postgresql+psycopg")
    return url.set(drivername="postgresql+psycopg")


class Database:
    """Create once at service startup; close after sessions finish at shutdown."""

    def __init__(self, database_url: str) -> None:
        self._engine = create_async_engine(postgres_url(database_url), pool_pre_ping=True)
        self._sessions = async_sessionmaker(self._engine, expire_on_commit=False, autoflush=False)

    def session(self) -> AsyncSession:
        """Return a fresh session. Writes require an explicit commit."""
        return self._sessions()

    async def close(self) -> None:
        """Release pooled connections after in-flight work has stopped."""
        await self._engine.dispose()
