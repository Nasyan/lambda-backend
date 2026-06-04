# core/tests/conftest.py

import pytest_asyncio
from uuid import uuid4
from database.db import get_db
from main import app
from users.models import Users, Instances, UserRole
from jsonwebtoken.utils import encode_jwt
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
import config as cfg


def get_test_session_maker():
    """Помощник для создания фабрики сессий внутри текущего event loop теста"""
    postgres_url = (
        f"postgresql+asyncpg://{cfg.POSTGRES_DB_USER}:{cfg.POSTGRES_DB_PASSWORD}@"
        f"{cfg.POSTGRES_TEST_DB_HOST}:{cfg.POSTGRES_TEST_DB_PORT}/{cfg.POSTGRES_TEST_DB_NAME}"
    )
    # Создаем engine строго внутри выполняющегося таска, чтобы избежать конфликта петель asyncio
    engine = create_async_engine(postgres_url, echo=False)
    return async_sessionmaker(engine, expire_on_commit=False), engine
