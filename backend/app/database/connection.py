from collections.abc import AsyncGenerator

from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings


DATABASE_URL = URL.create(
    drivername="postgresql+asyncpg",
    username=settings.DB_USER,
    password=settings.DB_PASSWORD,
    host=settings.DB_HOST,
    port=settings.DB_PORT,
    database=settings.DB_NAME
)


engine = create_async_engine(
    DATABASE_URL,
    echo=settings.DB_ECHO,
    pool_pre_ping=True
)


# Reusable asynchronous session factory.
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Provide an asynchronous database session to FastAPI dependencies."""

    async with AsyncSessionLocal() as session:
        yield session
