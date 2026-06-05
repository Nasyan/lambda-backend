# notifications/utils.py
from typing import List, Dict, Any, Optional
from uuid import UUID
import uuid

from sqlalchemy.ext.asyncio import AsyncSession
from middleware.schemas import ListParameters
from notifications.models import NotificationTemplate, NotificationInbox
from notifications.repository import (
    NotificationTemplateRepository,
    NotificationInboxRepository,
)
from notifications.exceptions.dispatcher import NotificationNotFoundError
from core.services.template_integrity import TemplateIntegrityService


class NotificationTemplateService:
    """Сервисный слой для управления шаблонами уведомлений и инбоксом."""

    @staticmethod
    async def create_template(
        db: AsyncSession,
        instance_uuid: UUID,
        payload_data: Dict[str, Any],
        mongo_template_repo: Any,
    ) -> UUID:
        """Создает шаблон и валидирует его No-Code плейсхолдеры."""
        new_uuid = uuid.uuid4()

        # 1. Запускаем валидацию масок, только если переданы маппинги сущностей
        entity_mappings = payload_data.get("entity_mappings")
        if entity_mappings:
            await TemplateIntegrityService.validate_notification_template(
                instance_uuid=instance_uuid,
                title=payload_data.get("title", ""),
                body=payload_data.get("body", ""),
                entity_mappings=entity_mappings,
                template_repo=mongo_template_repo,
            )

        # 2. 🔥 Фикс TypeError: исключаем entity_mappings из инициализации SQLAlchemy модели,
        # если его нет в схеме таблицы PostgreSQL
        template = NotificationTemplate(
            uuid=new_uuid,
            instance_uuid=instance_uuid,
            name=payload_data["name"],
            title=payload_data["title"],
            body=payload_data["body"],
            channels=payload_data["channels"],
            recipients_config=payload_data["recipients_config"],
            # Если в твоей модели Postgres маппинги хранятся под другим именем,
            # замапь его сюда, например: extra_config=payload_data.get("entity_mappings")
        )
        NotificationTemplateRepository(db).add(template)
        await db.commit()
        return new_uuid

    @staticmethod
    async def get_templates(
        db: AsyncSession, instance_uuid: UUID, params: ListParameters
    ) -> List[NotificationTemplate]:
        return await NotificationTemplateRepository(db).list(instance_uuid, params)

    @staticmethod
    async def get_template_by_uuid(
        db: AsyncSession, instance_uuid: UUID, template_uuid: UUID
    ) -> NotificationTemplate:
        """Возвращает шаблон или бросает доменное исключение."""
        template = await NotificationTemplateRepository(db).get_by_uuid(
            instance_uuid, template_uuid
        )

        if not template:
            raise NotificationNotFoundError()
        return template

    @staticmethod
    async def update_template(
        db: AsyncSession,
        instance_uuid: UUID,
        template_uuid: UUID,
        update_data: Dict[str, Any],
        mongo_template_repo: Any,
    ) -> Optional[UUID]:
        """Обновляет шаблон с повторной валидацией No-Code масок."""
        if not update_data:
            return None

        current_template = await NotificationTemplateService.get_template_by_uuid(
            db, instance_uuid, template_uuid
        )

        # Мержим данные для валидации
        full_title = update_data.get("title", current_template.title)
        full_body = update_data.get("body", current_template.body)

        # 🔥 Проверяем наличие entity_mappings в апдейте или в базе
        full_mappings = update_data.get(
            "entity_mappings", getattr(current_template, "entity_mappings", None)
        )

        if full_mappings:
            await TemplateIntegrityService.validate_notification_template(
                instance_uuid=instance_uuid,
                title=full_title,
                body=full_body,
                entity_mappings=full_mappings,
                template_repo=mongo_template_repo,
            )

        # Избегаем передачи несуществующего поля в UPDATE запрос SQLAlchemy
        update_data.pop("entity_mappings", None)

        updated_uuid = await NotificationTemplateRepository(db).update_values(
            instance_uuid, template_uuid, update_data
        )

        if not updated_uuid:
            raise NotificationNotFoundError()

        await db.commit()
        return updated_uuid

    @staticmethod
    async def delete_template(
        db: AsyncSession, instance_uuid: UUID, template_uuid: UUID
    ) -> None:
        """Удаляет шаблон, проверяя предварительно каскадные связи."""
        current_template = await NotificationTemplateService.get_template_by_uuid(
            db, instance_uuid, template_uuid
        )

        # Защита от удаления
        await TemplateIntegrityService.check_template_destruction_safe(
            instance_uuid=instance_uuid,
            template_uuid=template_uuid,
            template_name=current_template.name,
            db=db,
        )

        deleted_count = await NotificationTemplateRepository(db).delete_by_uuid(
            instance_uuid, template_uuid
        )

        if deleted_count == 0:
            raise NotificationNotFoundError()

        await db.commit()

    @staticmethod
    async def get_user_inbox(
        db: AsyncSession, user_uuid: UUID
    ) -> List[NotificationInbox]:
        """Возвращает элементы инбокса сотрудника с подгрузкой истории компиляции."""
        return await NotificationInboxRepository(db).list_for_user(user_uuid)

    @staticmethod
    async def mark_inbox_as_read(
        db: AsyncSession, user_uuid: UUID, notification_uuid: UUID
    ) -> None:
        """Помечает уведомление как прочитанное."""
        updated_uuid = await NotificationInboxRepository(db).mark_as_read(
            user_uuid, notification_uuid
        )

        if not updated_uuid:
            raise NotificationNotFoundError()

        await db.commit()
