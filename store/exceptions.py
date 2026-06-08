# store/exceptions.py

"""Доменные ошибки витрины (task3, ГЗ-1 Этап 2).

Заменяют сырые HTTPException внутри StorefrontService: сервисный слой
не должен знать о HTTP — маппинг в статус-коды живёт в
exceptions/handlers.py (EXCEPTION_STATUS_MAPPING)."""

from typing import Optional
from uuid import UUID

from exceptions.base import BaseAppException


class StorefrontDomainException(BaseAppException):
    """Базовое исключение бизнес-ошибок публичной витрины."""

    error_code = "STOREFRONT_DOMAIN_ERROR"


class StorefrontTemplateNotFoundError(StorefrontDomainException):
    """Запрошенная по ЧПУ-имени таблица не существует в инстансе."""

    status_code = 404
    error_code = "STOREFRONT_TEMPLATE_NOT_FOUND"
    message = "Таблица не найдена"

    def __init__(
        self,
        instance_uuid: UUID,
        template_name: str,
        message: Optional[str] = None,
    ):
        details = {
            "instance_uuid": str(instance_uuid),
            "template_name": template_name,
        }
        super().__init__(message=message or self.message, details=details)


class StorefrontEmptyWritePayloadError(StorefrontDomainException):
    """После применения write-маски и дефолтов не осталось данных для записи."""

    status_code = 400
    error_code = "STOREFRONT_EMPTY_WRITE_PAYLOAD"
    message = "Нет разрешенных полей для записи или значений по умолчанию"

    def __init__(self, template_name: str, message: Optional[str] = None):
        details = {"template_name": template_name}
        super().__init__(message=message or self.message, details=details)
