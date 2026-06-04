# health/views.py

import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

import asyncpg
from motor.motor_asyncio import AsyncIOMotorDatabase

from database.db import get_db
from health.schemas import PulseSchema, ExistsTables
from health.exceptions import (
    CantConnectMinioHttpException,
    CantConnectMongoHttpException,
    CantConnectPostgresHttpException,
    CantCheckMigrationsHttpException,
    CantConnectRedisHttpException,
)
from mongo.db import get_mongo_db
from redisdb.utils import redis_clients
from minio.db import get_s3_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/health", tags=["health"])


@router.get("/pulse/", response_model=PulseSchema)
async def check_pulse():
    logger.info("Pulse check initiated")
    return PulseSchema(description="Fastapi alive")


@router.get("/db/", response_model=PulseSchema)
async def check_db(db: AsyncSession = Depends(get_db)):
    try:
        logger.info("[postgres_check] Checking database connection...")

        result = await db.execute(text("SELECT 1"))
        result.scalar()

        logger.info("[postgres_check] Database check successful")
        return PulseSchema(description="Postgres alive")

    except (DBAPIError, SQLAlchemyError) as e:
        orig = getattr(e, "orig", None)

        if isinstance(orig, asyncpg.ConnectionDoesNotExistError):
            detail = "Connection closed unexpectedly. Port/PID conflict?"
        else:
            detail = f"Database error: {str(e)}"

        logger.exception(
            "[postgres_check] Postgres connection failed: %s",
            detail,
        )
        raise CantConnectPostgresHttpException(e=detail)

    except Exception as e:
        logger.exception("[postgres_check] Unexpected error during DB check")
        raise CantConnectPostgresHttpException(e=str(e))


@router.get("/redis/", response_model=PulseSchema)
async def check_redis():
    try:
        logger.info("[redis_check] Pinging all Redis databases...")

        for name, client in redis_clients.items():
            logger.info("[redis_check] Pinging Redis client: %s", name)
            await client.ping()

        logger.info("[redis_check] Redis check successful")
        return PulseSchema(description="Redis alive")

    except Exception as e:
        logger.exception("[redis_check] Redis connection failed")
        raise CantConnectRedisHttpException(e=str(e))


@router.get("/migrations/", response_model=ExistsTables)
async def check_migrations(db: AsyncSession = Depends(get_db)):
    try:
        logger.info("[migrations_check] Fetching public tables list")

        query = text("""
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public'
            AND table_type = 'BASE TABLE';
        """)

        result = await db.execute(query)
        table_names = [row[0] for row in result.fetchall()]

        logger.info(
            "[migrations_check] Tables found: count=%s tables=%s",
            len(table_names),
            table_names,
        )

        return ExistsTables(tables=table_names)

    except Exception as e:
        logger.exception("[migrations_check] Failed to check migrations")
        raise CantCheckMigrationsHttpException(e=str(e))


@router.get("/mongo/", response_model=PulseSchema)
async def check_mongo(
    db: AsyncIOMotorDatabase = Depends(get_mongo_db),
):
    try:
        await db.command("ping")

        logger.info("[mongo_pulse] Mongo is alive")
        return PulseSchema(description="MongoDB alive")

    except Exception as e:
        logger.exception("[mongo_pulse] Mongo pulse check failed")
        raise CantConnectMongoHttpException(e=str(e))


@router.get("/minio/", response_model=PulseSchema)
async def check_minio(s3_client=Depends(get_s3_client)):
    try:
        logger.info("[minio_pulse] Checking MinIO connection...")

        await s3_client.list_buckets()

        logger.info("[minio_pulse] MinIO is alive")
        return PulseSchema(description="MinIO alive")

    except Exception as e:
        logger.exception("[minio_pulse] MinIO pulse check failed")
        raise CantConnectMinioHttpException(e=str(e))
