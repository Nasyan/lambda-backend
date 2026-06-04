# database/db.py

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase
from config import (
    POSTGRES_DB_USER,
    POSTGRES_DB_PASSWORD,
    POSTGRES_DB_HOST,
    POSTGRES_DB_PORT,
    POSTGRES_DB_NAME,
)

if not all(
    [
        POSTGRES_DB_USER,
        POSTGRES_DB_PASSWORD,
        POSTGRES_DB_HOST,
        POSTGRES_DB_PORT,
        POSTGRES_DB_NAME,
    ]
):
    raise ValueError("Missing PostgreSQL credentials in .env file")

DATABASE_URL = (
    f"postgresql+asyncpg://{POSTGRES_DB_USER}:{POSTGRES_DB_PASSWORD}"
    f"@{POSTGRES_DB_HOST}:{POSTGRES_DB_PORT}/{POSTGRES_DB_NAME}"
)


class Base(DeclarativeBase):
    pass


engine = create_async_engine(
    url=DATABASE_URL,
    pool_pre_ping=True,
    echo=True,
)

SessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_db():
    async with SessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()
