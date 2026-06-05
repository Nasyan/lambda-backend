# core/services/template.py

from uuid import UUID
from typing import Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from mongo.template import TemplateRepository
from engine.schema_rules import NoCodeSchemaValidator
from core.services.template_integrity import TemplateIntegrityService
from core.services.schema_migration import SchemaMigrationService
from middleware.schemas import ListParameters

# Доменные ошибки слоя Mongo (контракт API сохраняем неизменным)
from mongo.exceptions.template import (
    TemplateNotFoundError,
    SchemaMutationError,
    TemplateValidationError,
)

# Импортируем строго типизированные исключения
from core.exceptions.template import (
    TemplateNotFoundException,
    TemplateMutationError,
    DuplicateTemplateNameException,
)


class TemplateService:
    """Оркестратор работы с шаблонами (task3, ГЗ-1 Блок A).

    Вызывает чистый валидатор схемы (NoCodeSchemaValidator), инфраструктурные
    проверки каскадных связей (TemplateIntegrityService), миграцию данных
    (SchemaMigrationService) и глупый I/O репозитория.
    """

    def __init__(
        self,
        template_repo: TemplateRepository,
        schema_migration: Optional[SchemaMigrationService] = None,
    ):
        self.template_repo = template_repo
        self.schema_migration = schema_migration

    async def _get_template_or_raise(
        self, instance_uuid: str, template_uuid: str
    ) -> Dict[str, Any]:
        """Вспомогательный приватный метод для проверки существования шаблона."""
        template = await self.template_repo.get_template(
            instance_uuid=instance_uuid, template_uuid=template_uuid
        )
        if not template:
            raise TemplateNotFoundException(
                template_uuid=template_uuid, instance_uuid=instance_uuid
            )
        return template

    async def create_template(
        self,
        instance_uuid: UUID,
        name: str,
        schema_definition: Dict[str, Any],
        user_uuid: UUID,
    ) -> Dict[str, Any]:
        existing_template = await self.template_repo.find_by_name(
            instance_uuid=str(instance_uuid), name=name
        )
        if existing_template:
            raise DuplicateTemplateNameException(
                name=name, instance_uuid=str(instance_uuid)
            )

        try:
            NoCodeSchemaValidator.validate_definition(schema_definition)
        except Exception as e:
            raise TemplateValidationError(
                message=f"Ошибка в определении схемы: {str(e)}"
            )

        NoCodeSchemaValidator.check_circular_dependencies(schema_definition)

        return await self.template_repo.create_template(
            instance_uuid=str(instance_uuid),
            name=name,
            schema=schema_definition,
            user_uuid=str(user_uuid),
        )

    async def delete_template(
        self,
        instance_uuid: UUID,
        template_uuid: UUID,
        db: Optional[AsyncSession] = None,
    ) -> None:
        str_instance = str(instance_uuid)
        str_template = str(template_uuid)

        if db is not None:
            template = await self._get_template_or_raise(str_instance, str_template)
            template_name = template.get("name", "")

            try:
                await TemplateIntegrityService.check_template_destruction_safe(
                    instance_uuid=instance_uuid,
                    template_uuid=template_uuid,
                    template_name=template_name,
                    db=db,
                )
            except Exception as e:
                raise TemplateMutationError(
                    action="удаление шаблона", error=e, template_uuid=str_template
                )

        await self.template_repo.delete_template(
            instance_uuid=str_instance,
            template_uuid=str_template,
        )

    async def add_column(
        self,
        instance_uuid: UUID,
        template_uuid: UUID,
        column_name: str,
        field_meta: Dict[str, Any],
        user_uuid: UUID,
    ) -> Dict[str, Any]:
        str_instance = str(instance_uuid)
        str_template = str(template_uuid)

        template = await self._get_template_or_raise(str_instance, str_template)
        schema = template.get("schema", {})

        try:
            NoCodeSchemaValidator.validate_definition({column_name: field_meta})
        except Exception as e:
            raise TemplateValidationError(
                message=f"Невалидная конфигурация для нового столбца '{column_name}': {str(e)}"
            )

        temp_schema = schema.copy()
        temp_schema[column_name] = field_meta

        NoCodeSchemaValidator.check_circular_dependencies(temp_schema)

        return await self.template_repo.add_column(
            instance_uuid=str_instance,
            template_uuid=str_template,
            column_name=column_name,
            field_meta=field_meta,
            user_uuid=str(user_uuid),
        )

    async def update_template_metadata(
        self,
        instance_uuid: UUID,
        template_uuid: UUID,
        name: str,
        user_uuid: UUID,
        db: Optional[AsyncSession] = None,
    ) -> Dict[str, Any]:
        str_instance = str(instance_uuid)
        str_template = str(template_uuid)

        template = await self._get_template_or_raise(str_instance, str_template)
        old_name = template.get("name", "")

        if old_name != name:
            schema = template.get("schema", {})
            for column_name, field_meta in schema.items():
                embedded_triggers = field_meta.get("triggers", [])
                if embedded_triggers:
                    raise TemplateMutationError(
                        action="переименование шаблона",
                        error=Exception(
                            f"Невозможно изменить имя таблицы '{old_name}', так как к её полям привязаны встроенные автоматизации."
                        ),
                        template_uuid=str_template,
                    )

            if db is not None:
                try:
                    # 🚀 Вызываем корректный метод валидации переименования
                    await TemplateIntegrityService.check_template_rename_safe(
                        instance_uuid=instance_uuid,
                        template_uuid=template_uuid,
                        old_name=old_name,
                        new_name=name,
                        db=db,
                    )
                except Exception as e:
                    raise TemplateMutationError(
                        action="переименование шаблона",
                        error=e,
                        template_uuid=str_template,
                    )

        return await self.template_repo.update_template_metadata(
            instance_uuid=str_instance,
            template_uuid=str_template,
            name=name,
            user_uuid=str(user_uuid),
        )

    async def drop_column(
        self,
        instance_uuid: UUID,
        template_uuid: UUID,
        column_name: str,
        user_uuid: UUID,
        db: Optional[AsyncSession] = None,
    ) -> Dict[str, Any]:
        str_instance = str(instance_uuid)
        str_template = str(template_uuid)

        template = await self._get_template_or_raise(str_instance, str_template)
        schema = template.get("schema", {})
        template_name = template.get("name", "")

        if db is not None:
            try:
                await TemplateIntegrityService.check_field_mutation_safe(
                    instance_uuid=instance_uuid,
                    template_uuid=template_uuid,
                    template_name=template_name,
                    column_name=column_name,
                    current_schema=schema,
                    db=db,
                )
            except Exception as e:
                raise TemplateMutationError(
                    action="удаление колонки",
                    error=e,
                    template_uuid=str_template,
                    column_name=column_name,
                )

        return await self.template_repo.drop_column(
            instance_uuid=str_instance,
            template_uuid=str_template,
            column_name=column_name,
            user_uuid=str(user_uuid),
        )

    async def update_column_meta(
        self,
        instance_uuid: UUID,
        template_uuid: UUID,
        column_name: str,
        new_meta: Dict[str, Any],
        user_uuid: UUID,
        db: Optional[AsyncSession] = None,
    ) -> Dict[str, Any]:
        str_instance = str(instance_uuid)
        str_template = str(template_uuid)

        template = await self._get_template_or_raise(str_instance, str_template)
        schema = template.get("schema", {})
        old_meta = schema.get(column_name, {})
        template_name = template.get("name", "")

        # 1. Чистая валидация новых метаданных колонки (in-memory)
        try:
            NoCodeSchemaValidator.validate_definition({column_name: new_meta})
        except Exception as e:
            raise TemplateValidationError(
                message=f"Некорректные метаданные для столбца '{column_name}': {str(e)}"
            )

        # 2. Бизнес-проверка существования колонки (раньше пряталась в репозитории)
        if column_name not in schema:
            raise TemplateNotFoundError(
                template_uuid=str_template,
                instance_uuid=str_instance,
                message=f"Столбец '{column_name}' не существует в схеме таблицы. Изменение метаданных невозможно.",
            )

        # 3. Инфраструктурная проверка каскадных зависимостей при смене типа
        if old_meta.get("type") != new_meta.get("type") and db is not None:
            try:
                await TemplateIntegrityService.check_field_mutation_safe(
                    instance_uuid=instance_uuid,
                    template_uuid=template_uuid,
                    template_name=template_name,
                    column_name=column_name,
                    current_schema=schema,
                    db=db,
                )
            except Exception as e:
                raise TemplateMutationError(
                    action="изменение типа колонки",
                    error=e,
                    template_uuid=str_template,
                    column_name=column_name,
                )

        # 4. Чистая проверка циклов формул с новой метой
        temp_schema = schema.copy()
        temp_schema[column_name] = new_meta
        NoCodeSchemaValidator.check_circular_dependencies(temp_schema)

        # 5. Миграция существующих записей под новые правила колонки
        if self.schema_migration is not None:
            try:
                await self.schema_migration.validate_existing_records_against_field(
                    instance_uuid=str_instance,
                    template_uuid=str_template,
                    column_name=column_name,
                    new_field_meta=new_meta,
                )
            except Exception as e:
                # Переупаковываем ошибку валидации записей в контекст мутации схемы
                raise SchemaMutationError(
                    template_uuid=str_template,
                    column_name=column_name,
                    message=f"Запрещено изменять конфигурацию столбца '{column_name}': существующие данные не соответствуют новым правилам. Детали: {str(e)}",
                )

        # 6. Глупый I/O
        return await self.template_repo.update_column_meta(
            instance_uuid=str_instance,
            template_uuid=str_template,
            column_name=column_name,
            new_meta=new_meta,
            user_uuid=str(user_uuid),
        )

    async def get_template(
        self,
        instance_uuid: UUID,
        template_uuid: UUID,
    ) -> Dict[str, Any]:
        return await self._get_template_or_raise(str(instance_uuid), str(template_uuid))

    async def get_all_templates(
        self,
        instance_uuid: UUID,
        params: Optional[ListParameters] = None,  # 🔥 Делаем опциональным
    ) -> list[dict[str, Any]]:
        return await self.template_repo.get_all_templates(
            instance_uuid=str(instance_uuid),
            params=params,  # Прокидываем дальше (может быть None)
        )
