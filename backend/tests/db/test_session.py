from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.session import (
    check_database,
    create_database_engine,
    create_session_factory,
    dispose_database,
)


@pytest.mark.asyncio
async def test_create_database_engine_uses_async_dialect() -> None:
    engine = create_database_engine(
        "postgresql+asyncpg://recruit:secret@127.0.0.1:5432/recruit"
    )

    assert engine.url.drivername == "postgresql+asyncpg"

    await dispose_database(engine)


@pytest.mark.asyncio
async def test_create_session_factory_expire_on_commit_false() -> None:
    engine = create_database_engine(
        "postgresql+asyncpg://recruit:secret@127.0.0.1:5432/recruit"
    )
    session_factory = create_session_factory(engine)

    assert session_factory.kw["expire_on_commit"] is False

    await dispose_database(engine)


@pytest.mark.asyncio
async def test_check_database_returns_true_on_success() -> None:
    engine = MagicMock(spec=AsyncEngine)
    connection = AsyncMock()
    connection.execute = AsyncMock()
    context_manager = AsyncMock()
    context_manager.__aenter__.return_value = connection
    context_manager.__aexit__.return_value = None
    engine.connect.return_value = context_manager

    result = await check_database(engine)

    assert result is True
    connection.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_check_database_returns_false_on_sqlalchemy_error() -> None:
    engine = MagicMock(spec=AsyncEngine)
    engine.connect.side_effect = SQLAlchemyError("connection failed")

    result = await check_database(engine)

    assert result is False


@pytest.mark.asyncio
async def test_check_database_returns_false_on_connection_error() -> None:
    engine = MagicMock(spec=AsyncEngine)
    engine.connect.side_effect = OSError("connection refused")

    result = await check_database(engine)

    assert result is False


@pytest.mark.asyncio
async def test_dispose_database_calls_engine_dispose() -> None:
    engine = AsyncMock(spec=AsyncEngine)
    engine.dispose = AsyncMock()

    await dispose_database(engine)

    engine.dispose.assert_awaited_once()
