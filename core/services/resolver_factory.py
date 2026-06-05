# core/services/resolver_factory.py

"""Фабрика резолверов записей (task3, ГЗ-1 Блок B).

Вынесена из RecordService: сборка сложного контекста (batch-резолвер N+1,
поиск по бизнес-ключу, резолвер агрегаций) — отдельная ответственность
паттерн-класса, сервис остаётся оркестратором.
"""

from typing import Any, Callable, Dict, List, Optional, Tuple

from engine.context import RecordResolverSession


class RecordResolverFactory:
    """Собирает резолверы, привязанные к конкретному инстансу (tenant)."""

    def __init__(self, record_repo: Any):
        self.record_repo = record_repo

    def create(
        self, instance_uuid: str
    ) -> Tuple[RecordResolverSession, Callable[..., Any]]:
        record_repo = self.record_repo

        # 1. Батч-резолвер для устранения N+1
        async def batch_fetch(
            uuids: List[str],
        ) -> Dict[str, Dict[str, Any]]:
            return await record_repo.get_records_by_uuids(
                instance_uuid,
                uuids,
            )

        # Поиск одиночной записи по бизнес-ключу (QR, SKU и т.д.)
        async def custom_lookup(
            lookup_field: str, value: Any
        ) -> Optional[Dict[str, Any]]:
            return await record_repo.get_record_by_custom_field(
                instance_uuid=instance_uuid, field_name=lookup_field, value=value
            )

        session_resolver = RecordResolverSession(
            batch_fetch_func=batch_fetch, custom_lookup_func=custom_lookup
        )

        # 2. Резолвер агрегаций
        async def resolve_aggregation(
            target_template_uuid: str,
            filter_field: str,
            filter_value: Any,
            agg_function: str,
            agg_field: Optional[str],
        ) -> Any:
            return await record_repo.aggregate_records(
                instance_uuid=instance_uuid,
                target_template_uuid=target_template_uuid,
                filter_field=filter_field,
                filter_value=filter_value,
                agg_function=agg_function,
                agg_field=agg_field,
            )

        return session_resolver, resolve_aggregation
