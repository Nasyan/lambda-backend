# logs/config.py

import logging.config
import sys
import structlog
from typing import Literal


def setup_logging(env: Literal["development", "production"] = "development") -> None:
    """
    Настройка структурированного логирования.
    Если env="production", логи выводятся в формате JSON.
    Если env="development", логи выводятся в красивом цветном текстовом формате.
    """

    # Общесистемные процессоры для structlog
    shared_processors = [
        # Вливает переменные из contextvars (наш Trace ID, instance_id и т.д.)
        structlog.contextvars.merge_contextvars,
        # Добавляет уровень лога (INFO, ERROR)
        structlog.stdlib.add_log_level,
        # Добавляет имя логгера
        structlog.stdlib.add_logger_name,
        # Корректно форматирует exception info (stack trace), если передан exc_info
        structlog.processors.format_exc_info,
        # Добавляет таймстамп в формате ISO 8601
        structlog.processors.TimeStamper(fmt="iso"),
        # Позволяет использовать позиционные аргументы в логах как в стандартном logging
        structlog.stdlib.PositionalArgumentsFormatter(),
        # Рендерит информацию о стеке, если необходимо
        structlog.processors.StackInfoRenderer(),
        # Декодирует unicode символы
        structlog.processors.UnicodeDecoder(),
    ]

    # Выбираем финальный рендерер в зависимости от окружения
    if env == "prod":
        # Для прода — чистый JSON, идеальный для векторных сборщиков (Vector/FluentBit)
        formatter_renderer = structlog.processors.JSONRenderer()
    else:
        # Для разработки — красивый, подсвеченный консольный вывод
        formatter_renderer = structlog.dev.ConsoleRenderer(colors=True)

    # Настройка стандартной библиотеки logging, чтобы перехватывать логи сторонних библиотек
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "structlog_formatter": {
                    # КРИТИЧЕСКИ ВАЖНО: Передаем сам класс БЕЗ кавычек
                    "()": structlog.stdlib.ProcessorFormatter,
                    "processor": formatter_renderer,
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "stream": sys.stdout,
                    "formatter": "structlog_formatter",
                },
            },
            "loggers": {
                # Укрощаем уввикорн и сторонние библиотеки, перенаправляя их в наш хэндлер
                "": {"handlers": ["console"], "level": "INFO"},
                "uvicorn": {
                    "handlers": ["console"],
                    "level": "INFO",
                    "propagate": False,
                },
                "uvicorn.error": {
                    "handlers": ["console"],
                    "level": "INFO",
                    "propagate": False,
                },
                "uvicorn.access": {
                    "handlers": ["console"],
                    "level": "INFO",
                    "propagate": False,
                },
                "sqlalchemy.engine": {
                    "handlers": ["console"],
                    "level": "WARNING",
                    "propagate": False,
                },
            },
        }
    )

    # Настройка самого structlog
    structlog.configure(
        processors=shared_processors
        + [
            # Готовит лог к передаче в стандартный logging
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )
