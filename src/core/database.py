"""
ClawMux — Database engine & session factory.

Uses SQLAlchemy 2.0 async with asyncpg driver.
"""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from src.core.config import settings

engine: AsyncEngine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_session() -> AsyncSession:
    """Dependency — yield an async session."""
    async with async_session_factory() as session:
        yield session


async def dispose_engine() -> None:
    """Close all connections (for graceful shutdown)."""
    await engine.dispose()
