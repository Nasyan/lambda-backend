# mongo/tests/conftest.py

import pytest_asyncio
from mongo.db import get_mongo_db
from main import app


@pytest_asyncio.fixture(scope="function")
async def mongo_db(test_client):
    """
    Автоматически перехватывает тестовую базу данных Mongo
    из переопределенных зависимостей FastAPI приложения.
    """
    override_func = app.dependency_overrides[get_mongo_db]
    async for db in override_func():
        yield db
