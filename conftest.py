# conftest.py

"""Корневая тестовая инфраструктура (task3, ГЗ-3 Фаза 1).

Архитектура:
- Подключения (scope=session): postgres_engine / mongo_client / redis_pool
  создаются ОДИН раз за прогон. Схема Postgres создаётся один раз —
  никаких drop_all/create_all на каждый тест.
- Изоляция данных (scope=function): db_session и приложение работают в ОДНОЙ
  внешней транзакции на выделенном соединении (savepoint-режим SQLAlchemy);
  после теста — ROLLBACK. Mongo чистится быстрым delete_many по коллекциям
  (база уникальна per xdist-воркер). Redis — flushdb лёгких тестовых БД.
- Изолированные клиенты: test_client переопределяет ТОЛЬКО get_db и
  get_mongo_db; S3 подмешивается отдельной фикстурой minio_client.
  Чистая логика (engine/tests, юниты AST) не запрашивает эти фикстуры и
  не поднимает ни Postgres, ни Redis.
- Конкурентность: для тестов гонок есть concurrent_test_client — реальные
  независимые соединения из пула (gather работает), изоляция через TRUNCATE
  после теста.

Требование: pytest-asyncio >= 0.24 (loop_scope). Все async-тесты автоматически
переводятся в session-петлю (pytest_collection_modifyitems), чтобы
session-scoped движки и function-тесты жили в одном event loop —
иначе asyncpg ломается на кросс-loop соединениях.
"""

import asyncio
import os
import sys

import aioboto3
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from motor.motor_asyncio import AsyncIOMotorClient

from sqlalchemy.ext.compiler import compiles
from sqlalchemy.schema import DropTable

from database.db import Base, get_db
from mongo.db import get_mongo_db
from main import app
from redisdb.utils import init_redis, redis_clients
import config as cfg
from faker import Faker
from minio.db import get_s3_client
from uuid import uuid4


from users.models import Users, Instances, UserRole, UserPermissions

from jsonwebtoken.utils import encode_jwt

fake = Faker()


if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def pytest_collection_modifyitems(items):
    """Все async-тесты — в session-петлю (общую с session-scoped движками)."""
    try:
        from pytest_asyncio import is_async_test
    except ImportError:  # pragma: no cover - очень старый pytest-asyncio
        return

    session_marker = pytest.mark.asyncio(loop_scope="session")
    for item in items:
        if is_async_test(item):
            item.add_marker(session_marker, append=False)


# 🔥 ПРАВИЛЬНЫЙ ХУК: Переопределяем компиляцию DROP TABLE для всех диалектов в тестах
@compiles(DropTable)
def compile_drop_table_cascade(element, compiler, **kw):
    """Принудительно добавляет CASCADE к какому угодно DROP TABLE в тестах"""
    return compiler.visit_drop_table(element) + " CASCADE"


postgres_url = (
    f"postgresql+asyncpg://{cfg.POSTGRES_DB_USER}:{cfg.POSTGRES_DB_PASSWORD}@"
    f"{cfg.POSTGRES_TEST_DB_HOST}:{cfg.POSTGRES_TEST_DB_PORT}/{cfg.POSTGRES_TEST_DB_NAME}"
)

_XDIST_WORKER = os.environ.get("PYTEST_XDIST_WORKER", "main")


# =============================================================================
# ПОДКЛЮЧЕНИЯ — scope=session (создаются один раз за прогон)
# =============================================================================


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def postgres_engine():
    """Движок Postgres на всю сессию. Схема создаётся ОДИН раз."""
    engine = create_async_engine(postgres_url, echo=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    yield engine
    await engine.dispose()


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def mongo_client():
    """Клиент Mongo на всю сессию; тестовая база своя на каждого xdist-воркера."""
    mongo_test_url = (
        f"mongodb://{cfg.ADMIN_USERNAME}:{cfg.ADMIN_PASSWORD}@"
        f"{cfg.MONGO_HOST}:{cfg.MONGO_TEST_PORT}/?authSource=admin"
    )
    client = AsyncIOMotorClient(mongo_test_url)

    db_name = _mongo_db_name()
    if db_name not in ["admin", "local", "config"]:
        await client.drop_database(db_name)

    yield client
    client.close()


def _mongo_db_name() -> str:
    return f"{cfg.MONGO_DB_NAME}_{_XDIST_WORKER}"


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def redis_pool():
    """Инициализация redis-клиентов приложения один раз за сессию."""
    await init_redis()
    yield redis_clients


# =============================================================================
# ИЗОЛЯЦИЯ ДАННЫХ — scope=function (транзакция + ROLLBACK, быстрые очистки)
# =============================================================================


@pytest_asyncio.fixture(loop_scope="session")
async def pg_session_factory(postgres_engine):
    """Соединение с внешней транзакцией на тест.

    Все сессии из этой фабрики (и db_session теста, и сессии приложения через
    override get_db) живут в ОДНОЙ транзакции на одном соединении:
    session.commit() внутри == release SAVEPOINT, а финальный rollback
    отбрасывает всё — таблицы не пересоздаются и не чистятся.
    """
    async with postgres_engine.connect() as connection:
        transaction = await connection.begin()

        factory = async_sessionmaker(
            bind=connection,
            class_=AsyncSession,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )

        yield factory

        await transaction.rollback()


@pytest_asyncio.fixture(loop_scope="session")
async def db_session(pg_session_factory):
    """Сессия для прямой работы с Postgres в тесте (та же транзакция, что у API)."""
    async with pg_session_factory() as session:
        yield session


@pytest_asyncio.fixture(loop_scope="session")
async def mongo_db(mongo_client):
    """Чистая Mongo-база на тест: быстрый delete_many вместо drop_database.

    Индексы коллекций сохраняются (их создание дорогое); данные вычищаются
    перед тестом, чтобы каждый тест стартовал с детерминированного состояния.
    """
    db = mongo_client[_mongo_db_name()]

    collection_names = await db.list_collection_names()
    for name in collection_names:
        await db[name].delete_many({})

    yield db


@pytest_asyncio.fixture(loop_scope="session")
async def redis_clean(redis_pool):
    """Очистка тестовых Redis-БД перед тестом (анти-коллизии rate-limit'ов)."""
    for client in redis_pool.values():
        try:
            await client.flushdb()
        except Exception:  # pragma: no cover - redis может быть не поднят для юнитов
            pass
    yield redis_pool


# =============================================================================
# КЛИЕНТЫ ПРИЛОЖЕНИЯ
# =============================================================================


@pytest_asyncio.fixture(loop_scope="session")
async def test_client(pg_session_factory, mongo_db, redis_clean):
    """HTTP-клиент приложения с точечными overrides (get_db, get_mongo_db).

    Postgres-изоляция — транзакция pg_session_factory (ROLLBACK после теста),
    Mongo — очищенная база per-test, Redis — flushdb. S3 НЕ поднимается —
    тестам с файлами нужен minio_client.
    """

    async def override_get_db():
        async with pg_session_factory() as session:
            yield session

    async def override_get_mongo_db():
        yield mongo_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_mongo_db] = override_get_mongo_db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client

    app.dependency_overrides.clear()


# Совместимость с новым неймингом из ГЗ-3 (и более говорящее имя для новых тестов)
@pytest_asyncio.fixture(loop_scope="session")
async def async_client(test_client):
    yield test_client


@pytest_asyncio.fixture(loop_scope="session")
async def concurrent_test_client(postgres_engine, mongo_db, redis_clean):
    """Клиент для тестов КОНКУРЕНТНОСТИ (asyncio.gather реальных запросов).

    Транзакционная изоляция на одном соединении сериализует запросы, поэтому
    здесь приложение получает обычные пуловые сессии (реальный параллелизм),
    а изоляция достигается TRUNCATE всех таблиц после теста.
    """
    factory = async_sessionmaker(postgres_engine, expire_on_commit=False)

    async def override_get_db():
        async with factory() as session:
            yield session

    async def override_get_mongo_db():
        yield mongo_db

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_mongo_db] = override_get_mongo_db

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client

    app.dependency_overrides.clear()

    # Пост-изоляция: реальные коммиты в пуле -> вычищаем таблицы
    async with postgres_engine.begin() as conn:
        table_names = ", ".join(
            f'"{table.name}"' for table in reversed(Base.metadata.sorted_tables)
        )
        if table_names:
            await conn.execute(text(f"TRUNCATE TABLE {table_names} CASCADE"))


@pytest_asyncio.fixture(loop_scope="session")
async def minio_client(test_client):
    """
    Расширение базового клиента для тестов, которым нужен MinIO (S3).
    Сюда подмешивается очистка бакетов и оверрайд зависимости S3-клиента.
    """
    test_s3_endpoint = f"http://127.0.0.1:{cfg.MINIO_TEST_PORT}"
    s3_session = aioboto3.Session()

    # --- НАСТРОЙКА MINIO (Очистка и создание бакета) ---
    async with s3_session.client(
        service_name="s3",
        endpoint_url=test_s3_endpoint,
        aws_access_key_id=cfg.MINIO_ROOT_USER,
        aws_secret_access_key=cfg.MINIO_ROOT_PASSWORD,
    ) as s3_client:
        try:
            # S3 не позволяет удалить непустой бакет, поэтому сначала чистим файлы
            response = await s3_client.list_objects_v2(Bucket=cfg.MINIO_DEFAULT_BUCKET)
            if "Contents" in response:
                objects_to_delete = [
                    {"Key": obj["Key"]} for obj in response["Contents"]
                ]
                await s3_client.delete_objects(
                    Bucket=cfg.MINIO_DEFAULT_BUCKET,
                    Delete={"Objects": objects_to_delete},
                )
            await s3_client.delete_bucket(Bucket=cfg.MINIO_DEFAULT_BUCKET)
        except Exception:
            # Если бакета не было — просто игнорируем
            pass

        # Создаем чистый бакет для текущего теста
        await s3_client.create_bucket(Bucket=cfg.MINIO_DEFAULT_BUCKET)

    # --- ДОПОЛНИТЕЛЬНЫЙ ОВЕРРАЙД ---
    async def override_get_s3_client():
        async with s3_session.client(
            service_name="s3",
            endpoint_url=test_s3_endpoint,
            aws_access_key_id=cfg.MINIO_ROOT_USER,
            aws_secret_access_key=cfg.MINIO_ROOT_PASSWORD,
        ) as client:
            yield client

    # Добавляем оверрайд S3 поверх уже существующих в приложении
    app.dependency_overrides[get_s3_client] = override_get_s3_client

    # Возвращаем тот же httpx клиент, но приложение теперь умеет работать с S3
    yield test_client


# =============================================================================
# ФАБРИКИ ДАННЫХ (Postgres-сущности)
# =============================================================================


@pytest_asyncio.fixture(loop_scope="session")
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
    response = await test_client.post("/auth/login/", data=login_payload)
    token = response.json()["access_token"]

    test_client.headers.update({"Authorization": f"Bearer {token}"})

    return test_client, user


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


@pytest_asyncio.fixture(loop_scope="session")
async def create_test_environment(pg_session_factory):
    """
    Фабрика для создания тестового пользователя и инстанса в БД Postgres.
    Работает в той же транзакции, что и приложение (pg_session_factory),
    поэтому данные видны API без реального коммита в базу.
    """

    async def _setup(
        role: UserRole = UserRole.CREATOR,
        user_active: bool = True,
        instance_active: bool = True,
        custom_instance_id: str = None,
    ):
        async with pg_session_factory() as session:
            user_uuid = str(uuid4())
            instance_uuid = custom_instance_id or str(uuid4())

            # Если инстанс с таким UUID уже не был создан ранее в рамках этого же теста
            if not custom_instance_id:
                test_instance = Instances(
                    uuid=instance_uuid,
                    title=f"Тестовая Компания {instance_uuid[:8]}",
                    active=instance_active,
                )
                session.add(test_instance)

            test_user = Users(
                uuid=user_uuid,
                email=f"user_{user_uuid[:8]}@test.com",
                hash_password="mocked_password_hash",  # Фикс NotNullViolationError
                role=role,
                active=user_active,
                instance_id=instance_uuid,
            )
            session.add(test_user)
            await session.commit()

            # Генерируем заголовки авторизации
            token = encode_jwt({"sub": user_uuid})
            headers = {"Authorization": f"Bearer {token}"}

            return user_uuid, instance_uuid, headers

    return _setup


@pytest_asyncio.fixture(loop_scope="session")
async def create_committed_environment(postgres_engine):
    """Окружение с РЕАЛЬНЫМ коммитом в Postgres — для concurrent_test_client.

    Обычное create_test_environment живёт в транзакции теста, которую пуловые
    сессии конкурентного клиента не видят. Эта фабрика коммитит по-настоящему;
    очистку делает TRUNCATE в концовке concurrent_test_client.
    """
    factory = async_sessionmaker(postgres_engine, expire_on_commit=False)

    async def _setup(role: UserRole = UserRole.CREATOR):
        async with factory() as session:
            user_uuid = str(uuid4())
            instance_uuid = str(uuid4())

            session.add(
                Instances(
                    uuid=instance_uuid,
                    title=f"Concurrent Компания {instance_uuid[:8]}",
                    active=True,
                )
            )
            session.add(
                Users(
                    uuid=user_uuid,
                    email=f"user_{user_uuid[:8]}@test.com",
                    hash_password="mocked_password_hash",
                    role=role,
                    active=True,
                    instance_id=instance_uuid,
                )
            )
            await session.commit()

        token = encode_jwt({"sub": user_uuid})
        return user_uuid, instance_uuid, {"Authorization": f"Bearer {token}"}

    return _setup


@pytest_asyncio.fixture(loop_scope="session")
async def test_instance(db_session):
    """Фикстура для создания активного инстанса магазина."""
    instance = Instances(title="Client Storefront Automation", active=True)
    db_session.add(instance)
    await db_session.commit()
    await db_session.refresh(instance)
    return instance


@pytest_asyncio.fixture(loop_scope="session")
async def setup_catalog_template(test_client, create_test_environment):
    """
    Разворачивает изолированное окружение инстанса и создает базовый
    шаблон "Товары" со строковым полем 'title' и числовым 'price'.
    """
    user_uuid, instance_uuid, headers = await create_test_environment()

    schema = {
        "title": {"type": "string", "required": True},
        "price": {"type": "number", "required": False},
    }

    response = await test_client.post(
        f"/instances/{instance_uuid}/templates",
        json={"name": "Товары", "schema": schema},
        headers=headers,
    )
    assert response.status_code == 201
    template_uuid = response.json()["_id"]

    return {
        "instance_uuid": instance_uuid,
        "template_uuid": template_uuid,
        "headers": headers,
        "base_url": f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
    }


@pytest_asyncio.fixture
def crm_template_factory(test_client, create_test_environment):
    """
    Фабрика для генерации изолированного окружения и создания шаблонов с динамической схемой.
    """

    async def _create_template(name="Динамический шаблон", schema=None):
        user_uuid, instance_uuid, headers = await create_test_environment()

        if schema is None:
            schema = {
                "title": {"type": "string", "required": True},
                "price": {"type": "number", "required": False},
            }

        response = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json={"name": name, "schema": schema},
            headers=headers,
        )
        assert response.status_code == 201
        template_uuid = response.json()["_id"]

        return {
            "instance_uuid": instance_uuid,
            "template_uuid": template_uuid,
            "headers": headers,
            "base_url": f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
        }

    return _create_template


@pytest_asyncio.fixture
def employee_factory(db_session):
    """
    Фабрика для создания пользователя в БД Postgres с привязкой к инстансу
    и выдачей прав на конкретный инструмент (AppTool).
    """

    async def _create_employee(instance_uuid, tool_name: str):
        employee_uuid = uuid4()

        db_session.add(
            Users(
                uuid=employee_uuid,
                email=f"employee_{uuid4().hex[:6]}@test.com",
                hash_password="mock_password_hash_for_tests",
                role=UserRole.USER,
                active=True,
                instance_id=instance_uuid,
            )
        )
        db_session.add(
            UserPermissions(
                user_uuid=employee_uuid,
                allowed_tools=[tool_name],
            )
        )
        await db_session.commit()

        token = encode_jwt(payload={"sub": str(employee_uuid)})
        return {"Authorization": f"Bearer {token}"}

    return _create_employee


@pytest_asyncio.fixture
def crm_environment_factory(db_session):
    """
    Фабрика для генерации изолированного бизнес-пространства с Владельцем (CREATOR).
    Возвращает UUID инстанса, заголовки владельца и хелпер для добавления сотрудников.
    """

    async def _setup_env():
        instance_uuid = uuid4()
        creator_uuid = uuid4()

        # 1. Создаем инстанс
        db_session.add(
            Instances(
                uuid=instance_uuid,
                title=f"Бизнес Пространство {uuid4().hex[:4]}",
                active=True,
            )
        )

        # 2. Создаем Создателя (Владельца) инстанса
        db_session.add(
            Users(
                uuid=creator_uuid,
                name="Иван Владелец",
                email=f"creator_{uuid4().hex[:6]}@test.com",
                hash_password="mock_password_hash_for_tests",
                role=UserRole.CREATOR,
                active=True,
                instance_id=instance_uuid,
            )
        )
        await db_session.commit()

        creator_token = encode_jwt(payload={"sub": str(creator_uuid)})
        creator_headers = {"Authorization": f"Bearer {creator_token}"}

        # Хелпер для быстрого добавления сотрудников в рамках этого же инстанса
        async def add_employee(role: UserRole, allowed_tools: list):
            emp_uuid = uuid4()
            db_session.add(
                Users(
                    uuid=emp_uuid,
                    name="Сотрудник",
                    email=f"worker_{uuid4().hex[:6]}@test.com",
                    hash_password="mock_password_hash_for_tests",
                    role=role,
                    active=True,
                    instance_id=instance_uuid,
                )
            )
            db_session.add(
                UserPermissions(user_uuid=emp_uuid, allowed_tools=allowed_tools)
            )
            await db_session.commit()

            token = encode_jwt(payload={"sub": str(emp_uuid)})
            return {"Authorization": f"Bearer {token}"}

        return {
            "instance_uuid": instance_uuid,
            "creator_headers": creator_headers,
            "add_employee": add_employee,
        }

    return _setup_env
