# logs/decorators.py

import functools
import inspect
import time
from typing import Any, Callable, TypeVar
import structlog

logger = structlog.get_logger("core.tracker")

F = TypeVar("F", bound=Callable[..., Any])


def trace_action(name: str | None = None) -> Callable[[F], F]:
    """
    Универсальный и адаптивный декоратор для замера времени выполнения бизнес-логики.
    Совместим с современными стандартами Python 3.12+ (без asyncio.iscoroutinefunction).

    Применение:
        @trace_action()
        def my_sync_func(): ...

        @trace_action(name="evaluate_formula_tree")
        async def my_async_func(): ...
    """

    def decorator(func: F) -> F:
        # Автоматически определяем красивое имя для логов, если оно не передано явно
        action_name = name or f"{func.__module__}.{func.__name__}"

        # --- КЕЙС 1: АСИНХРОННАЯ ФУНКЦИЯ (Используем современный inspect) ---
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                log = logger.bind(action=action_name)
                log.info("Action execution started")

                start_time = time.perf_counter()
                try:
                    result = await func(*args, **kwargs)
                    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
                    log.info("Action execution completed", duration_ms=duration_ms)
                    return result
                except Exception as exc:
                    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
                    log.error(
                        "Action execution failed",
                        duration_ms=duration_ms,
                        exc_info=True,
                    )
                    raise exc

            return async_wrapper  # type: ignore

        # --- КЕЙС 2: СИНХРОННАЯ ФУНКЦИЯ ---
        else:

            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                log = logger.bind(action=action_name)
                log.info("Action execution started")

                start_time = time.perf_counter()
                try:
                    result = func(*args, **kwargs)
                    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
                    log.info("Action execution completed", duration_ms=duration_ms)
                    return result
                except Exception as exc:
                    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
                    log.error(
                        "Action execution failed",
                        duration_ms=duration_ms,
                        exc_info=True,
                    )
                    raise exc

            return sync_wrapper  # type: ignore

    return decorator
