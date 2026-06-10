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
from typing import Any, Dict, List, Tuple
from uuid import UUID, uuid4

from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy.ext.asyncio import AsyncSession

from analytics.repository import AnalyticsWidgetRepository
from analytics.schemas import WidgetCreateRequest
from analytics.widget import WidgetService
from core.services.schema_migration import SchemaMigrationService
from core.services.template import TemplateService
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
    WidgetConfig,
)
from mongo.record import RecordRepository
from mongo.template import TemplateRepository
from mongo.trigger_metadata import TriggerMetadataRepository
from notifications.models import NotificationTemplate
from notifications.repository import NotificationTemplateRepository
from notifications.service import NotificationTemplateService
from policy.repository import PolicyRepository
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


class InstanceSchemaService:
    """Оркестратор выгрузки/загрузки схемы инстанса."""

    def __init__(self, db: AsyncSession, mongo_db: AsyncIOMotorDatabase):
        self.db = db
        self.mongo_db = mongo_db
        self.template_repo = TemplateRepository(mongo_db)
        self.template_service = TemplateService(
            self.template_repo,
            schema_migration=SchemaMigrationService(RecordRepository(mongo_db)),
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
    ) -> Tuple[List[ImportIssue], List[str], Dict[str, List[str]]]:
        """Скрупулёзная проверка целостности + расчёт порядка применения.

        Все ссылки на шаблоны обязаны резолвиться ВНУТРИ bundle — схема
        самодостаточна и после загрузки CRM полностью настроена.
        """
        errors: List[ImportIssue] = []
        warnings: List[str] = []

        template_uuids = [template.uuid for template in bundle.templates]
        template_uuid_set = set(template_uuids)
        template_names = [template.name for template in bundle.templates]

        # Дубликаты внутри bundle
        if len(template_uuid_set) != len(template_uuids):
            errors.append(
                ImportIssue(
                    object_type="template", detail="дубликаты uuid шаблонов в bundle"
                )
            )
        duplicate_names = {
            name for name in template_names if template_names.count(name) > 1
        }
        for name in sorted(duplicate_names):
            errors.append(
                ImportIssue(
                    object_type="template",
                    name=name,
                    detail="дубликат имени шаблона в bundle",
                )
            )

        # Ссылочная целостность шаблонов (relation/relation_list/formula AST)
        template_deps: Dict[str, set] = {}
        for template in bundle.templates:
            refs: set = set()
            _collect_template_refs(template.schema_definition, refs)
            unknown = refs - template_uuid_set
            for ref in sorted(unknown):
                errors.append(
                    ImportIssue(
                        object_type="template",
                        name=template.name,
                        detail=f"схема ссылается на отсутствующий в bundle шаблон {ref}",
                    )
                )
            template_deps[template.uuid] = refs & template_uuid_set

        # Триггеры: source/target + ссылки внутри AST
        for trigger in bundle.triggers:
            refs = {trigger.source_template_uuid}
            if trigger.target_template_uuid:
                refs.add(trigger.target_template_uuid)
            _collect_template_refs(
                {
                    "condition_ast": trigger.condition_ast,
                    "payload_ast": trigger.payload_ast,
                    "action_mapping_ast": trigger.action_mapping_ast,
                },
                refs,
            )
            for ref in sorted(refs - template_uuid_set):
                errors.append(
                    ImportIssue(
                        object_type="trigger",
                        name=trigger.name,
                        detail=f"ссылается на отсутствующий в bundle шаблон {ref}",
                    )
                )

        # Виджеты
        for widget in bundle.widgets:
            refs = {widget.target_template_uuid}
            _collect_template_refs(widget.ast_filter or {}, refs)
            for ref in sorted(refs - template_uuid_set):
                errors.append(
                    ImportIssue(
                        object_type="widget",
                        name=widget.name,
                        detail=f"ссылается на отсутствующий в bundle шаблон {ref}",
                    )
                )

        # Политики — по имени шаблона
        bundle_names = set(template_names)
        for policy in bundle.policies:
            if policy.template_name not in bundle_names:
                errors.append(
                    ImportIssue(
                        object_type="policy",
                        name=policy.template_name,
                        detail="template_name не найден среди шаблонов bundle",
                    )
                )

        # Нотификации
        for notification in bundle.notification_templates:
            if (
                notification.source_template_uuid
                and notification.source_template_uuid not in template_uuid_set
            ):
                errors.append(
                    ImportIssue(
                        object_type="notification",
                        name=notification.name,
                        detail=("source_template_uuid не найден среди шаблонов bundle"),
                    )
                )

        # Порядок шаблонов: независимые → зависимые
        template_order, template_cycles = _topo_sort(template_uuids, template_deps)
        if template_cycles:
            cyclic_names = [
                template.name
                for template in bundle.templates
                if template.uuid in template_cycles
            ]
            warnings.append(
                "циклические relation-связи шаблонов (создаются в исходном "
                f"порядке): {', '.join(sorted(cyclic_names))}"
            )
            template_order += [
                uuid for uuid in template_uuids if uuid in template_cycles
            ]

        # Порядок триггеров: B зависит от A, если A пишет в source-шаблон B
        # (каскад A → событие для B)
        trigger_names = [trigger.name for trigger in bundle.triggers]
        trigger_deps: Dict[str, set] = {name: set() for name in trigger_names}
        for downstream in bundle.triggers:
            for upstream in bundle.triggers:
                if upstream.name == downstream.name:
                    continue
                if (
                    upstream.target_template_uuid
                    and upstream.target_template_uuid == downstream.source_template_uuid
                ):
                    trigger_deps[downstream.name].add(upstream.name)
        trigger_order, trigger_cycles = _topo_sort(trigger_names, trigger_deps)
        if trigger_cycles:
            warnings.append(
                "циклические каскады триггеров (создаются в исходном порядке): "
                + ", ".join(sorted(trigger_cycles))
            )
            trigger_order += [name for name in trigger_names if name in trigger_cycles]

        uuid_to_name = {template.uuid: template.name for template in bundle.templates}
        apply_order = {
            "templates": [uuid_to_name[uuid] for uuid in template_order],
            "notification_templates": [n.name for n in bundle.notification_templates],
            "policies": [p.template_name for p in bundle.policies],
            "widgets": [w.name for w in bundle.widgets],
            "triggers": trigger_order,
        }
        return errors, warnings, apply_order

    # ------------------------------------------------------------------ import

    async def import_schema(
        self,
        instance_uuid: UUID,
        bundle: InstanceSchemaBundle,
        mode: ImportMode,
        user_uuid: UUID,
        dry_run: bool = False,
    ) -> ImportReport:
        errors, warnings, apply_order = self.validate_bundle(bundle)

        # merge: конфликты имён с уже существующими шаблонами — ошибка
        if mode == ImportMode.MERGE and not errors:
            for template in bundle.templates:
                existing = await self.template_repo.find_by_name(
                    instance_uuid=str(instance_uuid), name=template.name
                )
                if existing:
                    errors.append(
                        ImportIssue(
                            object_type="template",
                            name=template.name,
                            detail="шаблон с таким именем уже существует (merge)",
                        )
                    )

        report = ImportReport(
            mode=mode,
            dry_run=dry_run,
            valid=not errors,
            errors=errors,
            warnings=warnings,
            apply_order=apply_order,
        )
        if errors or dry_run:
            return report

        previous_schema = await self.export_schema(instance_uuid)
        if mode == ImportMode.REPLACE:
            report.previous_schema = previous_schema
            report.deleted = await self._wipe_instance_config(
                instance_uuid, previous_schema, user_uuid
            )

        # --- Создание в топологическом порядке -------------------------------
        uuid_by_name = {t.name: t for t in bundle.templates}
        id_map: Dict[str, str] = {}
        created = {key: 0 for key in apply_order}
        try:
            for template_name in apply_order["templates"]:
                template_cfg = uuid_by_name[template_name]
                created_template = await self.template_service.create_template(
                    instance_uuid=instance_uuid,
                    name=template_cfg.name,
                    # Ссылки на уже созданные шаблоны ремапим на лету; на ещё
                    # не созданные (циклы) — доремапим вторым проходом ниже.
                    schema_definition=_remap_uuids(
                        template_cfg.schema_definition, id_map
                    ),
                    user_uuid=user_uuid,
                )
                id_map[template_cfg.uuid] = str(created_template["_id"])
                created["templates"] += 1

            # Финальный проход: дотягиваем ссылки, которые на момент создания
            # указывали на ещё не созданный шаблон (циклы / forward-ссылки) —
            # перезаписываем схему полностью отремапленной версией.
            for template_cfg in bundle.templates:
                refs: set = set()
                _collect_template_refs(template_cfg.schema_definition, refs)
                if not (refs & set(id_map.keys())):
                    continue
                final_schema = _remap_uuids(template_cfg.schema_definition, id_map)
                await self.template_repo.set_template_schema(
                    instance_uuid=str(instance_uuid),
                    template_uuid=id_map[template_cfg.uuid],
                    schema=final_schema,
                )

            for notification in bundle.notification_templates:
                payload = _remap_uuids(
                    notification.model_dump(exclude_none=True), id_map
                )
                if payload.get("source_template_uuid"):
                    # Привязка известна (bundle собран вручную) — полный путь
                    # с валидацией плейсхолдеров против схемы шаблона.
                    await NotificationTemplateService.create_template(
                        db=self.db,
                        instance_uuid=instance_uuid,
                        payload_data=payload,
                        mongo_template_repo=self.template_repo,
                    )
                else:
                    # source_template_uuid не персистится в модели, поэтому
                    # экспорт его не возвращает — binding-валидация для таких
                    # bundle невозможна by design. Шаблон снят с работающего
                    # инстанса (валидировался при создании) — создаём напрямую.
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
                    await self.db.commit()
                created["notification_templates"] += 1

            for policy in bundle.policies:
                from policy.schemas import PolicyCreate

                await self.policy_service.create_policy(
                    instance_uuid=instance_uuid,
                    payload=PolicyCreate(**policy.model_dump()),
                )
                created["policies"] += 1

            for widget in bundle.widgets:
                payload_data = _remap_uuids(widget.model_dump(), id_map)
                await WidgetService.create_widget(
                    instance_uuid=instance_uuid,
                    payload=WidgetCreateRequest(**payload_data),
                    db=self.db,
                )
                created["widgets"] += 1

            triggers_by_name = {trigger.name: trigger for trigger in bundle.triggers}
            for trigger_name in apply_order["triggers"]:
                trigger_cfg = triggers_by_name[trigger_name]
                payload_data = _remap_uuids(trigger_cfg.model_dump(), id_map)
                await self.trigger_admin.create_trigger(
                    instance_uuid=instance_uuid,
                    payload=TriggerCreate(**payload_data),
                    user_uuid=user_uuid,
                )
                created["triggers"] += 1
        except BaseAppException as exc:
            # Stop-on-first-error: фиксируем частичное состояние честно.
            # previous_schema (replace) позволяет откатиться повторным импортом.
            report.valid = False
            report.errors.append(
                ImportIssue(
                    object_type="import",
                    detail=(
                        f"применение остановлено: {exc}. Создано частично — "
                        "см. counters; для отката используйте previous_schema "
                        "в режиме replace."
                    ),
                )
            )

        report.created = created
        report.id_map = id_map
        return report

    # ------------------------------------------------------------------ wipe

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
