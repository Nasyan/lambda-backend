# core/services/template.py

from uuid import UUID
from typing import Dict, Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from mongo.template import TemplateRepository
from engine.integrity import SchemaIntegrityValidator
from middleware.schemas import ListParameters

# Импортируем строго типизированные исключения
from core.exceptions.template import (
    TemplateNotFoundException,
    TemplateMutationError,
    DuplicateTemplateNameException,
)


class TemplateService:
    def __init__(self, template_repo: TemplateRepository):
        self.template_repo = template_repo

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

        SchemaIntegrityValidator.check_circular_dependencies(schema_definition)

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
                await SchemaIntegrityValidator.check_template_destruction_safe(
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

        temp_schema = schema.copy()
        temp_schema[column_name] = field_meta

        SchemaIntegrityValidator.check_circular_dependencies(temp_schema)

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
                    # 🚀 Исправлено: Вызываем корректный метод валидации переименования
                    await SchemaIntegrityValidator.check_template_rename_safe(
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
                await SchemaIntegrityValidator.check_field_mutation_safe(
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

        if old_meta.get("type") != new_meta.get("type") and db is not None:
            try:
                await SchemaIntegrityValidator.check_field_mutation_safe(
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

        temp_schema = schema.copy()
        temp_schema[column_name] = new_meta
        SchemaIntegrityValidator.check_circular_dependencies(temp_schema)

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
