from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from uuid import UUID

from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from engine.batch_loader import BatchDataLoader
from engine.evaluator import EvaluationScope
from mongo.template import TemplateRepository
from redisdb.cache import CacheLayer, triggers_cache_key
from triggers.models import EventType, PayloadReturnType, Trigger, TriggerType

TRIGGER_CACHE_FIELDS = (
    "id",
    "instance_uuid",
    "name",
    "trigger_type",
    "event_type",
    "condition_ast",
    "payload_ast",
    "payload_return_type",
    "action_mapping_ast",
    "source_template_uuid",
    "target_template_uuid",
    "target_field",
    "action_name",
    "action_params",
    "cron_expression",
)

TRIGGER_UUID_FIELDS = (
    "id",
    "instance_uuid",
    "source_template_uuid",
    "target_template_uuid",
)

TRIGGER_ENUM_FIELDS = {
    "trigger_type": TriggerType,
    "event_type": EventType,
    "payload_return_type": PayloadReturnType,
}


def _serialize_trigger(trigger: Trigger) -> Dict[str, Any]:
    data = {}
    for field_name in TRIGGER_CACHE_FIELDS:
        value = getattr(trigger, field_name, None)
        if hasattr(value, "value"):
            value = value.value
        elif isinstance(value, UUID):
            value = str(value)
        data[field_name] = value
    return data


def _deserialize_trigger(data: Dict[str, Any]) -> Trigger:
    trigger_data = dict(data)
    for field_name in TRIGGER_UUID_FIELDS:
        value = trigger_data.get(field_name)
        if value is not None:
            trigger_data[field_name] = UUID(str(value))
    for field_name, enum_type in TRIGGER_ENUM_FIELDS.items():
        value = trigger_data.get(field_name)
        if value is not None and not isinstance(value, enum_type):
            trigger_data[field_name] = enum_type(value)
    return Trigger(**trigger_data)


@dataclass
class TriggerEventContext:
    event_type: EventType
    instance_uuid: str
    template_uuid: str
    document: Dict[str, Any]
    manual_input: Optional[Any]
    triggers: List[Trigger]
    scope: EvaluationScope
    data_loader: BatchDataLoader


class EventReceptor:
    """Entry point that captures a record/manual event and finds subscribed triggers."""

    def __init__(
        self,
        pg_session: AsyncSession,
        mongo_db: AsyncIOMotorDatabase,
        template_repo: Optional[TemplateRepository] = None,
        trigger_cache: Optional[CacheLayer] = None,
    ):
        self.pg_session = pg_session
        self.mongo_db = mongo_db
        self.template_repo = template_repo or TemplateRepository(mongo_db)
        self.trigger_cache = trigger_cache

    async def capture(
        self,
        event_type: EventType,
        instance_uuid: str,
        template_uuid: str,
        document: Dict[str, Any],
        manual_input: Optional[Any] = None,
        previous_document: Optional[Dict[str, Any]] = None,
    ) -> TriggerEventContext:
        triggers = await self.get_subscribed_triggers(
            event_type=event_type,
            instance_uuid=instance_uuid,
            template_uuid=template_uuid,
        )
        source_schema = await self._get_source_schema(
            instance_uuid=instance_uuid,
            template_uuid=template_uuid,
        )
        data_loader = BatchDataLoader(
            mongo_db=self.mongo_db,
            instance_uuid=instance_uuid,
        )
        variables = {}
        if manual_input is not None:
            variables["__input_value__"] = manual_input
            variables["input"] = manual_input
        scope = EvaluationScope(
            document=document or {},
            instance_uuid=str(instance_uuid),
            variables=variables,
            source_schema=source_schema,
            previous_document=previous_document,
        )
        return TriggerEventContext(
            event_type=event_type,
            instance_uuid=str(instance_uuid),
            template_uuid=str(template_uuid),
            document=document or {},
            manual_input=manual_input,
            triggers=triggers,
            scope=scope,
            data_loader=data_loader,
        )

    async def get_subscribed_triggers(
        self,
        event_type: EventType,
        instance_uuid: str,
        template_uuid: str,
    ) -> List[Trigger]:
        if self.trigger_cache is not None:
            cache_key = triggers_cache_key(instance_uuid, template_uuid, event_type)
            cached_triggers = await self.trigger_cache.get_json(cache_key)
            if cached_triggers is not None:
                try:
                    return [_deserialize_trigger(item) for item in cached_triggers]
                except Exception:
                    pass

        stmt = select(Trigger).where(
            Trigger.instance_uuid == UUID(str(instance_uuid)),
            Trigger.source_template_uuid == UUID(str(template_uuid)),
            Trigger.trigger_type == TriggerType.AUTOMATION,
            Trigger.event_type == event_type,
        )
        result = await self.pg_session.execute(stmt)
        triggers = list(result.scalars().all())
        if self.trigger_cache is not None:
            await self.trigger_cache.set_json(
                triggers_cache_key(instance_uuid, template_uuid, event_type),
                [_serialize_trigger(trigger) for trigger in triggers],
            )
        return triggers

    async def _get_source_schema(
        self, instance_uuid: str, template_uuid: str
    ) -> Dict[str, Any]:
        template = await self.template_repo.get_template(
            instance_uuid=str(instance_uuid),
            template_uuid=str(template_uuid),
        )
        return template.get("schema", {})
