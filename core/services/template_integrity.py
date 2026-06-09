# core/services/template_integrity.py

"""Инфраструктурные проверки целостности схемы (Postgres + Mongo).

TemplateIntegrityService — вторая половина бывшего SchemaIntegrityValidator
(task3, ГЗ-1 Блок A): проверяет каскадные связи таблиц перед их
удалением/изменением, обращаясь к Postgres (триггеры, виджеты, политики
витрины, шаблоны уведомлений) и к Mongo через template_repo.

Чистые in-memory проверки живут в engine/schema_rules.py
(NoCodeSchemaValidator).
"""

import re
from typing import Dict, Any, Optional, Set
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import or_, select
from uuid import UUID

from triggers.models import Trigger
from analytics.models import AnalyticsWidget
from policy.models import StorefrontPolicies
from notifications.models import (
    NotificationTemplate,
)

from engine.schema_rules import NoCodeSchemaValidator
from engine.exceptions.integrity import (
    SchemaValidationError,
    SchemaDependencyError,
)
from logs.decorators import trace_action

VARIABLE_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\.([a-zA-Z0-9_\.]+)\s*\}\}")
NOTIFICATION_PLACEHOLDER_PATTERN = re.compile(r"\{\{\s*([\w\.]+)\s*\}\}")


class TemplateIntegrityService:
    """Полиция безопасности схемы данных (инфраструктурный слой).

    Проверяет, не сломает ли изменение структуры БД существующие формулы,
    триггеры, аналитику, правила доступа витрины (Storefront) и шаблоны
    уведомлений.
    """

    @classmethod
    @trace_action(name="Integrity::Validate_Notification_Template")
    async def validate_notification_template(
        cls,
        instance_uuid: UUID,
        title: str,
        body: str,
        entity_mappings: Optional[Dict[str, Any]],
        template_repo: Any,
        source_template_uuid: Optional[UUID | str] = None,
    ) -> None:
        """
        Парсит текст уведомления и проверяет существование no-code таблиц и колонок в MongoDB.
        Вызывается на этапе POST / PATCH запросов в роутере уведомлений.
        """
        all_text = f"{title or ''} {body or ''}"
        placeholders = sorted(set(NOTIFICATION_PLACEHOLDER_PATTERN.findall(all_text)))

        if not placeholders:
            return

        mappings = entity_mappings or {}
        template_cache: Dict[str, Dict[str, Any]] = {}

        async def _get_schema_template(template_uuid: UUID | str) -> Dict[str, Any]:
            cache_key = str(template_uuid)
            if cache_key in template_cache:
                return template_cache[cache_key]
            template = await template_repo.get_template(
                instance_uuid=str(instance_uuid), template_uuid=cache_key
            )
            template_cache[cache_key] = template
            return template

        async def _validate_field(
            template_uuid: UUID | str,
            field_path: str,
            raw_placeholder: str,
            target_context: str,
        ) -> None:
            template = await _get_schema_template(template_uuid)
            schema = template.get("schema", {})
            base_col = field_path.split(".")[0]
            if base_col not in schema:
                raise SchemaValidationError(
                    reason=(
                        f"Поле '{field_path}' из маски '{{{{{raw_placeholder}}}}}' "
                        f"не существует в структуре таблицы '{template.get('name')}'."
                    ),
                    invalid_fields=[raw_placeholder],
                    target_context=target_context,
                )

        for placeholder in placeholders:
            parts = placeholder.split(".")
            if len(parts) > 1 and parts[0] in mappings:
                await _validate_field(
                    template_uuid=mappings[parts[0]],
                    field_path=".".join(parts[1:]),
                    raw_placeholder=placeholder,
                    target_context="notification_template_fields",
                )
                continue

            if source_template_uuid:
                field_path = cls._normalize_notification_placeholder(placeholder)
                await _validate_field(
                    template_uuid=source_template_uuid,
                    field_path=field_path,
                    raw_placeholder=placeholder,
                    target_context="notification_template_fields",
                )
                continue

            missing_context = parts[0] if len(parts) > 1 else str(placeholder)
            raise SchemaValidationError(
                reason=(
                    f"В тексте шаблона используется маска '{{{{{placeholder}}}}}', "
                    "но не передан source_template_uuid или entity_mappings для проверки CRM-схемы."
                ),
                invalid_fields=[missing_context],
                target_context="notification_template_binding",
            )

    @staticmethod
    def _normalize_notification_placeholder(placeholder: str) -> str:
        for prefix in ("data.", "$new.", "$old."):
            if placeholder.startswith(prefix):
                return placeholder[len(prefix) :]
        return placeholder

    @classmethod
    @trace_action(name="Integrity::Check_Template_Deletion")
    async def check_template_destruction_safe(
        cls,
        instance_uuid: UUID,
        template_uuid: UUID,
        template_name: str,
        db: AsyncSession,
    ) -> None:
        """
        Проверяет, безопасно ли удалить весь шаблон целиком.
        """
        conflicts = []
        structured_details = {
            "triggers": [],
            "widgets": [],
            "storefront_policy": False,
            "notification_templates": [],
        }

        result_triggers = await db.execute(
            select(Trigger).where(
                Trigger.instance_uuid == instance_uuid,
                or_(
                    Trigger.source_template_uuid == template_uuid,
                    Trigger.target_template_uuid == template_uuid,
                ),
            )
        )
        triggers = result_triggers.scalars().all()
        if triggers:
            trigger_names = ", ".join([f"'{t.name}'" for t in triggers])
            conflicts.append(f"активные автоматизации: {trigger_names}")

        result_widgets = await db.execute(
            select(AnalyticsWidget).where(
                AnalyticsWidget.instance_uuid == instance_uuid,
                AnalyticsWidget.target_template_uuid == template_uuid,
            )
        )
        widgets = result_widgets.scalars().all()
        if widgets:
            widget_names = ", ".join([f"'{w.name}'" for w in widgets])
            conflicts.append(f"виджеты аналитики: {widget_names}")

        result_policy = await db.execute(
            select(StorefrontPolicies).where(
                StorefrontPolicies.instance_uuid == instance_uuid,
                StorefrontPolicies.template_name == template_name,
            )
        )
        if result_policy.scalar_one_or_none():
            conflicts.append(
                f"активная интеграция витрины для таблицы '{template_name}'"
            )
            structured_details["storefront_policy"] = True

        result_notifications = await db.execute(
            select(NotificationTemplate).where(
                NotificationTemplate.instance_uuid == instance_uuid
            )
        )
        for nt in result_notifications.scalars().all():
            nt_mappings = getattr(nt, "entity_mappings", None) or {}
            mapped_uuids = {str(v) for v in nt_mappings.values()}
            if str(template_uuid) in mapped_uuids:
                conflicts.append(f"шаблон уведомлений '{nt.name}'")
                structured_details["notification_templates"].append(str(nt.id))

        if conflicts:
            raise SchemaDependencyError(
                message=f"Невозможно удалить таблицу '{template_name}'. К ней привязаны активные зависимости. Сначала удалите их.",
                target_resource=template_name,
                conflicts=conflicts,
                raw_details=structured_details,
            )

    @classmethod
    @trace_action(name="Integrity::Check_Template_Mutation")
    async def check_field_mutation_safe(
        cls,
        instance_uuid: UUID,
        template_uuid: UUID,
        template_name: str,
        column_name: str,
        current_schema: Dict[str, Any],
        db: AsyncSession,
    ) -> None:
        """
        Проверяет, безопасно ли удалить поле или сменить его тип.
        """
        conflicts = []

        def _is_used(used_set: Set[str]) -> bool:
            for f in used_set:
                if not f:
                    continue
                # $old.<field>/$new.<field> в триггерных AST указывают на то же
                # физическое поле схемы — нормализуем префикс state-tracking'а.
                if f.startswith("$old.") or f.startswith("$new."):
                    f = f.split(".", 1)[1]
                elif f in ("$old", "$new"):
                    continue
                if f == column_name or f.startswith(f"{column_name}."):
                    return True
            return False

        for other_col, meta in current_schema.items():
            if other_col == column_name:
                continue
            if meta.get("type") == "formula":
                used_fields = NoCodeSchemaValidator.extract_used_fields(
                    meta.get("ast", {})
                )
                if _is_used(used_fields):
                    conflicts.append(f"Формула в поле '{other_col}'")

        result_triggers = await db.execute(
            select(Trigger).where(
                Trigger.instance_uuid == instance_uuid,
                Trigger.source_template_uuid == template_uuid,
            )
        )
        for trigger in result_triggers.scalars().all():
            trigger_asts = [
                trigger.condition_ast,
                trigger.payload_ast,
                trigger.action_mapping_ast,
            ]
            for trigger_ast in trigger_asts:
                if _is_used(
                    NoCodeSchemaValidator.extract_used_fields(trigger_ast or {})
                ):
                    conflicts.append(f"AST в триггере '{trigger.name}'")
                    break

        result_widgets = await db.execute(
            select(AnalyticsWidget).where(
                AnalyticsWidget.instance_uuid == instance_uuid,
                AnalyticsWidget.target_template_uuid == template_uuid,
            )
        )
        for widget in result_widgets.scalars().all():
            if widget.ast_filter and _is_used(
                NoCodeSchemaValidator.extract_used_fields(widget.ast_filter)
            ):
                conflicts.append(f"Фильтр виджета '{widget.name}'")

            config = widget.chart_config or {}
            used_axes = {
                config.get("axis_x", {}).get("field"),
                config.get("axis_y", {}).get("field"),
            }
            if _is_used(used_axes):
                conflicts.append(f"Оси разметки виджета '{widget.name}'")

        result_policy = await db.execute(
            select(StorefrontPolicies).where(
                StorefrontPolicies.instance_uuid == instance_uuid,
                StorefrontPolicies.template_name == template_name,
            )
        )
        policy = result_policy.scalar_one_or_none()
        if policy:
            if _is_used(set(policy.read_mask or [])):
                conflicts.append("Маска чтения витрины (read_mask)")
            if _is_used(set(policy.write_mask or [])):
                conflicts.append("Маска записи витрины (write_mask)")
            if policy.read_filters and _is_used(set(policy.read_filters.keys())):
                conflicts.append("Серверный фильтр витрины (read_filters)")

        result_notifications = await db.execute(
            select(NotificationTemplate).where(
                NotificationTemplate.instance_uuid == instance_uuid
            )
        )
        for nt in result_notifications.scalars().all():
            nt_mappings = getattr(nt, "entity_mappings", None) or {}
            aliases_for_template = [
                alias
                for alias, t_uuid in nt_mappings.items()
                if str(t_uuid) == str(template_uuid)
            ]
            if aliases_for_template:
                all_text = f"{nt.title or ''} {nt.body or ''}"
                matches = VARIABLE_PATTERN.findall(all_text)
                for entity_alias, field_path in matches:
                    if entity_alias in aliases_for_template:
                        base_col = field_path.split(".")[0]
                        if base_col == column_name:
                            conflicts.append(
                                f"Шаблон уведомлений '{nt.name}' (переменная {{{{{entity_alias}.{field_path}}}}})"
                            )
                            break

        if conflicts:
            raise SchemaDependencyError(
                message=f"Невозможно изменить или удалить поле '{column_name}'. Оно используется в конфигурациях системы.",
                target_resource=f"{template_name}.{column_name}",
                conflicts=conflicts,
            )

    @classmethod
    @trace_action(name="Integrity::Check_Template_Rename")
    async def check_template_rename_safe(
        cls,
        instance_uuid: UUID,
        template_uuid: UUID,
        old_name: str,
        new_name: str,
        db: AsyncSession,
    ) -> None:
        """
        Проверяет, не сломает ли переименование таблицы витрину Storefront.
        """
        conflicts = []
        result_policy = await db.execute(
            select(StorefrontPolicies).where(
                StorefrontPolicies.instance_uuid == instance_uuid,
                StorefrontPolicies.template_name == old_name,
            )
        )
        if result_policy.scalar_one_or_none():
            conflicts.append(f"активная интеграция витрины для таблицы '{old_name}'")

        if conflicts:
            raise SchemaDependencyError(
                message=f"Невозможно переименовать таблицу '{old_name}' в '{new_name}'. Обнаружены зависимости.",
                target_resource=old_name,
                conflicts=conflicts,
            )

    @classmethod
    @trace_action(name="Integrity::Validate_Trigger_AST_Fields")
    async def validate_trigger_ast_fields(
        cls,
        instance_uuid: UUID,
        template_uuid: UUID,
        ast: Dict[str, Any],
        template_repo: Any,
    ) -> None:
        """
        Извлекает все поля из AST триггера и проверяет их физическое существование в схеме No-Code таблицы.
        """
        if not template_uuid or not ast:
            return

        used_fields = NoCodeSchemaValidator.extract_used_fields(ast)
        if not used_fields:
            return

        template = await template_repo.get_template(
            instance_uuid=str(instance_uuid), template_uuid=str(template_uuid)
        )
        if not template:
            raise SchemaValidationError(
                reason=f"No-Code таблица с UUID '{template_uuid}' не найдена.",
                invalid_fields=[str(template_uuid)],
                target_context="trigger_validation",
            )

        schema = template.get("schema", {})

        invalid_fields = []
        for field_path in used_fields:
            if not field_path:
                continue
            base_col = field_path.split(".")[0]
            if base_col not in schema:
                invalid_fields.append(field_path)

        if invalid_fields:
            raise SchemaValidationError(
                reason=f"В условии триггера используются несуществующие в таблице поля: {', '.join(invalid_fields)}",
                invalid_fields=invalid_fields,
                target_context="trigger_ast_fields",
            )
