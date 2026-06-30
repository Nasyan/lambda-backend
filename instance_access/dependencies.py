from uuid import UUID
from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import get_db
from instance_access.service import TriggersConfigManager
from instance_access.exceptions.dependenies import (
    ToolDisabledException,
    ToolActionForbiddenException,
)


def get_triggers_manager(
    session: AsyncSession = Depends(get_db),
) -> TriggersConfigManager:
    return TriggersConfigManager(session)


class RequireTriggerPermission:
    """
    Зависимость для проверки конкретного права в конфигурации триггеров инстанса.
    """

    def __init__(self, permission_field: str):
        # Передаем имя проверяемого поля, например: 'allow_get', 'allow_post'
        self.permission_field = permission_field

    async def __call__(
        self, instance_uuid: UUID, db: AsyncSession = Depends(get_db)
    ) -> None:
        manager = TriggersConfigManager(db)
        config = await manager.get_config(instance_uuid)

        # 1. Если инструмент выключен целиком — бросаем специализированное исключение
        if not config.enabled:
            raise ToolDisabledException(
                details={"instance_uuid": str(instance_uuid), "tool": "triggers"}
            )

        # 2. Проверяем точечное действие
        has_permission = getattr(config, self.permission_field, False)
        if not has_permission:
            raise ToolActionForbiddenException(
                message=f"Операция отклонена: отсутствует право '{self.permission_field}'.",
                details={
                    "instance_uuid": str(instance_uuid),
                    "tool": "triggers",
                    "missing_permission": self.permission_field,
                },
            )
