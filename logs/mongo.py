import time
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Optional, TypeVar

import structlog

logger = structlog.get_logger("mongo.query")

T = TypeVar("T")

MAX_LOGGED_ITEMS = 5
MAX_LOGGED_STRING = 120


def start_mongo_timer() -> float:
    return time.perf_counter()


async def execute_logged_mongo_call(
    collection: Any,
    operation: str,
    query: Any,
    call: Callable[[], Awaitable[T]],
    documents_affected: Callable[[T], Optional[int]],
    *,
    update: Optional[Any] = None,
    extra: Optional[dict[str, Any]] = None,
) -> T:
    start_time = start_mongo_timer()
    try:
        result = await call()
    except Exception as exc:
        log_mongo_query(
            collection=collection,
            operation=operation,
            query=query,
            start_time=start_time,
            documents_affected=0,
            update=update,
            extra={**(extra or {}), "error_type": type(exc).__name__},
            failed=True,
        )
        raise

    log_mongo_query(
        collection=collection,
        operation=operation,
        query=query,
        start_time=start_time,
        documents_affected=documents_affected(result),
        update=update,
        extra=extra,
    )
    return result


def log_mongo_query(
    collection: Any,
    operation: str,
    query: Any,
    start_time: float,
    documents_affected: Optional[int],
    *,
    update: Optional[Any] = None,
    extra: Optional[dict[str, Any]] = None,
    failed: bool = False,
) -> None:
    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
    event = {
        "collection": getattr(collection, "name", str(collection)),
        "operation": operation,
        "query": summarize_for_mongo_log(query),
        "documents_affected": documents_affected,
        "duration_ms": duration_ms,
    }
    if update is not None:
        event["update"] = summarize_mongo_update(update)
    if extra:
        event.update(extra)

    if failed:
        logger.error("MongoDB query failed", **event)
    else:
        logger.info("MongoDB query executed", **event)


def summarize_for_mongo_log(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): summarize_for_mongo_log(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set, frozenset)):
        sequence = list(value)
        return {
            "type": type(value).__name__,
            "count": len(sequence),
            "items": [
                summarize_for_mongo_log(item) for item in sequence[:MAX_LOGGED_ITEMS]
            ],
        }

    if isinstance(value, str):
        if len(value) <= MAX_LOGGED_STRING:
            return value
        return f"{value[:MAX_LOGGED_STRING]}...<len={len(value)}>"

    if isinstance(value, bytes):
        return {"type": "bytes", "bytes": len(value)}

    if value is None or isinstance(value, (int, float, bool)):
        return value

    return repr(value)


def summarize_mongo_update(update: Any) -> Any:
    if not isinstance(update, Mapping):
        return summarize_for_mongo_log(update)

    summary: dict[str, Any] = {}
    for operator, payload in update.items():
        if isinstance(payload, Mapping):
            fields = sorted(str(field) for field in payload.keys())
            summary[str(operator)] = {
                "field_count": len(fields),
                "fields": fields[:MAX_LOGGED_ITEMS],
            }
        elif isinstance(payload, Sequence) and not isinstance(payload, (str, bytes)):
            summary[str(operator)] = {
                "type": type(payload).__name__,
                "count": len(payload),
            }
        else:
            summary[str(operator)] = type(payload).__name__

    return summary


def summarize_mongo_document(document: Mapping[str, Any]) -> dict[str, Any]:
    data = document.get("data")
    data_keys = (
        sorted(str(key) for key in data.keys()) if isinstance(data, Mapping) else []
    )
    return {
        "document_keys": sorted(str(key) for key in document.keys()),
        "data_key_count": len(data_keys),
        "data_keys": data_keys[:MAX_LOGGED_ITEMS],
    }
