# core/schemas/history.py

from pydantic import BaseModel
from uuid import UUID
from datetime import datetime
from typing import Any, List, Optional, Dict


class FieldHistoryItem(BaseModel):
    version: int
    user_uuid: UUID
    updated_at: Optional[datetime] = None
    field_name: str
    value: Any


class FieldHistoryResponse(BaseModel):
    status: str
    record_uuid: UUID
    field_name: str
    history: List[FieldHistoryItem]


class FullHistoryItem(BaseModel):
    version: int
    user_uuid: UUID
    updated_at: Optional[datetime] = None
    snapshot: Dict[str, Any]


class FullHistoryResponse(BaseModel):
    status: str
    record_uuid: UUID
    history: List[FullHistoryItem]
