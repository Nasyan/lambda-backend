# engine/context.py

from typing import List, Dict, Any, Callable, Awaitable, Optional
from logs.decorators import trace_action


class RecordResolverSession:
    def __init__(
        self,
        batch_fetch_func: Callable[[List[str]], Awaitable[Dict[str, Dict[str, Any]]]],
        custom_lookup_func: Optional[
            Callable[[str, Any], Awaitable[Optional[Dict[str, Any]]]]
        ] = None,
    ):
        self.batch_fetch_func = batch_fetch_func
        self.custom_lookup_func = custom_lookup_func
        self._cache: Dict[str, Dict[str, Any]] = {}
        self._custom_cache: Dict[str, Dict[str, Any]] = {}

    @trace_action(name="Resolver::Resolve_Record")
    async def __call__(
        self, record_val: Any, lookup_field: str = "_id"
    ) -> Dict[str, Any]:
        if not record_val:
            return {}

        # 🔥 Нормализация входных данных: защита от словарей/объектов
        if isinstance(record_val, dict):
            if lookup_field in record_val:
                normalized_val = str(record_val[lookup_field])
            else:
                return {}  # Если передали объект без нужного ключа, искать нечего
        else:
            normalized_val = str(record_val)

        # 1. СТАНДАРТНЫЙ ПОИСК ПО СИСТЕМНОМУ UUID
        if lookup_field == "_id":
            if normalized_val not in self._cache:
                fetched = await self.batch_fetch_func([normalized_val])
                self._cache.update(fetched)
            return self._cache.get(normalized_val, {})

        # 2. ПОИСК ПО КАСТОМНОМУ ПОЛЮ (QR, SKU)
        else:
            cache_key = f"{lookup_field}:{normalized_val}"
            if cache_key not in self._custom_cache:
                if self.custom_lookup_func:
                    fetched_record = await self.custom_lookup_func(
                        lookup_field, normalized_val
                    )
                    self._custom_cache[cache_key] = fetched_record or {}
                else:
                    self._custom_cache[cache_key] = {}

            return self._custom_cache.get(cache_key, {})

    @trace_action(name="Resolver::Batch_Prefetch")
    async def prefetch(self, record_ids: List[Any]) -> None:
        missing_ids = []

        # 🔥 Дополнительная очистка на входе в prefetch
        for rid in record_ids:
            if not rid:
                continue

            val = None
            if isinstance(rid, dict) and "_id" in rid:
                val = str(rid["_id"])
            elif isinstance(rid, str):
                val = rid

            if val and val not in self._cache:
                missing_ids.append(val)

        if not missing_ids:
            return

        fetched_records = await self.batch_fetch_func(missing_ids)
        self._cache.update(fetched_records)
