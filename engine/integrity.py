# engine/integrity.py

import re
from typing import Dict, Any, Set, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import or_, select
from uuid import UUID

from triggers.models import Trigger
from analytics.models import AnalyticsWidget
from policy.models import StorefrontPolicies
from notifications.models import (
    NotificationTemplate,
)

from engine.exceptions.integrity import (
    CircularDependencyError,
    SchemaValidationError,
    SchemaDependencyError,
)
from logs.decorators import trace_action

VARIABLE_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)\.([a-zA-Z0-9_\.]+)\s*\}\}")


class SchemaIntegrityValidator:
    """
    Полиция безопасности схемы данных.
    Проверяет, не сломает ли изменение структуры БД существующие формулы, триггеры,
    аналитику, правила доступа витрины (Storefront) и шаблоны уведомлений.
    """

    @classmethod
    @trace_action(name="Integrity::Validate_Notification_Template")
    async def validate_notification_template(
        cls,
        instance_uuid: UUID,
        title: str,
        body: str,
        entity_mappings: Dict[str, Any],
        template_repo: Any,
    ) -> None:
        """
        Парсит текст уведомления и проверяет существование no-code таблиц и колонок в MongoDB.
        Вызывается на этапе POST / PATCH запросов в роутере уведомлений.
        """
        all_text = f"{title or ''} {body or ''}"
        matches = VARIABLE_PATTERN.findall(all_text)

        if not matches:
            return

        mappings = entity_mappings or {}

        for entity_alias, field_path in matches:
            if entity_alias not in mappings:
                raise SchemaValidationError(
                    reason=f"В тексте шаблона используется сущность '{entity_alias}', но для неё отсутствует маппинг в 'entity_mappings'.",
                    invalid_fields=[entity_alias],
                    target_context="entity_mappings",
                )

            template_uuid = mappings[entity_alias]

            template = await template_repo.get_template(
                instance_uuid=str(instance_uuid), template_uuid=str(template_uuid)
            )
            if not template:
                raise SchemaValidationError(
                    reason=f"No-Code таблица с UUID '{template_uuid}' (алиас '{entity_alias}') не найдена в текущем пространстве.",
                    invalid_fields=[entity_alias],
                    target_context="entity_mappings",
                )

            schema = template.get("schema", {})
            base_col = field_path.split(".")[0]
            if base_col not in schema:
                raise SchemaValidationError(
                    reason=f"Поле '{field_path}' не существует в структуре таблицы '{template.get('name')}'.",
                    invalid_fields=[field_path],
                    target_context="notification_template_fields",
                )

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
                if f and (f == column_name or f.startswith(f"{column_name}.")):
                    return True
            return False

        for other_col, meta in current_schema.items():
            if other_col == column_name:
                continue
            if meta.get("type") == "formula":
                used_fields = cls.extract_used_fields(meta.get("ast", {}))
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
                if _is_used(cls.extract_used_fields(trigger_ast or {})):
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
                cls.extract_used_fields(widget.ast_filter)
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
            nt_mappings = nt.entity_mappings or {}
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
    def validate_storefront_policy(
        cls, schema: Dict[str, Any], policy_data: Dict[str, Any]
    ) -> None:
        valid_columns = set(schema.keys())

        def _is_valid_col(col: str) -> bool:
            base_col = col.split(".")[0]
            return base_col in valid_columns

        read_mask: List[str] = policy_data.get("read_mask", [])
        write_mask: List[str] = policy_data.get("write_mask", [])
        read_filters: Dict[str, Any] = policy_data.get("read_filters", {})

        invalid_read = [col for col in read_mask if not _is_valid_col(col)]
        if invalid_read:
            raise SchemaValidationError(
                reason="Несуществующие поля в маске чтения витрины",
                invalid_fields=invalid_read,
                target_context="read_mask",
            )

        invalid_write = [col for col in write_mask if not _is_valid_col(col)]
        if invalid_write:
            raise SchemaValidationError(
                reason="Несуществующие поля в маске записи витрины",
                invalid_fields=invalid_write,
                target_context="write_mask",
            )

        invalid_filters = [col for col in read_filters.keys() if not _is_valid_col(col)]
        if invalid_filters:
            raise SchemaValidationError(
                reason="Несуществующие поля в серверных фильтрах витрины",
                invalid_fields=invalid_filters,
                target_context="read_filters",
            )

    @classmethod
    def extract_used_fields(cls, ast_node: Dict[str, Any]) -> Set[str]:
        if not ast_node or not isinstance(ast_node, dict):
            return set()

        node_type = ast_node.get("type")
        used_fields = set()

        if node_type == "field":
            used_fields.add(ast_node.get("value"))
        elif node_type == "relation_field":
            used_fields.add(ast_node.get("relation_column"))
        elif node_type == "aggregation":
            if isinstance(ast_node.get("filter_value"), dict):
                used_fields.update(
                    cls.extract_used_fields(ast_node.get("filter_value"))
                )
        elif node_type in ("binary_op", "logical_op", "string_op", "date_op"):
            used_fields.update(cls.extract_used_fields(ast_node.get("left", {})))
            used_fields.update(cls.extract_used_fields(ast_node.get("right", {})))
        elif node_type == "condition":
            used_fields.update(cls.extract_used_fields(ast_node.get("condition", {})))
            used_fields.update(cls.extract_used_fields(ast_node.get("true_expr", {})))
            used_fields.update(cls.extract_used_fields(ast_node.get("false_expr", {})))
        elif node_type == "array_reduce":
            used_fields.add(ast_node.get("array_field"))
            used_fields.update(
                cls.extract_used_fields(ast_node.get("item_expression", {}))
            )
            used_fields.update(
                cls.extract_used_fields(ast_node.get("filter_expression", {}))
            )
        elif node_type == "object":
            for child in ast_node.get("fields", {}).values():
                used_fields.update(cls.extract_used_fields(child))
        elif node_type == "query":
            for item in ast_node.get("filters", []):
                if isinstance(item.get("value"), dict):
                    used_fields.update(cls.extract_used_fields(item.get("value")))

        return used_fields

    @classmethod
    @trace_action(name="Integrity::Check_Circular_Deps")
    def check_circular_dependencies(cls, schema: Dict[str, Any]) -> None:
        graph: Dict[str, Set[str]] = {}
        for column_name, meta in schema.items():
            if meta.get("type") == "formula":
                ast = meta.get("ast", {})
                raw_used = cls.extract_used_fields(ast)
                graph[column_name] = {f.split(".")[0] for f in raw_used if f}
            else:
                graph[column_name] = set()

        visited = set()
        rec_stack = set()

        def dfs(node: str) -> bool:
            if node in rec_stack:
                return True
            if node in visited:
                return False

            visited.add(node)
            rec_stack.add(node)

            for neighbor in graph.get(node, []):
                if dfs(neighbor):
                    return True

            rec_stack.remove(node)
            return False

        for node in graph:
            if dfs(node):
                raise CircularDependencyError(
                    f"Обнаружена циклическая зависимость: поле '{node}' зациклено в цепочке вычислений формул."
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

        used_fields = cls.extract_used_fields(ast)
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
