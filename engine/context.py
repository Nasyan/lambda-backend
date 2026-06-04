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
        # Вся внутренняя логика остается без изменений...
        if not record_val:
            return {}

        # 1. СТАНДАРТНЫЙ ПОИСК ПО СИСТЕМНОМУ UUID
        if lookup_field == "_id":
            record_id_str = str(record_val)
            if record_id_str not in self._cache:
                fetched = await self.batch_fetch_func([record_id_str])
                self._cache.update(fetched)
            return self._cache.get(record_id_str, {})

        else:
            cache_key = f"{lookup_field}:{record_val}"
            if cache_key not in self._custom_cache:
                if self.custom_lookup_func:
                    fetched_record = await self.custom_lookup_func(
                        lookup_field, record_val
                    )
                    self._custom_cache[cache_key] = fetched_record or {}
                else:
                    self._custom_cache[cache_key] = {}

            return self._custom_cache.get(cache_key, {})

    @trace_action(name="Resolver::Batch_Prefetch")
    async def prefetch(self, record_ids: List[str]) -> None:
        missing_ids = [rid for rid in record_ids if rid not in self._cache]
        if not missing_ids:
            return

        fetched_records = await self.batch_fetch_func(missing_ids)
        self._cache.update(fetched_records)
