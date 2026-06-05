# triggers/models.py

import uuid
from enum import Enum
from sqlalchemy import Column, String, JSON, Enum as SQLEnum, DateTime
from sqlalchemy.dialects.postgresql import UUID
from database.db import Base
from datetime import datetime
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func


class TriggerType(str, Enum):
    STORED_COLUMN = "STORED_COLUMN"
    LIVE_EVAL = "LIVE_EVAL"
    AUTOMATION = "AUTOMATION"


class EventType(str, Enum):
    CRON = "CRON"  # По расписанию (раз в день/час)
    ON_RECORD_CREATE = "ON_RECORD_CREATE"  # При создании новой записи в Mongo
    ON_RECORD_UPDATE = "ON_RECORD_UPDATE"  # При изменении записи в Mongo
    MANUAL = "MANUAL"  # Вызывается вручную (например, кнопка "Разослать всем")


class PayloadReturnType(str, Enum):
    BOOLEAN = "BOOLEAN"
    VALUE = "VALUE"
    LIST = "LIST"


class Trigger(Base):
    __tablename__ = "triggers"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    instance_uuid = Column(UUID(as_uuid=True), nullable=False, index=True)
    name = Column(String(255), nullable=False)

    target_field = Column(String(64), nullable=True)

    trigger_type = Column(
        SQLEnum(TriggerType, name="trigger_type_enum", create_type=False),
        nullable=False,
        default=TriggerType.LIVE_EVAL,
    )

    condition_ast = Column(JSON, nullable=True)
    payload_ast = Column(JSON, nullable=False)
    payload_return_type = Column(
        SQLEnum(
            PayloadReturnType,
            name="payload_return_type_enum",
            create_type=False,
        ),
        nullable=False,
    )
    action_mapping_ast = Column(JSON, nullable=True)

    source_template_uuid = Column(UUID(as_uuid=True), nullable=False, index=True)
    target_template_uuid = Column(UUID(as_uuid=True), nullable=True, index=True)

    event_type = Column(
        SQLEnum(EventType, name="event_type_enum", create_type=False), nullable=True
    )

    cron_expression = Column(String(50), nullable=True)

    action_name = Column(String(100), nullable=True)

    action_params = Column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    def to_dict(self):
        return {
            "id": str(self.id),
            "instance_uuid": str(self.instance_uuid),
            "name": self.name,
            "trigger_type": (
                self.trigger_type.value
                if hasattr(self.trigger_type, "value")
                else self.trigger_type
            ),
            "condition_ast": self.condition_ast,
            "payload_ast": self.payload_ast,
            "payload_return_type": (
                self.payload_return_type.value
                if hasattr(self.payload_return_type, "value")
                else self.payload_return_type
            ),
            "action_mapping_ast": self.action_mapping_ast,
            "source_template_uuid": (
                str(self.source_template_uuid) if self.source_template_uuid else None
            ),
            "target_template_uuid": (
                str(self.target_template_uuid) if self.target_template_uuid else None
            ),
            "event_type": (
                self.event_type.value
                if hasattr(self.event_type, "value")
                else self.event_type
            ),
            "cron_expression": self.cron_expression,
            "action_name": self.action_name,
            "action_params": self.action_params,
        }
