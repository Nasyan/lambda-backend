# logs/context.py

from typing import Any
import structlog


# structlog.contextvars позволяет сохранять переменные в контексте текущего асинхронного таска (asyncio.Task)
def bind_log_context(**kwargs: Any) -> None:
    """Привязывает параметры к контексту логов текущего запроса."""
    structlog.contextvars.bind_contextvars(**kwargs)


def unbind_log_context(*keys: str) -> None:
    """Удаляет определенные параметры из контекста логов."""
    structlog.contextvars.unbind_contextvars(*keys)


def clear_log_context() -> None:
    """Полностью очищает контекст логов (например, в конце запроса)."""
    structlog.contextvars.clear_contextvars()
