# notifications/views.py
from datetime import datetime
from typing import Any, Dict, List
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field


class TemplateCreate(BaseModel):
    name: str = Field(
        ..., description="Техническое имя шаблона для отображения в админке"
    )
    title: str = Field(
        ..., description="Шаблон заголовка с поддержкой масок {{data.field}}"
    )
    body: str = Field(
        ..., description="Шаблон тела уведомления с поддержкой масок {{data.field}}"
    )
    channels: List[str] = Field(
        default=["crm"], description="Список каналов отправки (crm, email, telegram)"
    )
    recipients_config: Dict[str, Any] = Field(
        default_factory=dict,
        description="Конфигурация получателей: статические UUID или правила вычисления",
    )


class TemplateUpdate(BaseModel):
    name: str | None = None
    title: str | None = None
    body: str | None = None
    channels: List[str] | None = None
    recipients_config: Dict[str, Any] | None = None


class TemplateResponse(BaseModel):
    uuid: UUID
    name: str
    title: str
    body: str
    channels: List[str]
    recipients_config: Dict[str, Any]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class InboxItemResponse(BaseModel):
    uuid: UUID
    is_read: bool
    created_at: datetime
    title: str
    body: str

    model_config = ConfigDict(from_attributes=True)
