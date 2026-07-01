# instance_schema/service.py

"""Экспорт/импорт полной конфигурации инстанса (задание 4, 2026-06-10).

Экспорт: собирает все конфигурационные объекты инстанса в один bundle.
Импорт: скрупулёзная проверка целостности ссылок → топологический порядок
(шаблоны без связей → зависимые шаблоны → нотификации → политики → виджеты →
триггеры от независимых к зависимым) → создание через штатные сервисы
(их валидаторы выполняются для каждого объекта) с ремапом UUID шаблонов.

Режимы: merge (поверх существующего) и replace (снести конфиг и загрузить
bundle; previous_schema в ответе позволяет откатиться повторным импортом).
"""

from __future__ import annotations

import logging
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Tuple
from uuid import UUID, uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from analytics.repository import AnalyticsWidgetRepository
from analytics.schemas import WidgetCreateRequest
from analytics.widget import WidgetService
from core.services.schema_migration import SchemaMigrationService
from core.services.template import TemplateService
from engine.ast import parse_ast
from engine.schema_rules import NoCodeSchemaValidator
from exceptions.base import BaseAppException
from instance_schema.schemas import (
    ImportIssue,
    ImportMode,
    ImportReport,
    InstanceSchemaBundle,
    NotificationConfig,
    PolicyConfig,
    TemplateConfig,
    TriggerConfig,
    UnresolvedOp,
    WidgetConfig,
)
from mongo.record import RecordRepository
from mongo.template import TemplateRepository
from mongo.trigger_metadata import TriggerMetadataRepository
from notifications.models import NotificationTemplate
from notifications.repository import NotificationTemplateRepository
from notifications.service import NotificationTemplateService
from policy.repository import PolicyRepository
from policy.schemas import PolicyCreate
from policy.service import PolicyAdminService
from triggers.admin_service import TriggerAdminService
from triggers.repository import TriggerRepository
from triggers.schemas import TriggerCreate

logger = logging.getLogger(__name__)


def _collect_template_refs(value: Any, acc: set) -> None:
    """Рекурсивно собирает все target_template_uuid из произвольного JSON
    (схемы шаблонов, AST триггеров/формул, конфиги виджетов)."""
    if isinstance(value, dict):
        ref = value.get("target_template_uuid")
        if isinstance(ref, str) and ref:
            acc.add(ref)
        for item in value.values():
            _collect_template_refs(item, acc)
    elif isinstance(value, list):
        for item in value:
            _collect_template_refs(item, acc)


def _remap_uuids(value: Any, id_map: Dict[str, str]) -> Any:
    """Глубокая замена старых UUID шаблонов на новые по всему JSON."""
    if isinstance(value, str):
        return id_map.get(value, value)
    if isinstance(value, dict):
        return {key: _remap_uuids(item, id_map) for key, item in value.items()}
    if isinstance(value, list):
        return [_remap_uuids(item, id_map) for item in value]
    return value


def _topo_sort(nodes: List[str], edges: Dict[str, set]) -> Tuple[List[str], List[str]]:
    """Kahn. edges[A] = множество узлов, от которых A зависит (должны идти
    раньше). Возвращает (порядок, узлы_в_циклах). Детерминированно: ties — по
    порядку в nodes."""
    order: List[str] = []
    remaining = {
        node: {dep for dep in edges.get(node, set()) if dep in nodes and dep != node}
        for node in nodes
    }
    ready = [node for node in nodes if not remaining[node]]
    while ready:
        current = ready.pop(0)
        order.append(current)
        for node in nodes:
            if current in remaining.get(node, set()):
                remaining[node].discard(current)
                if not remaining[node] and node not in order and node not in ready:
                    ready.append(node)
    cyclic = [node for node in nodes if node not in order]
    return order, cyclic


RegistryKey = Tuple[str, str]


@dataclass
class Registry:
    keys: Set[RegistryKey] = field(default_factory=set)

    def add(self, keys: Set[RegistryKey]) -> None:
        self.keys.update(keys)


@dataclass
class Operation:
    id: str
    kind: str
    obj_type: str
    obj_name: Optional[str]
    requires: Set[RegistryKey]
    produces: Set[RegistryKey]
    apply: Callable[[], Awaitable[None]]
    report_group: Optional[str] = None
    report_name: Optional[str] = None

    def describe(self) -> str:
        name = f":{self.obj_name}" if self.obj_name else ""
        return f"{self.kind}{name}"


@dataclass
class EngineResult:
    applied: List[Operation]
    pending: List[Operation]


class OperationEngine:
    def __init__(self, operations: List[Operation], registry: Registry):
        self.operations = operations
        self.registry = registry

    async def run(self) -> EngineResult:
        pending = list(self.operations)
        applied: List[Operation] = []
        while pending:
            ran_this_pass = 0
            for operation in list(pending):
                if operation.requires <= self.registry.keys:
                    await operation.apply()
                    self.registry.add(operation.produces)
                    pending.remove(operation)
                    applied.append(operation)
                    ran_this_pass += 1
            if ran_this_pass == 0:
                break
        return EngineResult(applied=applied, pending=pending)


class MongoCompensationLog:
    def __init__(self, template_repo: TemplateRepository):
        self.template_repo = template_repo
        self._undo: List[Callable[[], Awaitable[None]]] = []

    def record_template_insert(self, instance_uuid: UUID, template_uuid: str) -> None:
        async def undo() -> None:
            await self.template_repo.collection.delete_one(
                {"_id": str(template_uuid), "instance_uuid": str(instance_uuid)}
            )

        self._undo.append(undo)

    async def record_schema_before_set(
        self, instance_uuid: UUID, template_uuid: str
    ) -> None:
        document = await self.template_repo.collection.find_one(
            {"_id": str(template_uuid), "instance_uuid": str(instance_uuid)},
            projection={"schema": 1},
        )
        previous_schema = deepcopy((document or {}).get("schema", {}))

        async def undo() -> None:
            await self.template_repo.collection.update_one(
                {"_id": str(template_uuid), "instance_uuid": str(instance_uuid)},
                {"$set": {"schema": previous_schema}},
            )

        self._undo.append(undo)

    def record_template_restore(self, instance_uuid: UUID, template_uuid: str) -> None:
        async def undo() -> None:
            await self.template_repo.restore_template(
                instance_uuid=str(instance_uuid),
                template_uuid=str(template_uuid),
            )

        self._undo.append(undo)

    async def rollback(self) -> None:
        while self._undo:
            undo = self._undo.pop()
            try:
                await undo()
            except Exception:
                logger.exception("Mongo import compensation step failed")


SYSTEM_FIELD_NAMES = {
    "_id",
    "created_at",
    "updated_at",
    "created_by",
    "updated_by",
    "instance_uuid",
    "schema",
}
RELATION_FIELD_TYPES = {"relation", "relation_list"}
FORMULA_FIELD_TYPES = {"formula", "aggregation"}
IMPORT_CREATED_KEYS = [
    "templates",
    "notification_templates",
    "policies",
    "widgets",
    "triggers",
]


def _field_base(field_name: Any) -> Optional[str]:
    if not isinstance(field_name, str) or not field_name:
        return None
    if field_name.startswith("$old.") or field_name.startswith("$new."):
        field_name = field_name.split(".", 1)[1]
    if field_name in ("$old", "$new"):
        return None
    return field_name.split(".", 1)[0]


def _field_dependency_key(
    template_uuid: str,
    field_name: str,
    templates_by_uuid: Dict[str, TemplateConfig],
) -> RegistryKey:
    base = _field_base(field_name)
    if base is None or base in SYSTEM_FIELD_NAMES:
        return ("template", template_uuid)
    meta = templates_by_uuid[template_uuid].schema_definition.get(base, {})
    field_type = meta.get("type")
    if field_type in FORMULA_FIELD_TYPES:
        return ("template_formulas", template_uuid)
    if field_type in RELATION_FIELD_TYPES:
        return ("template_relations", template_uuid)
    return ("template", template_uuid)


def _split_schema(
    schema: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    simple: Dict[str, Any] = {}
    relations: Dict[str, Any] = {}
    formulas: Dict[str, Any] = {}
    for field_name, meta in schema.items():
        field_type = meta.get("type") if isinstance(meta, dict) else None
        if field_type in RELATION_FIELD_TYPES:
            relations[field_name] = meta
        elif field_type in FORMULA_FIELD_TYPES:
            formulas[field_name] = meta
        else:
            simple[field_name] = meta
    return simple, relations, formulas


def _merge_schema_parts(
    simple: Dict[str, Any],
    relations: Dict[str, Any],
    formulas: Dict[str, Any],
) -> Dict[str, Any]:
    merged: Dict[str, Any] = {}
    merged.update(deepcopy(simple))
    merged.update(deepcopy(relations))
    merged.update(deepcopy(formulas))
    return merged


def _strip_embedded_triggers(schema: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = deepcopy(schema)
    for field_meta in cleaned.values():
        if isinstance(field_meta, dict):
            field_meta.pop("triggers", None)
    return cleaned


def _op_key(kind: str, uuid_value: str) -> RegistryKey:
    return (kind, uuid_value)


class InstanceSchemaService:
    """Оркестратор выгрузки/загрузки схемы инстанса."""

    def __init__(self, db: AsyncSession, mongo_db: AsyncIOMotorDatabase):
        self.db = db
        self.mongo_db = mongo_db
        self.template_repo = TemplateRepository(mongo_db)

        # Создаем RecordRepository один раз для переиспользования
        record_repo = RecordRepository(mongo_db)

        # Инициализируем TemplateService через именованные аргументы
        self.template_service = TemplateService(
            template_repo=self.template_repo,
            record_repo=record_repo,
            schema_migration=SchemaMigrationService(record_repo),
            cache=None,  # Передай реальный кэш, если он ожидается, или оставь None
        )

        self.trigger_repo = TriggerRepository(db)
        self.trigger_admin = TriggerAdminService(
            db=db,
            template_repo=self.template_repo,
            trigger_meta_repo=TriggerMetadataRepository(mongo_db),
        )
        self.widget_repo = AnalyticsWidgetRepository(db)
        self.policy_service = PolicyAdminService(self.template_service, db)
        self.policy_repo = PolicyRepository(db)
        self.notification_repo = NotificationTemplateRepository(db)

    # ------------------------------------------------------------------ export

    async def export_schema(self, instance_uuid: UUID) -> InstanceSchemaBundle:
        templates = await self.template_repo.get_all_templates(
            str(instance_uuid), limit=1000
        )
        triggers = await self.trigger_repo.list(instance_uuid)
        widgets = await self.widget_repo.list(instance_uuid)
        policies = await self.policy_repo.list(instance_uuid)
        notifications = await self.notification_repo.list(instance_uuid)

        return InstanceSchemaBundle(
            templates=[
                TemplateConfig(
                    uuid=str(template["_id"]),
                    name=template["name"],
                    schema_definition=template.get("schema", {}),
                )
                for template in templates
            ],
            triggers=[
                TriggerConfig(
                    name=trigger.name,
                    trigger_type=(
                        trigger.trigger_type.value
                        if hasattr(trigger.trigger_type, "value")
                        else str(trigger.trigger_type)
                    ),
                    condition_ast=trigger.condition_ast,
                    payload_ast=trigger.payload_ast,
                    action_mapping_ast=trigger.action_mapping_ast,
                    source_template_uuid=str(trigger.source_template_uuid),
                    target_template_uuid=(
                        str(trigger.target_template_uuid)
                        if trigger.target_template_uuid
                        else None
                    ),
                    target_field=trigger.target_field,
                    event_type=(
                        trigger.event_type.value
                        if hasattr(trigger.event_type, "value")
                        else (str(trigger.event_type) if trigger.event_type else None)
                    ),
                    cron_expression=trigger.cron_expression,
                    action_name=trigger.action_name,
                    action_params=trigger.action_params,
                )
                for trigger in triggers
            ],
            widgets=[
                WidgetConfig(
                    name=widget.name,
                    target_template_uuid=str(widget.target_template_uuid),
                    widget_type=(
                        widget.widget_type.value
                        if hasattr(widget.widget_type, "value")
                        else str(widget.widget_type)
                    ),
                    chart_config=widget.chart_config,
                    ast_filter=widget.ast_filter,
                )
                for widget in widgets
            ],
            policies=[
                PolicyConfig(
                    template_name=policy.template_name,
                    defaults=policy.defaults or {},
                    read_filters=policy.read_filters or {},
                    read_mask=policy.read_mask or [],
                    write_mask=policy.write_mask or [],
                )
                for policy in policies
            ],
            notification_templates=[
                NotificationConfig(
                    name=notification.name,
                    title=notification.title,
                    body=notification.body,
                    channels=list(notification.channels or []),
                    recipients_config=notification.recipients_config or {},
                )
                for notification in notifications
            ],
        )

    # -------------------------------------------------------------- validation

    def validate_bundle(
        self, bundle: InstanceSchemaBundle
    ) -> Tuple[List[ImportIssue], List[str]]:
        """Pure bundle validation: no live DB reads and no writes."""
        errors: List[ImportIssue] = []
        warnings: List[str] = []
        templates_by_uuid = {template.uuid: template for template in bundle.templates}
        templates_by_name = {template.name: template for template in bundle.templates}

        self._validate_duplicate_values(
            errors,
            object_type="template",
            values=[template.uuid for template in bundle.templates],
            detail="дубликат uuid шаблона в bundle",
        )
        self._validate_duplicate_values(
            errors,
            object_type="template",
            values=[template.name for template in bundle.templates],
            detail="дубликат имени шаблона в bundle",
        )
        self._validate_duplicate_values(
            errors,
            object_type="trigger",
            values=[trigger.name for trigger in bundle.triggers],
            detail="дубликат имени триггера в bundle",
        )
        self._validate_duplicate_values(
            errors,
            object_type="widget",
            values=[widget.name for widget in bundle.widgets],
            detail="дубликат имени виджета в bundle",
        )
        self._validate_duplicate_values(
            errors,
            object_type="policy",
            values=[policy.template_name for policy in bundle.policies],
            detail="дубликат политики для template_name в bundle",
        )
        self._validate_duplicate_values(
            errors,
            object_type="notification",
            values=[item.name for item in bundle.notification_templates],
            detail="дубликат имени notification template в bundle",
        )

        for template in bundle.templates:
            self._validate_uuid_value(errors, "template", template.name, template.uuid)
            if not template.name:
                errors.append(
                    ImportIssue(
                        object_type="template",
                        name=template.uuid,
                        detail="имя шаблона обязательно",
                    )
                )
            self._validate_template_schema(errors, template, templates_by_uuid)

        for trigger in bundle.triggers:
            self._validate_trigger_refs(errors, trigger, templates_by_uuid)

        for widget in bundle.widgets:
            self._validate_widget_refs(errors, widget, templates_by_uuid)

        for policy in bundle.policies:
            template = templates_by_name.get(policy.template_name)
            if template is None:
                errors.append(
                    ImportIssue(
                        object_type="policy",
                        name=policy.template_name,
                        detail="template_name не найден среди шаблонов bundle",
                    )
                )
                continue
            for field_name in list(policy.defaults.keys()):
                self._validate_field_ref(
                    errors,
                    "policy",
                    policy.template_name,
                    template.uuid,
                    field_name,
                    templates_by_uuid,
                    context="defaults",
                )
            for field_name in list(policy.read_filters.keys()):
                self._validate_field_ref(
                    errors,
                    "policy",
                    policy.template_name,
                    template.uuid,
                    field_name,
                    templates_by_uuid,
                    context="read_filters",
                )
            for field_name in policy.read_mask + policy.write_mask:
                self._validate_field_ref(
                    errors,
                    "policy",
                    policy.template_name,
                    template.uuid,
                    field_name,
                    templates_by_uuid,
                    context="mask",
                )

        for notification in bundle.notification_templates:
            if notification.source_template_uuid:
                self._validate_template_ref(
                    errors,
                    "notification",
                    notification.name,
                    notification.source_template_uuid,
                    templates_by_uuid,
                    context="source_template_uuid",
                )

        return errors, warnings

    def _validate_duplicate_values(
        self,
        errors: List[ImportIssue],
        object_type: str,
        values: List[str],
        detail: str,
    ) -> None:
        seen: Set[str] = set()
        duplicates: Set[str] = set()
        for value in values:
            if value in seen:
                duplicates.add(value)
            seen.add(value)
        for value in sorted(duplicates):
            errors.append(
                ImportIssue(object_type=object_type, name=value, detail=detail)
            )

    def _validate_uuid_value(
        self,
        errors: List[ImportIssue],
        object_type: str,
        name: Optional[str],
        value: Optional[str],
    ) -> None:
        if not value:
            errors.append(
                ImportIssue(
                    object_type=object_type, name=name, detail="uuid обязателен"
                )
            )
            return
        try:
            UUID(str(value))
        except ValueError:
            errors.append(
                ImportIssue(
                    object_type=object_type,
                    name=name,
                    detail=f"некорректный uuid: {value}",
                )
            )

    def _validate_template_ref(
        self,
        errors: List[ImportIssue],
        object_type: str,
        name: Optional[str],
        template_uuid: Optional[str],
        templates_by_uuid: Dict[str, TemplateConfig],
        context: str,
    ) -> bool:
        if not template_uuid or template_uuid not in templates_by_uuid:
            errors.append(
                ImportIssue(
                    object_type=object_type,
                    name=name,
                    detail=(
                        f"{context} ссылается на отсутствующий в bundle шаблон "
                        f"{template_uuid}"
                    ),
                )
            )
            return False
        return True

    def _validate_field_ref(
        self,
        errors: List[ImportIssue],
        object_type: str,
        name: Optional[str],
        template_uuid: str,
        field_name: Any,
        templates_by_uuid: Dict[str, TemplateConfig],
        context: str,
    ) -> None:
        base = _field_base(field_name)
        if base is None:
            errors.append(
                ImportIssue(
                    object_type=object_type,
                    name=name,
                    detail=f"{context}: некорректная ссылка на поле {field_name}",
                )
            )
            return
        if base in SYSTEM_FIELD_NAMES:
            return
        template = templates_by_uuid.get(template_uuid)
        if template is None:
            self._validate_template_ref(
                errors, object_type, name, template_uuid, templates_by_uuid, context
            )
            return
        if base not in template.schema_definition:
            errors.append(
                ImportIssue(
                    object_type=object_type,
                    name=name,
                    detail=(
                        f"{context}: поле '{field_name}' отсутствует в шаблоне "
                        f"'{template.name}'"
                    ),
                )
            )

    def _validate_ast_shape(
        self,
        errors: List[ImportIssue],
        object_type: str,
        name: Optional[str],
        ast: Optional[Dict[str, Any]],
        context: str,
    ) -> None:
        if ast is None:
            return
        try:
            parse_ast(ast)
        except BaseAppException as exc:
            errors.append(
                ImportIssue(
                    object_type=object_type,
                    name=name,
                    detail=f"{context}: {exc}",
                )
            )

    def _validate_ast_refs(
        self,
        errors: List[ImportIssue],
        object_type: str,
        name: Optional[str],
        ast: Optional[Dict[str, Any]],
        source_template_uuid: str,
        templates_by_uuid: Dict[str, TemplateConfig],
        context: str,
    ) -> None:
        if ast is None:
            return
        self._validate_ast_shape(errors, object_type, name, ast, context)

        def visit(node: Any, current_template_uuid: str, path: str) -> None:
            if isinstance(node, list):
                for index, item in enumerate(node):
                    visit(item, current_template_uuid, f"{path}.{index}")
                return
            if not isinstance(node, dict):
                return

            node_type = node.get("type")
            if node_type == "field":
                self._validate_field_ref(
                    errors,
                    object_type,
                    name,
                    current_template_uuid,
                    node.get("value"),
                    templates_by_uuid,
                    context=path,
                )
                return
            if node_type == "relation_field":
                relation_column = node.get("relation_column")
                self._validate_field_ref(
                    errors,
                    object_type,
                    name,
                    current_template_uuid,
                    relation_column,
                    templates_by_uuid,
                    context=f"{path}.relation_column",
                )
                relation_base = _field_base(relation_column)
                current_template = templates_by_uuid.get(current_template_uuid)
                relation_meta = (
                    current_template.schema_definition.get(relation_base or "", {})
                    if current_template
                    else {}
                )
                target_uuid = relation_meta.get("target_template_uuid")
                if target_uuid and self._validate_template_ref(
                    errors,
                    object_type,
                    name,
                    target_uuid,
                    templates_by_uuid,
                    context=f"{path}.target_template_uuid",
                ):
                    self._validate_field_ref(
                        errors,
                        object_type,
                        name,
                        target_uuid,
                        node.get("target_field"),
                        templates_by_uuid,
                        context=f"{path}.target_field",
                    )
                return
            if node_type == "aggregation":
                target_uuid = node.get("target_template_uuid")
                if self._validate_template_ref(
                    errors,
                    object_type,
                    name,
                    target_uuid,
                    templates_by_uuid,
                    context=f"{path}.target_template_uuid",
                ):
                    self._validate_field_ref(
                        errors,
                        object_type,
                        name,
                        target_uuid,
                        node.get("filter_field"),
                        templates_by_uuid,
                        context=f"{path}.filter_field",
                    )
                    if node.get("agg_field"):
                        self._validate_field_ref(
                            errors,
                            object_type,
                            name,
                            target_uuid,
                            node.get("agg_field"),
                            templates_by_uuid,
                            context=f"{path}.agg_field",
                        )
                visit(node.get("filter_value"), current_template_uuid, f"{path}.filter_value")
                return
            if node_type == "query":
                target_uuid = node.get("target_template_uuid")
                target_ok = self._validate_template_ref(
                    errors,
                    object_type,
                    name,
                    target_uuid,
                    templates_by_uuid,
                    context=f"{path}.target_template_uuid",
                )
                for index, query_filter in enumerate(node.get("filters", [])):
                    if target_ok:
                        self._validate_field_ref(
                            errors,
                            object_type,
                            name,
                            target_uuid,
                            query_filter.get("field"),
                            templates_by_uuid,
                            context=f"{path}.filters.{index}.field",
                        )
                    visit(
                        query_filter.get("value"),
                        current_template_uuid,
                        f"{path}.filters.{index}.value",
                    )
                if target_ok:
                    for field_name in node.get("return_fields") or []:
                        self._validate_field_ref(
                            errors,
                            object_type,
                            name,
                            target_uuid,
                            field_name,
                            templates_by_uuid,
                            context=f"{path}.return_fields",
                        )
                return

            for key, value in node.items():
                if isinstance(value, (dict, list)):
                    visit(value, current_template_uuid, f"{path}.{key}")

        visit(ast, source_template_uuid, context)

    def _validate_template_schema(
        self,
        errors: List[ImportIssue],
        template: TemplateConfig,
        templates_by_uuid: Dict[str, TemplateConfig],
    ) -> None:
        field_names = [field_name.strip() for field_name in template.schema_definition]
        self._validate_duplicate_values(
            errors,
            object_type="template_field",
            values=field_names,
            detail=f"дубликат имени поля внутри шаблона '{template.name}'",
        )
        try:
            NoCodeSchemaValidator.validate_definition(template.schema_definition)
            NoCodeSchemaValidator.check_circular_dependencies(template.schema_definition)
        except BaseAppException as exc:
            errors.append(
                ImportIssue(
                    object_type="template",
                    name=template.name,
                    detail=f"некорректная схема: {exc}",
                )
            )

        for field_name, field_meta in template.schema_definition.items():
            if not isinstance(field_meta, dict):
                continue
            field_type = field_meta.get("type")
            if field_type in RELATION_FIELD_TYPES:
                self._validate_template_ref(
                    errors,
                    "template",
                    template.name,
                    field_meta.get("target_template_uuid"),
                    templates_by_uuid,
                    context=f"{field_name}.target_template_uuid",
                )
            if field_type in FORMULA_FIELD_TYPES:
                self._validate_ast_refs(
                    errors,
                    "template",
                    template.name,
                    field_meta.get("ast"),
                    template.uuid,
                    templates_by_uuid,
                    context=f"{field_name}.ast",
                )

    def _validate_trigger_refs(
        self,
        errors: List[ImportIssue],
        trigger: TriggerConfig,
        templates_by_uuid: Dict[str, TemplateConfig],
    ) -> None:
        source_ok = self._validate_template_ref(
            errors,
            "trigger",
            trigger.name,
            trigger.source_template_uuid,
            templates_by_uuid,
            context="source_template_uuid",
        )
        target_ok = True
        if trigger.target_template_uuid:
            target_ok = self._validate_template_ref(
                errors,
                "trigger",
                trigger.name,
                trigger.target_template_uuid,
                templates_by_uuid,
                context="target_template_uuid",
            )
        if target_ok and trigger.target_field and trigger.target_template_uuid:
            self._validate_field_ref(
                errors,
                "trigger",
                trigger.name,
                trigger.target_template_uuid,
                trigger.target_field,
                templates_by_uuid,
                context="target_field",
            )
        if source_ok:
            for context, ast in (
                ("condition_ast", trigger.condition_ast),
                ("payload_ast", trigger.payload_ast),
                ("action_mapping_ast", trigger.action_mapping_ast),
            ):
                self._validate_ast_refs(
                    errors,
                    "trigger",
                    trigger.name,
                    ast,
                    trigger.source_template_uuid,
                    templates_by_uuid,
                    context=context,
                )
        action_refs: Set[str] = set()
        _collect_template_refs(trigger.action_params or {}, action_refs)
        for ref in action_refs:
            self._validate_template_ref(
                errors,
                "trigger",
                trigger.name,
                ref,
                templates_by_uuid,
                context="action_params.target_template_uuid",
            )

    def _validate_widget_refs(
        self,
        errors: List[ImportIssue],
        widget: WidgetConfig,
        templates_by_uuid: Dict[str, TemplateConfig],
    ) -> None:
        target_ok = self._validate_template_ref(
            errors,
            "widget",
            widget.name,
            widget.target_template_uuid,
            templates_by_uuid,
            context="target_template_uuid",
        )
        if target_ok:
            config = widget.chart_config or {}
            for context, field_name in (
                ("chart_config.axis_x.field", (config.get("axis_x") or {}).get("field")),
                ("chart_config.axis_y.field", (config.get("axis_y") or {}).get("field")),
                ("chart_config.unwind_field", config.get("unwind_field")),
            ):
                if field_name:
                    self._validate_field_ref(
                        errors,
                        "widget",
                        widget.name,
                        widget.target_template_uuid,
                        field_name,
                        templates_by_uuid,
                        context=context,
                    )
            self._validate_ast_refs(
                errors,
                "widget",
                widget.name,
                widget.ast_filter,
                widget.target_template_uuid,
                templates_by_uuid,
                context="ast_filter",
            )

    # ------------------------------------------------------------------ import

    async def import_schema(
        self,
        instance_uuid: UUID,
        bundle: InstanceSchemaBundle,
        mode: ImportMode,
        user_uuid: UUID,
        dry_run: bool = False,
    ) -> ImportReport:
        errors, warnings = self.validate_bundle(bundle)
        report = ImportReport(
            mode=mode,
            dry_run=dry_run,
            valid=not errors,
            errors=errors,
            warnings=warnings,
            apply_order=self._empty_apply_order(),
        )
        if errors:
            return report

        if mode == ImportMode.MERGE:
            await self._validate_merge_conflicts(instance_uuid, bundle, report.errors)
            if report.errors:
                report.valid = False
                return report

        if mode == ImportMode.REPLACE:
            report.previous_schema = await self.export_schema(instance_uuid)

        id_map = {template.uuid: str(uuid4()) for template in bundle.templates}
        report.id_map = id_map
        compensation = MongoCompensationLog(self.template_repo)
        registry = Registry()
        operations = self._build_operations(
            instance_uuid=instance_uuid,
            bundle=bundle,
            user_uuid=user_uuid,
            id_map=id_map,
            compensation=compensation,
        )

        try:
            if mode == ImportMode.REPLACE and report.previous_schema is not None:
                report.deleted = await self._wipe_instance_config_atomic(
                    instance_uuid=instance_uuid,
                    current=report.previous_schema,
                    compensation=compensation,
                )

            result = await OperationEngine(operations, registry).run()
            report.apply_order = self._apply_order_from_operations(result.applied)

            if result.pending:
                report.valid = False
                report.created = {}
                report.unresolved, report.cycles = self._diagnose_pending(
                    pending=result.pending,
                    registry=registry,
                    operations=operations,
                )
                report.errors.append(
                    ImportIssue(
                        object_type="import",
                        detail="граф импорта не удалось полностью разрешить",
                    )
                )
                await self.db.rollback()
                await compensation.rollback()
                return report

            if dry_run:
                await self.db.rollback()
                await compensation.rollback()
                report.created = {}
                return report

            await self.db.commit()
            report.created = self._created_counts(bundle)
            return report
        except (BaseAppException, ValidationError) as exc:
            await self.db.rollback()
            await compensation.rollback()
            report.valid = False
            report.created = {}
            object_type = (
                "merge_conflict"
                if mode == ImportMode.MERGE
                and getattr(exc, "status_code", None) == 409
                else "import"
            )
            report.errors.append(
                ImportIssue(
                    object_type=object_type,
                    detail=f"применение остановлено и полностью откатано: {exc}",
                )
            )
            return report
        except Exception:
            await self.db.rollback()
            await compensation.rollback()
            raise

    async def _validate_merge_conflicts(
        self,
        instance_uuid: UUID,
        bundle: InstanceSchemaBundle,
        errors: List[ImportIssue],
    ) -> None:
        for template in bundle.templates:
            existing = await self.template_repo.find_by_name(
                instance_uuid=str(instance_uuid), name=template.name
            )
            if existing:
                errors.append(
                    ImportIssue(
                        object_type="merge_conflict",
                        name=template.name,
                        detail="шаблон с таким именем уже существует (merge)",
                    )
                )
        existing_triggers = {trigger.name for trigger in await self.trigger_repo.list(instance_uuid)}
        for trigger in bundle.triggers:
            if trigger.name in existing_triggers:
                errors.append(
                    ImportIssue(
                        object_type="merge_conflict",
                        name=trigger.name,
                        detail="триггер с таким именем уже существует (merge)",
                    )
                )
        existing_widgets = {widget.name for widget in await self.widget_repo.list(instance_uuid)}
        for widget in bundle.widgets:
            if widget.name in existing_widgets:
                errors.append(
                    ImportIssue(
                        object_type="merge_conflict",
                        name=widget.name,
                        detail="виджет с таким именем уже существует (merge)",
                    )
                )
        existing_policies = {
            policy.template_name for policy in await self.policy_repo.list(instance_uuid)
        }
        for policy in bundle.policies:
            if policy.template_name in existing_policies:
                errors.append(
                    ImportIssue(
                        object_type="merge_conflict",
                        name=policy.template_name,
                        detail="политика для template_name уже существует (merge)",
                    )
                )
        existing_notifications = {
            notification.name
            for notification in await self.notification_repo.list(instance_uuid)
        }
        for notification in bundle.notification_templates:
            if notification.name in existing_notifications:
                errors.append(
                    ImportIssue(
                        object_type="merge_conflict",
                        name=notification.name,
                        detail="notification template с таким именем уже существует (merge)",
                    )
                )

    def _build_operations(
        self,
        instance_uuid: UUID,
        bundle: InstanceSchemaBundle,
        user_uuid: UUID,
        id_map: Dict[str, str],
        compensation: MongoCompensationLog,
    ) -> List[Operation]:
        operations: List[Operation] = []
        templates_by_uuid = {template.uuid: template for template in bundle.templates}
        templates_by_name = {template.name: template for template in bundle.templates}
        created_triggers: Dict[str, Any] = {}

        for template in bundle.templates:
            cleaned_schema = _strip_embedded_triggers(template.schema_definition)
            simple, relations, formulas = _split_schema(cleaned_schema)
            old_uuid = template.uuid
            new_uuid = id_map[old_uuid]

            async def apply_shell(
                template: TemplateConfig = template,
                simple: Dict[str, Any] = simple,
                new_uuid: str = new_uuid,
            ) -> None:
                await self.template_service.create_template(
                    instance_uuid=instance_uuid,
                    name=template.name,
                    schema_definition=_remap_uuids(simple, id_map),
                    user_uuid=user_uuid,
                    template_uuid=UUID(new_uuid),
                )
                compensation.record_template_insert(instance_uuid, new_uuid)

            operations.append(
                Operation(
                    id=f"template:shell:{old_uuid}",
                    kind="template:shell",
                    obj_type="template",
                    obj_name=template.name,
                    requires=set(),
                    produces={_op_key("template", old_uuid)},
                    apply=apply_shell,
                )
            )

            relation_requires = {_op_key("template", old_uuid)}
            for field_meta in relations.values():
                target_uuid = field_meta.get("target_template_uuid")
                if target_uuid:
                    relation_requires.add(_op_key("template", target_uuid))

            async def apply_relations(
                simple: Dict[str, Any] = simple,
                relations: Dict[str, Any] = relations,
                new_uuid: str = new_uuid,
            ) -> None:
                if not relations:
                    return
                schema = _merge_schema_parts(simple, relations, {})
                await self._set_template_schema_with_compensation(
                    instance_uuid,
                    new_uuid,
                    _remap_uuids(schema, id_map),
                    compensation,
                )

            operations.append(
                Operation(
                    id=f"template:relations:{old_uuid}",
                    kind="template:relations",
                    obj_type="template",
                    obj_name=template.name,
                    requires=relation_requires,
                    produces={_op_key("template_relations", old_uuid)},
                    apply=apply_relations,
                )
            )

            formula_requires = {_op_key("template_relations", old_uuid)}
            formula_requires.update(
                self._formula_dependency_keys(template, formulas, templates_by_uuid)
            )

            async def apply_formulas(
                simple: Dict[str, Any] = simple,
                relations: Dict[str, Any] = relations,
                formulas: Dict[str, Any] = formulas,
                new_uuid: str = new_uuid,
            ) -> None:
                if not formulas:
                    return
                schema = _merge_schema_parts(simple, relations, formulas)
                await self._set_template_schema_with_compensation(
                    instance_uuid,
                    new_uuid,
                    _remap_uuids(schema, id_map),
                    compensation,
                )

            operations.append(
                Operation(
                    id=f"template:formulas:{old_uuid}",
                    kind="template:formulas",
                    obj_type="template",
                    obj_name=template.name,
                    requires=formula_requires,
                    produces={_op_key("template_formulas", old_uuid)},
                    apply=apply_formulas,
                )
            )

            async def apply_finalize(
                cleaned_schema: Dict[str, Any] = cleaned_schema,
                new_uuid: str = new_uuid,
            ) -> None:
                final_schema = _remap_uuids(cleaned_schema, id_map)
                NoCodeSchemaValidator.check_circular_dependencies(final_schema)
                await self._set_template_schema_with_compensation(
                    instance_uuid,
                    new_uuid,
                    final_schema,
                    compensation,
                )

            operations.append(
                Operation(
                    id=f"template:finalize:{old_uuid}",
                    kind="template:finalize",
                    obj_type="template",
                    obj_name=template.name,
                    requires={
                        _op_key("template_relations", old_uuid),
                        _op_key("template_formulas", old_uuid),
                    },
                    produces={_op_key("template_complete", old_uuid)},
                    apply=apply_finalize,
                    report_group="templates",
                    report_name=template.name,
                )
            )

        for notification in bundle.notification_templates:
            requires: Set[RegistryKey] = set()
            if notification.source_template_uuid:
                requires.add(_op_key("template_complete", notification.source_template_uuid))

            async def apply_notification(
                notification: NotificationConfig = notification,
            ) -> None:
                payload = _remap_uuids(
                    notification.model_dump(exclude_none=True), id_map
                )
                if payload.get("source_template_uuid"):
                    await NotificationTemplateService.create_template(
                        db=self.db,
                        instance_uuid=instance_uuid,
                        payload_data=payload,
                        mongo_template_repo=self.template_repo,
                        commit=False,
                    )
                else:
                    self.notification_repo.add(
                        NotificationTemplate(
                            uuid=uuid4(),
                            instance_uuid=instance_uuid,
                            name=payload["name"],
                            title=payload["title"],
                            body=payload["body"],
                            channels=payload.get("channels") or ["crm"],
                            recipients_config=payload.get("recipients_config") or {},
                        )
                    )
                    await self.db.flush()

            operations.append(
                Operation(
                    id=f"notification:create:{notification.name}",
                    kind="notification:create",
                    obj_type="notification",
                    obj_name=notification.name,
                    requires=requires,
                    produces={_op_key("notification", notification.name)},
                    apply=apply_notification,
                    report_group="notification_templates",
                    report_name=notification.name,
                )
            )

        for policy in bundle.policies:
            template = templates_by_name[policy.template_name]

            async def apply_policy(policy: PolicyConfig = policy) -> None:
                await self.policy_service.create_policy(
                    instance_uuid=instance_uuid,
                    payload=PolicyCreate(**policy.model_dump()),
                    commit=False,
                )

            operations.append(
                Operation(
                    id=f"policy:create:{policy.template_name}",
                    kind="policy:create",
                    obj_type="policy",
                    obj_name=policy.template_name,
                    requires={_op_key("template_complete", template.uuid)},
                    produces={_op_key("policy", policy.template_name)},
                    apply=apply_policy,
                    report_group="policies",
                    report_name=policy.template_name,
                )
            )

        for widget in bundle.widgets:
            refs = {widget.target_template_uuid}
            _collect_template_refs(widget.ast_filter or {}, refs)
            requires = {_op_key("template_complete", ref) for ref in refs}

            async def apply_widget(widget: WidgetConfig = widget) -> None:
                payload_data = _remap_uuids(widget.model_dump(), id_map)
                await WidgetService.create_widget(
                    instance_uuid=instance_uuid,
                    payload=WidgetCreateRequest(**payload_data),
                    db=self.db,
                    commit=False,
                )

            operations.append(
                Operation(
                    id=f"widget:create:{widget.name}",
                    kind="widget:create",
                    obj_type="widget",
                    obj_name=widget.name,
                    requires=requires,
                    produces={_op_key("widget", widget.name)},
                    apply=apply_widget,
                    report_group="widgets",
                    report_name=widget.name,
                )
            )

        for trigger in bundle.triggers:
            refs = {trigger.source_template_uuid}
            if trigger.target_template_uuid:
                refs.add(trigger.target_template_uuid)
            _collect_template_refs(trigger.condition_ast or {}, refs)
            _collect_template_refs(trigger.payload_ast or {}, refs)
            _collect_template_refs(trigger.action_mapping_ast or {}, refs)
            _collect_template_refs(trigger.action_params or {}, refs)
            requires = {_op_key("template_complete", ref) for ref in refs}

            async def apply_trigger(trigger: TriggerConfig = trigger) -> None:
                payload_data = _remap_uuids(
                    trigger.model_dump(exclude_none=True), id_map
                )
                db_trigger = await self.trigger_admin.create_trigger(
                    instance_uuid=instance_uuid,
                    payload=TriggerCreate(**payload_data),
                    user_uuid=user_uuid,
                    commit=False,
                    inject_schema=False,
                )
                created_triggers[trigger.name] = db_trigger

            operations.append(
                Operation(
                    id=f"trigger:create:{trigger.name}",
                    kind="trigger:create",
                    obj_type="trigger",
                    obj_name=trigger.name,
                    requires=requires,
                    produces={_op_key("trigger", trigger.name)},
                    apply=apply_trigger,
                    report_group="triggers",
                    report_name=trigger.name,
                )
            )

            if trigger.target_field and trigger.target_template_uuid:
                async def apply_trigger_inject(trigger: TriggerConfig = trigger) -> None:
                    db_trigger = created_triggers[trigger.name]
                    await compensation.record_schema_before_set(
                        instance_uuid, id_map[trigger.target_template_uuid]
                    )
                    await self.trigger_admin.trigger_meta_repo.inject_trigger_to_schema(
                        instance_uuid=str(instance_uuid),
                        template_uuid=id_map[trigger.target_template_uuid],
                        column_name=trigger.target_field,
                        trigger_data={
                            "trigger_id": str(db_trigger.id),
                            "trigger_type": db_trigger.trigger_type,
                            "event": db_trigger.event_type or "onCalculate",
                            "target_field": trigger.target_field,
                        },
                        user_uuid=str(user_uuid),
                    )

                operations.append(
                    Operation(
                        id=f"trigger:inject:{trigger.name}",
                        kind="trigger:inject",
                        obj_type="trigger",
                        obj_name=trigger.name,
                        requires={
                            _op_key("trigger", trigger.name),
                            _op_key("template_complete", trigger.target_template_uuid),
                        },
                        produces={_op_key("trigger_injected", trigger.name)},
                        apply=apply_trigger_inject,
                    )
                )

        return operations

    def _formula_dependency_keys(
        self,
        template: TemplateConfig,
        formulas: Dict[str, Any],
        templates_by_uuid: Dict[str, TemplateConfig],
    ) -> Set[RegistryKey]:
        dependencies: Set[RegistryKey] = set()
        for field_meta in formulas.values():
            dependencies.update(
                self._ast_dependency_keys(
                    field_meta.get("ast"), template.uuid, templates_by_uuid
                )
            )
        dependencies.discard(_op_key("template_formulas", template.uuid))
        return dependencies

    def _ast_dependency_keys(
        self,
        ast: Optional[Dict[str, Any]],
        source_template_uuid: str,
        templates_by_uuid: Dict[str, TemplateConfig],
    ) -> Set[RegistryKey]:
        dependencies: Set[RegistryKey] = set()

        def add_field(template_uuid: Optional[str], field_name: Any) -> None:
            if not template_uuid or template_uuid not in templates_by_uuid:
                return
            base = _field_base(field_name)
            if base is None:
                return
            dependencies.add(
                _field_dependency_key(template_uuid, base, templates_by_uuid)
            )

        def visit(node: Any, current_template_uuid: str) -> None:
            if isinstance(node, list):
                for item in node:
                    visit(item, current_template_uuid)
                return
            if not isinstance(node, dict):
                return
            node_type = node.get("type")
            if node_type == "field":
                add_field(current_template_uuid, node.get("value"))
                return
            if node_type == "relation_field":
                relation_column = node.get("relation_column")
                add_field(current_template_uuid, relation_column)
                relation_base = _field_base(relation_column)
                relation_meta = (
                    templates_by_uuid[current_template_uuid]
                    .schema_definition
                    .get(relation_base or "", {})
                )
                target_uuid = relation_meta.get("target_template_uuid")
                add_field(target_uuid, node.get("target_field"))
                return
            if node_type == "aggregation":
                target_uuid = node.get("target_template_uuid")
                add_field(target_uuid, node.get("filter_field"))
                if node.get("agg_field"):
                    add_field(target_uuid, node.get("agg_field"))
                visit(node.get("filter_value"), current_template_uuid)
                return
            if node_type == "query":
                target_uuid = node.get("target_template_uuid")
                for query_filter in node.get("filters", []):
                    add_field(target_uuid, query_filter.get("field"))
                    visit(query_filter.get("value"), current_template_uuid)
                for field_name in node.get("return_fields") or []:
                    add_field(target_uuid, field_name)
                return
            for value in node.values():
                if isinstance(value, (dict, list)):
                    visit(value, current_template_uuid)

        visit(ast, source_template_uuid)
        return dependencies

    async def _set_template_schema_with_compensation(
        self,
        instance_uuid: UUID,
        template_uuid: str,
        schema: Dict[str, Any],
        compensation: MongoCompensationLog,
    ) -> None:
        await compensation.record_schema_before_set(instance_uuid, template_uuid)
        await self.template_repo.set_template_schema(
            instance_uuid=str(instance_uuid),
            template_uuid=template_uuid,
            schema=schema,
        )

    def _empty_apply_order(self) -> Dict[str, List[str]]:
        return {key: [] for key in IMPORT_CREATED_KEYS}

    def _apply_order_from_operations(
        self, operations: List[Operation]
    ) -> Dict[str, List[str]]:
        apply_order = self._empty_apply_order()
        for operation in operations:
            if operation.report_group and operation.report_name:
                apply_order[operation.report_group].append(operation.report_name)
        return apply_order

    def _created_counts(self, bundle: InstanceSchemaBundle) -> Dict[str, int]:
        return {
            "templates": len(bundle.templates),
            "notification_templates": len(bundle.notification_templates),
            "policies": len(bundle.policies),
            "widgets": len(bundle.widgets),
            "triggers": len(bundle.triggers),
        }

    def _diagnose_pending(
        self,
        pending: List[Operation],
        registry: Registry,
        operations: List[Operation],
    ) -> Tuple[List[UnresolvedOp], List[List[str]]]:
        producer_by_key: Dict[RegistryKey, str] = {}
        for operation in operations:
            for key in operation.produces:
                producer_by_key[key] = operation.id

        pending_ids = {operation.id for operation in pending}
        graph: Dict[str, Set[str]] = {operation.id: set() for operation in pending}
        for operation in pending:
            for missing_key in operation.requires - registry.keys:
                producer = producer_by_key.get(missing_key)
                if producer in pending_ids:
                    graph[operation.id].add(producer)

        cycles = self._strongly_connected_components(graph)
        cycle_nodes = {node for cycle in cycles for node in cycle}
        unresolved = [
            UnresolvedOp(
                op_id=operation.id,
                obj_type=operation.obj_type,
                obj_name=operation.obj_name,
                missing=sorted(operation.requires - registry.keys),
                reason="cycle" if operation.id in cycle_nodes else "missing_dependency",
            )
            for operation in pending
        ]
        return unresolved, cycles

    def _strongly_connected_components(
        self, graph: Dict[str, Set[str]]
    ) -> List[List[str]]:
        index = 0
        stack: List[str] = []
        on_stack: Set[str] = set()
        indices: Dict[str, int] = {}
        lowlinks: Dict[str, int] = {}
        cycles: List[List[str]] = []

        def strongconnect(node: str) -> None:
            nonlocal index
            indices[node] = index
            lowlinks[node] = index
            index += 1
            stack.append(node)
            on_stack.add(node)

            for neighbor in graph.get(node, set()):
                if neighbor not in indices:
                    strongconnect(neighbor)
                    lowlinks[node] = min(lowlinks[node], lowlinks[neighbor])
                elif neighbor in on_stack:
                    lowlinks[node] = min(lowlinks[node], indices[neighbor])

            if lowlinks[node] == indices[node]:
                component: List[str] = []
                while True:
                    item = stack.pop()
                    on_stack.remove(item)
                    component.append(item)
                    if item == node:
                        break
                if len(component) > 1 or node in graph.get(node, set()):
                    cycles.append(sorted(component))

        for node in graph:
            if node not in indices:
                strongconnect(node)
        return cycles

    # ------------------------------------------------------------------ wipe

    async def _wipe_instance_config_atomic(
        self,
        instance_uuid: UUID,
        current: InstanceSchemaBundle,
        compensation: MongoCompensationLog,
    ) -> Dict[str, int]:
        """Replace-mode wipe under the caller-owned transaction.

        Postgres deletes are rolled back by the session transaction. Mongo template
        soft deletes are recorded so they can be restored if the import fails or is
        a dry-run.
        """
        deleted = {
            "triggers": 0,
            "widgets": 0,
            "policies": 0,
            "notification_templates": 0,
            "templates": 0,
        }

        for trigger in await self.trigger_repo.list(instance_uuid):
            await self.db.delete(trigger)
            deleted["triggers"] += 1

        for widget in await self.widget_repo.list(instance_uuid):
            await self.db.delete(widget)
            deleted["widgets"] += 1

        for policy in await self.policy_repo.list(instance_uuid):
            await self.db.delete(policy)
            deleted["policies"] += 1

        for notification in await self.notification_repo.list(instance_uuid):
            await self.db.delete(notification)
            deleted["notification_templates"] += 1

        await self.db.flush()

        for template in current.templates:
            await self.template_repo.delete_template(
                instance_uuid=str(instance_uuid),
                template_uuid=template.uuid,
            )
            compensation.record_template_restore(instance_uuid, template.uuid)
            deleted["templates"] += 1

        return deleted

    async def _wipe_instance_config(
        self,
        instance_uuid: UUID,
        current: InstanceSchemaBundle,
        user_uuid: UUID,
    ) -> Dict[str, int]:
        """Replace-режим: удалить конфигурацию в безопасном порядке —
        зависимые объекты раньше шаблонов. Records НЕ трогаем: они уходят в
        soft-deleted вместе со своими шаблонами и восстановимы restore'ом."""
        deleted = {
            "triggers": 0,
            "widgets": 0,
            "policies": 0,
            "notification_templates": 0,
            "templates": 0,
        }

        for trigger in await self.trigger_repo.list(instance_uuid):
            await self.trigger_admin.delete_trigger(
                instance_uuid, trigger.id, user_uuid
            )
            deleted["triggers"] += 1

        for widget in await self.widget_repo.list(instance_uuid):
            await self.widget_repo.delete(widget)
            deleted["widgets"] += 1
        await self.db.commit()

        for policy in await self.policy_repo.list(instance_uuid):
            await self.policy_service.delete_policy(instance_uuid, policy.id)
            deleted["policies"] += 1

        for notification in await self.notification_repo.list(instance_uuid):
            await NotificationTemplateService.delete_template(
                db=self.db,
                instance_uuid=instance_uuid,
                template_uuid=notification.uuid,
            )
            deleted["notification_templates"] += 1

        for template in current.templates:
            # Зависимые объекты уже удалены — integrity-чек не нужен (db=None).
            await self.template_service.delete_template(
                instance_uuid=instance_uuid,
                template_uuid=UUID(template.uuid),
                db=None,
            )
            deleted["templates"] += 1

        return deleted
