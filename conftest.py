import asyncio
import sys
import aioboto3
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from motor.motor_asyncio import AsyncIOMotorClient

from sqlalchemy.ext.compiler import compiles
from sqlalchemy.schema import DropTable

from database.db import Base, get_db
from mongo.db import get_mongo_db
from main import app, init_redis
import config as cfg
from faker import Faker
from minio.db import get_s3_client
from uuid import uuid4

# ИМПОРТЫ МОДЕЛЕЙ ДЛЯ РЕГИСТРАЦИИ В Base.metadata
from users.models import Users, Instances, UserRole

from jsonwebtoken.utils import encode_jwt

fake = Faker()


if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


# 🔥 ПРАВИЛЬНЫЙ ХУК: Переопределяем компиляцию DROP TABLE для всех диалектов в тестах
@compiles(DropTable)
def compile_drop_table_cascade(element, compiler, **kw):
    """Принудительно добавляет CASCADE к какому угодно DROP TABLE в тестах"""
    return compiler.visit_drop_table(element) + " CASCADE"


postgres_url = (
    f"postgresql+asyncpg://{cfg.POSTGRES_DB_USER}:{cfg.POSTGRES_DB_PASSWORD}@"
    f"{cfg.POSTGRES_TEST_DB_HOST}:{cfg.POSTGRES_TEST_DB_PORT}/{cfg.POSTGRES_TEST_DB_NAME}"
)


@pytest_asyncio.fixture(scope="function")
async def test_client():
    # --- НАСТРОЙКА MONGODB ---
    mongo_test_url = (
        f"mongodb://{cfg.ADMIN_USERNAME}:{cfg.ADMIN_PASSWORD}@"
        f"{cfg.MONGO_HOST}:{cfg.MONGO_TEST_PORT}/?authSource=admin"
    )
    mongo_client = AsyncIOMotorClient(mongo_test_url)

    db_name = cfg.MONGO_DB_NAME
    if db_name not in ["admin", "local", "config"]:
        await mongo_client.drop_database(db_name)

    test_mongo_db = mongo_client[db_name]

    # --- НАСТРОЙКА REDIS ---
    await init_redis()

    # --- НАСТРОЙКА POSTGRES ---
    test_engine = create_async_engine(postgres_url, echo=False)

    async with test_engine.begin() as conn:
        # Теперь drop_all выполнит команды вида: DROP TABLE "users" CASCADE;
        # Любые связи из неимпортированных в текущем тесте модулей будут проигнорированы Постгресом.
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    SessionLocal = async_sessionmaker(
        test_engine,
        expire_on_commit=False,
    )

    # --- ЗАВИСИМОСТИ-ОВЕРРАЙДЫ ---
    async def override_get_db():
        async with SessionLocal() as session:
            yield session

    async def override_get_mongo_db():
        yield test_mongo_db

    # Внедряем базовые оверрайды в приложение
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_mongo_db] = override_get_mongo_db

    # --- ЗАПУСК КЛИЕНТА ---
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client

    # --- ОЧИСТКА ПОСЛЕ ТЕСТА ---
    app.dependency_overrides.clear()
    await test_engine.dispose()
    mongo_client.close()


@pytest_asyncio.fixture(scope="function")
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


@pytest_asyncio.fixture(scope="function")
async def db_session():
    """
    Фикстура для прямой работы с Postgres в тестах.
    Создает engine внутри текущего loop, изолирует транзакцию,
    а после теста автоматически чистит коннекты.
    """
    # Создаем движок строго внутри контекста выполняющегося теста
    engine = create_async_engine(postgres_url, echo=False)
    TestingSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

    # Отдаем сессию в тест
    async with TestingSessionLocal() as session:
        yield session

    # Код ниже выполнится АВТОМАТИЧЕСКИ после завершения теста
    await engine.dispose()


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


@pytest_asyncio.fixture(scope="function")
async def create_test_environment():
    """
    Фабрика для создания тестового пользователя и инстанса в БД Postgres.
    Автоматически учитывает обязательные поля 'title' и 'hash_password'.
    """

    async def _setup(
        role: UserRole = UserRole.CREATOR,
        user_active: bool = True,
        instance_active: bool = True,
        custom_instance_id: str = None,
    ):
        get_db_override = app.dependency_overrides[get_db]

        async for session in get_db_override():
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


@pytest_asyncio.fixture(scope="function")
async def test_instance(db_session):
    """Фикстура для создания активного инстанса магазина."""
    instance = Instances(title="Client Storefront Automation", active=True)
    db_session.add(instance)
    await db_session.commit()
    await db_session.refresh(instance)
    return instance
