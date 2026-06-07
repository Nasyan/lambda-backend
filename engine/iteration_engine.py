from typing import Any, Awaitable, Callable, Dict, Iterable, List, Optional

from engine.batch_loader import BatchDataLoader
from engine.evaluator import ASTEvaluator, EvaluationScope


class IterationLoopEngine:
    """Runs isolated per-item scopes for LIST payload automation."""

    def __init__(
        self,
        batch_loader: BatchDataLoader,
        evaluator: ASTEvaluator,
    ):
        self.batch_loader = batch_loader
        self.evaluator = evaluator

    async def for_each(
        self,
        items: Iterable[Any],
        base_scope: EvaluationScope,
        callback: Callable[[Any, EvaluationScope], Awaitable[None]],
        target_template_uuid: Optional[str] = None,
    ) -> None:
        item_list = list(items)
        await self.prefetch_items(item_list, target_template_uuid)

        for item in item_list:
            item_scope = base_scope.child_for_item(item)
            await callback(item, item_scope)

    async def map_items(
        self,
        items: Iterable[Any],
        base_scope: EvaluationScope,
        mapper: Callable[[Any, EvaluationScope], Awaitable[Dict[str, Any]]],
        target_template_uuid: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        mapped: List[Dict[str, Any]] = []

        async def collect(item: Any, scope: EvaluationScope) -> None:
            mapped.append(await mapper(item, scope))

        await self.for_each(
            items=items,
            base_scope=base_scope,
            callback=collect,
            target_template_uuid=target_template_uuid,
        )
        return mapped

    async def prefetch_items(
        self, items: Iterable[Any], target_template_uuid: Optional[str]
    ) -> None:
        record_ids = [
            record_id
            for record_id in (self._extract_record_id(item) for item in items)
            if record_id
        ]
        if record_ids:
            await self.batch_loader.prefetch(target_template_uuid, record_ids)

    def _extract_record_id(self, item: Any) -> Optional[str]:
        if not item:
            return None
        if isinstance(item, dict):
            raw_id = item.get("_id") or item.get("uuid") or item.get("target_uuid")
            return str(raw_id) if raw_id else None
        return str(item)
