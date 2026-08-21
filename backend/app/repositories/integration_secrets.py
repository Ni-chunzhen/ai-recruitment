"""List / get / upsert helpers for integration_secrets (no crypto)."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.integration_secret import IntegrationSecret


async def list_integration_secrets(
    session: AsyncSession,
    *,
    provider: str | None = None,
) -> list[IntegrationSecret]:
    stmt = select(IntegrationSecret).order_by(
        IntegrationSecret.provider, IntegrationSecret.config_key
    )
    if provider is not None:
        stmt = stmt.where(IntegrationSecret.provider == provider)
    result = await session.scalars(stmt)
    return list(result.all())


async def get_integration_secret(
    session: AsyncSession,
    *,
    provider: str,
    config_key: str,
) -> IntegrationSecret | None:
    return await session.scalar(
        select(IntegrationSecret).where(
            IntegrationSecret.provider == provider,
            IntegrationSecret.config_key == config_key,
        )
    )


async def add_integration_secret(
    session: AsyncSession, row: IntegrationSecret
) -> IntegrationSecret:
    session.add(row)
    await session.flush()
    return row


async def upsert_integration_secret(
    session: AsyncSession,
    *,
    provider: str,
    config_key: str,
    value_encrypted: str,
    is_secret: bool,
    enabled: bool = True,
    updated_by: UUID | None = None,
) -> IntegrationSecret:
    existing = await get_integration_secret(
        session, provider=provider, config_key=config_key
    )
    if existing is None:
        from datetime import UTC, datetime

        row = IntegrationSecret(
            provider=provider,
            config_key=config_key,
            value_encrypted=value_encrypted,
            is_secret=is_secret,
            enabled=enabled,
            updated_by=updated_by,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        return await add_integration_secret(session, row)

    existing.value_encrypted = value_encrypted
    existing.is_secret = is_secret
    existing.enabled = enabled
    existing.updated_by = updated_by
    await session.flush()
    return existing
