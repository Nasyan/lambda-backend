# minio/exceptions/service.py

from typing import Optional
from exceptions.base import BaseAppException


class StorageDomainException(BaseAppException):
    """Базовое исключение для всех операций с объектным хранилищем (S3/MinIO)."""

    error_code = "STORAGE_DOMAIN_ERROR"


class StorageInfrastructureError(StorageDomainException):
    """Выбрасывается, когда S3-клиент не может связаться с MinIO (сеть, неверные креды, бакет не существует)."""

    error_code = "STORAGE_INFRASTRUCTURE_UNAVAILABLE"
    message = "Ошибка взаимодействия с удаленным файловым хранилищем."

    def __init__(self, message: Optional[str] = None, raw_error: Optional[str] = None):
        details = {"raw_error": raw_error} if raw_error else {}
        super().__init__(message=message or self.message, details=details)


class StorageFileNotFoundError(StorageDomainException):
    """Выбрасывается, когда запрашиваемый по file_path объект отсутствует в бакете."""

    error_code = "STORAGE_FILE_NOT_FOUND"
    message = "Указанный файл не найден в хранилище данных."

    def __init__(self, file_path: str):
        details = {"file_path": file_path}
        super().__init__(
            message=f"Файл по пути '{file_path}' отсутствует или был удален.",
            details=details,
        )


class StorageURLGenerationError(StorageDomainException):
    """Выбрасывается, если произошел сбой генерации пресайнед ссылки."""

    error_code = "STORAGE_URL_GENERATION_FAILED"
    message = "Не удалось сгенерировать безопасную ссылку для файла."
