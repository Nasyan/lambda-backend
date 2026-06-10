# instance_schema/schemas.py

"""Pydantic-схемы экспорта/импорта конфигурации инстанса (задание 4, 2026-06-10).

Bundle — «огромный json» со ВСЕЙ конфигурацией CRM-инстанса: templates,
triggers, analytics widgets, storefront policies, notification templates.
Данные (records) и история в bundle НЕ входят — это конфигурация, не контент.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field

FORMAT_VERSION = 1


class TemplateConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    uuid: str
    name: str
    schema_definition: Dict[str, Any] = Field(..., alias="schema")


class TriggerConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    name: str
    trigger_type: str
    condition_ast: Optional[Dict[str, Any]] = None
    payload_ast: Dict[str, Any]
    action_mapping_ast: Optional[Dict[str, Any]] = None
    source_template_uuid: str
    target_template_uuid: Optional[str] = None
    target_field: Optional[str] = None
    event_type: Optional[str] = None
    cron_expression: Optional[str] = None
    action_name: Optional[str] = None
    action_params: Optional[Dict[str, Any]] = None


class WidgetConfig(BaseModel):
    name: str
    target_template_uuid: str
    widget_type: str
    chart_config: Dict[str, Any]
    ast_filter: Optional[Dict[str, Any]] = None


class PolicyConfig(BaseModel):
    template_name: str
    defaults: Dict[str, Any] = Field(default_factory=dict)
    read_filters: Dict[str, Any] = Field(default_factory=dict)
    read_mask: List[str] = Field(default_factory=list)
    write_mask: List[str] = Field(default_factory=list)


class NotificationConfig(BaseModel):
    name: str
    title: str
    body: str
    channels: List[str] = Field(default_factory=lambda: ["crm"])
    recipients_config: Dict[str, Any] = Field(default_factory=dict)
    source_template_uuid: Optional[str] = None
    entity_mappings: Optional[Any] = None


class InstanceSchemaBundle(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    format_version: int = FORMAT_VERSION
    templates: List[TemplateConfig] = Field(default_factory=list)
    triggers: List[TriggerConfig] = Field(default_factory=list)
    widgets: List[WidgetConfig] = Field(default_factory=list)
    policies: List[PolicyConfig] = Field(default_factory=list)
    notification_templates: List[NotificationConfig] = Field(default_factory=list)


class ImportMode(str, Enum):
    # Снести текущую конфигурацию и загрузить bundle с нуля. В ответе
    # возвращается previous_schema — её можно импортировать обратно тем же
    # эндпоинтом («заменить всё и загрузить предыдущую»).
    REPLACE = "replace"
    # Добавить объекты bundle поверх существующих (новые UUID, конфликты имён
    # шаблонов считаются ошибкой валидации).
    MERGE = "merge"


class ImportRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    bundle: InstanceSchemaBundle = Field(..., alias="schema")
    mode: ImportMode = ImportMode.MERGE
    # dry_run: только скрупулёзная проверка целостности + план порядка
    # применения, без каких-либо изменений.
    dry_run: bool = False


class ImportIssue(BaseModel):
    object_type: str  # template / trigger / widget / policy / notification
    name: Optional[str] = None
    detail: str


class ImportReport(BaseModel):
    mode: ImportMode
    dry_run: bool
    valid: bool
    errors: List[ImportIssue] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    # Порядок применения, рассчитанный топологической сортировкой
    apply_order: Dict[str, List[str]] = Field(default_factory=dict)
    created: Dict[str, int] = Field(default_factory=dict)
    deleted: Dict[str, int] = Field(default_factory=dict)
    # Маппинг старых UUID шаблонов из bundle на созданные
    id_map: Dict[str, str] = Field(default_factory=dict)
    # Полный экспорт состояния ДО replace-импорта (для отката)
    previous_schema: Optional[InstanceSchemaBundle] = None
