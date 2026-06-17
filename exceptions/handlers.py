# exceptions/handlers.py

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from exceptions.base import BaseAppException

# Оставляем маппинг только для тех редких исключений,
# у которых статус-кода НЕТ внутри самого класса.
EXCEPTION_STATUS_MAPPING = {}


def resolve_exception_status_code(exc: BaseAppException) -> int:
    """Динамически определяет HTTP статус-код исключения."""
    # 1. Проверяем, задан ли статус-код в самом объекте/классе исключения
    status_code = getattr(exc, "status_code", None)
    if status_code is not None:
        return int(status_code)

    # 2. Проверяем по резервному словарю (если останутся внешние исключения)
    for exc_class, mapped_status in EXCEPTION_STATUS_MAPPING.items():
        if isinstance(exc, exc_class):
            return mapped_status

    # Fallback по умолчанию
    return status.HTTP_500_INTERNAL_SERVER_ERROR


async def app_exception_handler(
    request: Request, exc: BaseAppException
) -> JSONResponse:
    """Перехватчик контролируемых бизнес-исключений платформы."""
    status_code = resolve_exception_status_code(exc)

    return JSONResponse(
        status_code=status_code,
        content={
            "error_code": getattr(exc, "error_code", "BUSINESS_LOGIC_ERROR"),
            "message": getattr(exc, "message", "Произошла ошибка бизнес-логики."),
            "details": getattr(exc, "details", {}),
        },
    )


async def universal_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Глобальный "улавливатель" непредвиденных системных падений (Panic/500)."""
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error_code": "INTERNAL_SERVER_ERROR",
            "message": "На сервере произошла критическая ошибка. Инженеры уже уведомлены.",
            "details": {},
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Функция-регистратор для подключения в main.py."""
    app.add_exception_handler(BaseAppException, app_exception_handler)
    app.add_exception_handler(Exception, universal_exception_handler)
