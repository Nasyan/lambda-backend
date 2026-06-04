# minio/dependencies.py

from fastapi import Depends
from minio.db import get_s3_client  # Твой генератор клиента из прошлого сообщения
from minio.service import S3StorageService


async def get_s3_service(s3_client=Depends(get_s3_client)) -> S3StorageService:
    """Зависимость, которая поставляет готовый сервис в эндпоинты."""
    return S3StorageService(s3_client)
