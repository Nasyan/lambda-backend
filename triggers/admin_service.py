# triggers/admin_service.py

"""Оркестратор администрирования триггеров (task3, ГЗ-1 Этап 2).

Вынесен из triggers/views.py: роутер занимался и SQL-запросами, и
валидацией, и синхронизацией embedded-метаданных в Mongo. Теперь роутер
только принимает запрос и передаёт DTO сюда; I/O делают TriggerRepository
(Postgres) и TriggerMetadataRepository (Mongo).
"""

import uuid as uuid_module
from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from mongo.template import TemplateRepository
from mongo.trigger_metadata import TriggerMetadataRepository
from triggers.models import Trigger
from triggers.repository import TriggerRepository
from triggers.validator import TriggerSchemaValidator
from triggers.exceptions.action import TriggerNotFoundDomainError
from middleware.schemas import ListParameters


def _dump_action_params(action_params: Any) -> Any:
    if hasattr(action_params, "model_dump"):
        return action_params.model_dump(mode="json")
    return action_params


class TriggerAdminService:
    def __init__(
        self,
        db: AsyncSession,
        template_repo: TemplateRepository,
        trigger_meta_repo: TriggerMetadataRepository,
    ):
        self.db = db
        self.repo = TriggerRepository(db)
        self.template_repo = template_repo
        self.trigger_meta_repo = trigger_meta_repo

    async def list_triggers(
        self, instance_uuid: UUID, params: Optional[ListParameters] = None
    ) -> List[Trigger]:
        return await self.repo.list(instance_uuid, params)

    async def get_trigger_or_raise(
        self, instance_uuid: UUID, trigger_uuid: UUID
    ) -> Trigger:
        trigger = await self.repo.get(instance_uuid, trigger_uuid)
        if not trigger:
            raise TriggerNotFoundDomainError(trigger_uuid=str(trigger_uuid))
        return trigger

    async def create_trigger(
        self, instance_uuid: UUID, payload: Any, user_uuid: UUID
    ) -> Trigger:
        validator = TriggerSchemaValidator()
        trigger_data = payload.model_dump()
        trigger_data["instance_uuid"] = instance_uuid
        trigger_data["action_params"] = _dump_action_params(payload.action_params)
        payload_return_type = await validator.validate(
            trigger_data=trigger_data,
            db=self.db,
            template_repo=self.template_repo,
        )

        target_field = getattr(payload, "target_field", None)

        db_trigger = Trigger(
            id=uuid_module.uuid4(),
            instance_uuid=instance_uuid,
            name=payload.name,
            trigger_type=payload.trigger_type,
            condition_ast=payload.condition_ast,
            payload_ast=payload.payload_ast,
            payload_return_type=payload_return_type,
            action_mapping_ast=payload.action_mapping_ast,
            source_template_uuid=payload.source_template_uuid,
            target_template_uuid=payload.target_template_uuid,
            target_field=target_field,
            event_type=payload.event_type,
            cron_expression=payload.cron_expression,
            action_name=payload.action_name,
            action_params=_dump_action_params(payload.action_params),
        )
        self.repo.add(db_trigger)
        await self.db.flush()

        # Инжекция триггера в динамическую схему Mongo
        if target_field and payload.target_template_uuid:
            schema_trigger_data = {
                "trigger_id": str(db_trigger.id),
                "trigger_type": db_trigger.trigger_type,
                "event": db_trigger.event_type or "onCalculate",
                "target_field": target_field,
            }

            await self.trigger_meta_repo.inject_trigger_to_schema(
                instance_uuid=str(instance_uuid),
                template_uuid=str(payload.target_template_uuid),
                column_name=target_field,
                trigger_data=schema_trigger_data,
                user_uuid=str(user_uuid),
            )

        await self.db.commit()
        await self.db.refresh(db_trigger)
        return db_trigger

    async def update_trigger(
        self,
        instance_uuid: UUID,
        trigger_uuid: UUID,
        payload: Any,
        user_uuid: UUID,
    ) -> Trigger:
        trigger = await self.get_trigger_or_raise(instance_uuid, trigger_uuid)

        update_data = payload.model_dump(exclude_unset=True)
        if "action_params" in update_data:
            update_data["action_params"] = _dump_action_params(payload.action_params)

        validation_data = self._trigger_validation_data(trigger)
        validation_data.update(update_data)
        validation_data["instance_uuid"] = instance_uuid

        validator = TriggerSchemaValidator()
        payload_return_type = await validator.validate(
            trigger_data=validation_data,
            db=self.db,
            template_repo=self.template_repo,
            trigger_uuid=trigger_uuid,
        )

        old_target_field = trigger.target_field
        old_target_template_uuid = trigger.target_template_uuid
        should_sync_schema = bool(
            {"target_field", "target_template_uuid", "trigger_type", "event_type"}
            & set(update_data.keys())
        )

        for field_name, value in update_data.items():
            setattr(trigger, field_name, value)
        trigger.payload_return_type = payload_return_type

        if should_sync_schema and old_target_field and old_target_template_uuid:
            await self.trigger_meta_repo.remove_trigger_from_schema(
                instance_uuid=str(instance_uuid),
                template_uuid=str(old_target_template_uuid),
                column_name=old_target_field,
                trigger_id=str(trigger.id),
                user_uuid=str(user_uuid),
            )

        if should_sync_schema and trigger.target_field and trigger.target_template_uuid:
            trigger_data_for_schema = {
                "trigger_id": str(trigger.id),
                "trigger_type": trigger.trigger_type,
                "event": trigger.event_type or "onCalculate",
                "target_field": trigger.target_field,
            }

            await self.trigger_meta_repo.inject_trigger_to_schema(
                instance_uuid=str(instance_uuid),
                template_uuid=str(trigger.target_template_uuid),
                column_name=trigger.target_field,
                trigger_data=trigger_data_for_schema,
                user_uuid=str(user_uuid),
            )

        await self.db.commit()
        await self.db.refresh(trigger)
        return trigger

    async def delete_trigger(
        self, instance_uuid: UUID, trigger_uuid: UUID, user_uuid: Any
    ) -> None:
        trigger = await self.get_trigger_or_raise(instance_uuid, trigger_uuid)

        target_field = getattr(trigger, "target_field", None)

        if trigger.target_template_uuid and target_field:
            await self.trigger_meta_repo.remove_trigger_from_schema(
                instance_uuid=str(instance_uuid),
                template_uuid=str(trigger.target_template_uuid),
                column_name=target_field,
                trigger_id=str(trigger.id),
                user_uuid=str(user_uuid),
            )

        await self.repo.delete(trigger)
        await self.db.commit()

    @staticmethod
    def _trigger_validation_data(trigger: Trigger) -> Dict[str, Any]:
        return {
            "instance_uuid": trigger.instance_uuid,
            "name": trigger.name,
            "trigger_type": trigger.trigger_type,
            "condition_ast": trigger.condition_ast,
            "payload_ast": trigger.payload_ast,
            "payload_return_type": trigger.payload_return_type,
            "action_mapping_ast": trigger.action_mapping_ast,
            "source_template_uuid": trigger.source_template_uuid,
            "target_template_uuid": trigger.target_template_uuid,
            "target_field": trigger.target_field,
            "event_type": trigger.event_type,
            "cron_expression": trigger.cron_expression,
            "action_name": trigger.action_name,
            "action_params": trigger.action_params,
        }
