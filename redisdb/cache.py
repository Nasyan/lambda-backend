import hashlib
import json
from datetime import date, datetime
from enum import Enum
from typing import Any, Optional
from uuid import UUID

import config
from redisdb.utils import redis_clients

try:
    from bson import ObjectId
except ImportError:  # pragma: no cover - pymongo installs bson in normal runtime
    ObjectId = None


class CacheJSONEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, Enum):
            return obj.value
        if ObjectId is not None and isinstance(obj, ObjectId):
            return str(obj)
        if hasattr(obj, "model_dump"):
            return obj.model_dump(mode="json")
        return super().default(obj)


class CacheLayer:
    def __init__(self, client: Any, ttl: int, enabled: bool = True):
        self.client = client
        self.ttl = ttl
        self.enabled = enabled

    async def get_json(self, key: str) -> Optional[Any]:
        if not self.enabled or self.client is None:
            return None

        try:
            raw_value = await self.client.get(key)
            if raw_value is None:
                return None
            return json.loads(raw_value)
        except Exception:
            return None

    async def set_json(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        if not self.enabled or self.client is None:
            return

        try:
            payload = json.dumps(
                value,
                cls=CacheJSONEncoder,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            await self.client.set(key, payload, ex=ttl or self.ttl)
        except Exception:
            return

    async def delete(self, *keys: str) -> None:
        if not self.enabled or self.client is None:
            return

        filtered_keys = [key for key in keys if key]
        if not filtered_keys:
            return

        try:
            await self.client.delete(*filtered_keys)
        except Exception:
            return

    async def delete_pattern(self, pattern: str) -> None:
        if not self.enabled or self.client is None:
            return

        try:
            batch = []
            async for key in self.client.scan_iter(match=pattern):
                batch.append(key)
                if len(batch) >= 100:
                    await self.delete(*batch)
                    batch = []
            if batch:
                await self.delete(*batch)
        except Exception:
            return


def build_cache_layer(db_name: str, ttl: int) -> CacheLayer:
    return CacheLayer(
        client=redis_clients.get(db_name),
        ttl=ttl,
        enabled=config.CACHE_ENABLED,
    )


def _normalize_for_hash(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _normalize_for_hash(value.model_dump(mode="json"))
    if isinstance(value, dict):
        return {
            str(key): _normalize_for_hash(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_normalize_for_hash(item) for item in value]
    if isinstance(value, set):
        return sorted(_normalize_for_hash(item) for item in value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if ObjectId is not None and isinstance(value, ObjectId):
        return str(value)
    return value


def stable_hash(value: Any) -> str:
    payload = json.dumps(
        _normalize_for_hash(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def template_cache_key(instance_uuid: Any, template_uuid: Any) -> str:
    return f"template:{instance_uuid}:{template_uuid}"


def template_list_cache_key(
    instance_uuid: Any,
    params: Any = None,
    limit: int = 100,
    offset: int = 0,
) -> str:
    params_payload = (
        params.model_dump(mode="json") if hasattr(params, "model_dump") else params
    )
    key_hash = stable_hash(
        {
            "params": params_payload,
            "limit": limit,
            "offset": offset,
        }
    )
    return f"template_list:{instance_uuid}:{key_hash}"


def template_list_cache_pattern(instance_uuid: Any) -> str:
    return f"template_list:{instance_uuid}:*"


def triggers_cache_key(instance_uuid: Any, template_uuid: Any, event_type: Any) -> str:
    key_hash = stable_hash(
        {
            "event_type": (
                event_type.value if hasattr(event_type, "value") else event_type
            ),
        }
    )
    return f"triggers:{instance_uuid}:{template_uuid}-{key_hash}"


def triggers_cache_pattern(instance_uuid: Any) -> str:
    return f"triggers:{instance_uuid}:*"


def analytics_cache_key(
    instance_uuid: Any,
    widget_uuid: Any,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    date_field: Optional[str] = None,
) -> str:
    key_hash = stable_hash(
        {
            "date_from": date_from,
            "date_to": date_to,
            "date_field": date_field,
        }
    )
    return f"analytics:{instance_uuid}:{widget_uuid}-{key_hash}"


def analytics_widget_cache_pattern(instance_uuid: Any, widget_uuid: Any) -> str:
    return f"analytics:{instance_uuid}:{widget_uuid}-*"
