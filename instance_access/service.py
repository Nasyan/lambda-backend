from uuid import UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from pydantic import ValidationError

from users.models import InstanceToolsConfig
from instance_access.schemas import InstanceToolsConfigSchema, TriggersToolSchema
from instance_access.exceptions.service import InstanceConfigValidationError


class BaseToolsConfigManager:
    """
    Базовый класс для работы с конфигурацией инстанса.
    Отвечает за чтение, валидацию и коммит корневого JSON в БД.
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_or_create_raw_config(
        self, instance_uuid: UUID
    ) -> InstanceToolsConfig:
        query = select(InstanceToolsConfig).where(
            InstanceToolsConfig.instance_uuid == instance_uuid
        )
        result = await self.db.execute(query)
        db_config = result.scalar_one_or_none()

        if not db_config:
            # Если даже дефолтная схема по какой-то причине не собирается
            try:
                default_data = InstanceToolsConfigSchema().to_dict()
            except ValidationError as e:
                raise InstanceConfigValidationError(
                    message="Не удалось сгенерировать дефолтную конфигурацию.",
                    details={"errors": e.errors()},
                )

            db_config = InstanceToolsConfig(
                instance_uuid=instance_uuid, config_data=default_data
            )
            self.db.add(db_config)
            await self.db.commit()
            await self.db.refresh(db_config)

        return db_config

    async def load_schema(self, instance_uuid: UUID) -> InstanceToolsConfigSchema:
        db_config = await self.get_or_create_raw_config(instance_uuid)
        try:
            return InstanceToolsConfigSchema.model_validate(db_config.config_data)
        except ValidationError as e:
            # Если в БД лежит невалидный JSON, плавно отдаем 422 вместо падения в 500
            raise InstanceConfigValidationError(
                message="Конфигурация в базе данных повреждена или устарела.",
                details={"instance_uuid": str(instance_uuid), "errors": e.errors()},
            )

    async def save_schema(
        self, instance_uuid: UUID, schema: InstanceToolsConfigSchema
    ) -> None:
        db_config = await self.get_or_create_raw_config(instance_uuid)
        db_config.config_data = schema.to_dict()

        self.db.add(db_config)
        await self.db.commit()


class TriggersConfigManager(BaseToolsConfigManager):
    """
    Управление конфигурацией инструмента 'Триггеры'.
    """

    async def get_config(self, instance_uuid: UUID) -> TriggersToolSchema:
        # load_schema уже защищен эксцепшеном внутри базового класса
        master_schema = await self.load_schema(instance_uuid)
        return master_schema.triggers

    async def update_full_config(
        self, instance_uuid: UUID, new_trigger_config: TriggersToolSchema
    ) -> TriggersToolSchema:
        master_schema = await self.load_schema(instance_uuid)
        master_schema.triggers = new_trigger_config

        await self.save_schema(instance_uuid, master_schema)
        return master_schema.triggers

    async def patch_config(self, instance_uuid: UUID, **kwargs) -> TriggersToolSchema:
        master_schema = await self.load_schema(instance_uuid)
        current_triggers_dict = master_schema.triggers.model_dump()

        for key, value in kwargs.items():
            if key in current_triggers_dict:
                current_triggers_dict[key] = value

        # Защищаем сборку частичного конфига. Если переданы несовместимые параметры
        try:
            master_schema.triggers = TriggersToolSchema(**current_triggers_dict)
        except ValidationError as e:
            raise InstanceConfigValidationError(
                message="Переданы некорректные параметры для обновления триггеров.",
                details={"instance_uuid": str(instance_uuid), "errors": e.errors()},
            )

        await self.save_schema(instance_uuid, master_schema)
        return master_schema.triggers

    async def disable_triggers_entirely(
        self, instance_uuid: UUID
    ) -> TriggersToolSchema:
        return await self.patch_config(instance_uuid, enabled=False)

    async def enable_triggers_entirely(self, instance_uuid: UUID) -> TriggersToolSchema:
        return await self.patch_config(
            instance_uuid,
            enabled=True,
            allow_get=True,
            allow_post=True,
            allow_put=True,
            allow_delete=True,
        )
