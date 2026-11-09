"""Helpers for detecting whether pgvector storage is usable on a session."""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def is_semantic_storage_available(session: AsyncSession) -> bool:
    """Return True only when the session is PostgreSQL with pgvector enabled.

    SQLite test databases intentionally return False: they may carry the
    portable vector column for unit tests, but automatic indexing and
    PostgreSQL vector operators require a real pgvector installation.
    """

    try:
        bind = session.get_bind()

        if bind is None or bind.dialect.name != "postgresql":
            return False

        result = await session.execute(
            text(
                "SELECT 1 FROM pg_extension WHERE extname = 'vector'"
            )
        )

        return result.scalar_one_or_none() == 1

    except Exception:
        return False
