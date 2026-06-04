# logs/middleware.py

import time
import uuid
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp
import structlog

from logs.context import bind_log_context, clear_log_context

logger = structlog.get_logger("core.middleware")


class StructuredLoggingMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        # Очищаем контекст от прошлых запросов на всякий случай
        clear_log_context()

        # 1. Извлекаем или генерируем Trace ID (Request ID)
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())

        # Попробуем вытащить инстанс теннанта прямо из URL (/instances/{instance_uuid}/...)
        path_parts = request.url.path.split("/")
        instance_uuid = None
        try:
            # Ищем маркер "instances" в пути и берем следующий за ним сегмент
            if "instances" in path_parts:
                idx = path_parts.index("instances")
                if idx + 1 < len(path_parts):
                    instance_uuid = path_parts[idx + 1]
        except Exception:
            pass

        # 2. Намертво привязываем базовый контекст к текущему асинхронному таску
        bind_log_context(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
            ip=request.client.host if request.client else "unknown",
        )
        if instance_uuid:
            bind_log_context(instance_uuid=instance_uuid)

        logger.info("Incoming HTTP request started")

        start_time = time.perf_counter()
        try:
            response: Response = await call_next(request)
            process_time = time.perf_counter() - start_time

            # Добавляем заголовки трассировки в ответ клиенту (полезно для отладки)
            response.headers["X-Request-ID"] = request_id

            # Логируем успешное или контролируемое завершение запроса
            logger.info(
                "HTTP request completed",
                status_code=response.status_code,
                duration_ms=round(process_time * 1000, 2),
            )
            return response

        except Exception as exc:
            # Если произошла непредвиденная паника (Код 500), логируем ее со всем контекстом и эксепшеном
            process_time = time.perf_counter() - start_time
            logger.critical(
                "HTTP request crashed unhandled",
                duration_ms=round(process_time * 1000, 2),
                exc_info=True,  # Автоматически распарсит и приложит стек ошибки
            )
            raise exc
        finally:
            # В самом конце очищаем контекст таска, чтобы не было утечек данных между запросами
            clear_log_context()
