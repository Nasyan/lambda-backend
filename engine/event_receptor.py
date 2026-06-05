from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from uuid import UUID

from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from engine.batch_loader import BatchDataLoader
from engine.evaluator import EvaluationScope
from mongo.template import TemplateRepository
from triggers.models import EventType, Trigger, TriggerType


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
    ):
        self.pg_session = pg_session
        self.mongo_db = mongo_db
        self.template_repo = template_repo or TemplateRepository(mongo_db)

    async def capture(
        self,
        event_type: EventType,
        instance_uuid: str,
        template_uuid: str,
        document: Dict[str, Any],
        manual_input: Optional[Any] = None,
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
        stmt = select(Trigger).where(
            Trigger.instance_uuid == UUID(str(instance_uuid)),
            Trigger.source_template_uuid == UUID(str(template_uuid)),
            Trigger.trigger_type == TriggerType.AUTOMATION,
            Trigger.event_type == event_type,
        )
        result = await self.pg_session.execute(stmt)
        return list(result.scalars().all())

    async def _get_source_schema(
        self, instance_uuid: str, template_uuid: str
    ) -> Dict[str, Any]:
        template = await self.template_repo.get_template(
            instance_uuid=str(instance_uuid),
            template_uuid=str(template_uuid),
        )
        return template.get("schema", {})
