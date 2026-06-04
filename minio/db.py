# minio/db.py

import aioboto3
from config import (
    MINIO_ENDPOINT_URL,
    MINIO_ROOT_USER,
    MINIO_ROOT_PASSWORD,
    MINIO_DEFAULT_BUCKET,
)

# 1. Валидация переменных окружения
if not all(
    [MINIO_ENDPOINT_URL, MINIO_ROOT_USER, MINIO_ROOT_PASSWORD, MINIO_DEFAULT_BUCKET]
):
    raise ValueError("Missing MinIO (S3) credentials in .env file")

# 2. Создаем глобальный асинхронный сессионный менеджер aioboto3
s3_session = aioboto3.Session()


# 3. Зависимость для получения s3-клиента в эндпоинтах (аналог get_db)
async def get_s3_client():
    """
    Асинхронный генератор s3-клиента.
    Используется через Depends(get_s3_client) в роутерах.
    """
    async with s3_session.client(
        service_name="s3",
        endpoint_url=MINIO_ENDPOINT_URL,
        aws_access_key_id=MINIO_ROOT_USER,
        aws_secret_access_key=MINIO_ROOT_PASSWORD,
    ) as client:
        yield client


# 4. Инициализатор бакета для lifespan
async def init_s3_storage():
    """
    Проверяет существование дефолтного бакета и создает его, если он отсутствует.
    Запускается строго один раз при старте FastAPI приложения.
    """

    async with s3_session.client(
        service_name="s3",
        endpoint_url=MINIO_ENDPOINT_URL,
        aws_access_key_id=MINIO_ROOT_USER,
        aws_secret_access_key=MINIO_ROOT_PASSWORD,
    ) as client:
        try:
            # Проверяем, существует ли бакет (пытаемся получить его метаданные)
            await client.head_bucket(Bucket=MINIO_DEFAULT_BUCKET)
        except Exception:
            # Если head_bucket бросил исключение, значит бакета нет (или нет прав, но мы админы)
            await client.create_bucket(Bucket=MINIO_DEFAULT_BUCKET)
