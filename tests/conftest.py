"""Shared fixtures: real SQLite database wired into MappingStorage."""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool
from sqlalchemy.sql.functions import now


@compiles(now, "sqlite")
def _compile_now_sqlite(element, compiler, **kw):  # noqa: ANN001, ARG001
    return "CURRENT_TIMESTAMP"


@pytest.fixture()
def sqlite_db(monkeypatch):
    from src.core.models import Base
    import src.services.mapping as mapping_module

    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    async def _create() -> None:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    asyncio.run(_create())
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    monkeypatch.setattr(mapping_module, "async_session_factory", factory)
    yield factory
    asyncio.run(engine.dispose())
