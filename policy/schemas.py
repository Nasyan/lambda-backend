# policy/schemas.py

from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional
from uuid import UUID


class PolicyBase(BaseModel):
    template_name: str = Field(
        ..., max_length=100, description="Имя шаблона CRM, например 'products'"
    )
    read_filters: Dict[str, Any] = Field(
        default_factory=dict, description="Фильтры безопасности витрины"
    )
    read_mask: List[str] = Field(
        default_factory=list, description="Белый список полей для чтения"
    )
    write_mask: List[str] = Field(
        default_factory=list, description="Белый список полей для записи"
    )
    defaults: Dict[str, Any] = Field(
        default_factory=dict,
        description="Значения по умолчанию (автозаполнение), скрытые от клиента",
    )


class PolicyCreate(PolicyBase):
    pass


class PolicyUpdate(BaseModel):
    read_filters: Optional[Dict[str, Any]] = None
    read_mask: Optional[List[str]] = None
    write_mask: Optional[List[str]] = None
    defaults: Optional[Dict[str, Any]] = None


class PolicyResponse(PolicyBase):
    id: UUID
    instance_uuid: UUID

    class Config:
        from_attributes = True
