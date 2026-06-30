# users/tests/conftest.py

import pytest_asyncio
import pytest
from unittest.mock import patch, MagicMock
from faker import Faker
from database.db import get_db
from main import app
from users.models import Users

fake = Faker()


@pytest.fixture
def mock_email_task():
    with patch("tasks.utils.send_email.send") as mock:
        mock_message = MagicMock()
        mock_message.message_id = "test-task-id"
        mock.return_value = mock_message
        yield mock


@pytest.fixture
def user_factory():
    def _create_user_data(**kwargs):
        data = {
            "name": fake.name(),
            "email": fake.email(),
            "password": "SecurePass123!",
        }
        data.update(kwargs)
        return data

    return _create_user_data


@pytest_asyncio.fixture(scope="function")
async def db_session(test_client):
    db_gen = app.dependency_overrides[get_db]()
    session = await db_gen.__anext__()
    yield session
    try:
        await db_gen.aclose()
    except StopAsyncIteration:
        pass


@pytest_asyncio.fixture(scope="function")
async def redis_email_db():
    from redis.asyncio import Redis
    from config import REDIS_HOST, REDIS_PORT, EMAIL_DB

    client = Redis(host=REDIS_HOST, port=REDIS_PORT, db=EMAIL_DB, decode_responses=True)
    yield client
    await client.flushdb()
    await client.aclose()


@pytest_asyncio.fixture(scope="function")
async def auth_client(test_client, db_session, user_factory):
    raw_data = user_factory()
    password = "SecurePass123!"

    user = Users(
        name=raw_data["name"],
        email=raw_data["email"],
        active=True,
    )
    user.password = password
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    login_payload = {"username": user.email, "password": password}
    response = await test_client.post("/auth/login", data=login_payload)
    print("\nSTATUS CODE:", response.status_code)
    print("RESPONSE JSON:", response.json())
    token = response.json()["access_token"]

    test_client.headers.update({"Authorization": f"Bearer {token}"})

    return test_client, user
