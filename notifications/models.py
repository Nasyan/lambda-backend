# notifications/models.py
import uuid
from datetime import datetime
from typing import List, Dict, Any
from uuid import UUID
from sqlalchemy import ForeignKey, String, Boolean, DateTime, Integer
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import JSONB, ARRAY
from sqlalchemy.sql import func
from database.db import Base


class NotificationTemplate(Base):
    __tablename__ = "notification_templates"

    uuid: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    instance_uuid: Mapped[UUID] = mapped_column(index=True, nullable=False)

    name: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[str] = mapped_column(String, nullable=False)
    body: Mapped[str] = mapped_column(String, nullable=False)

    channels: Mapped[List[str]] = mapped_column(ARRAY(String), default=["crm"])
    recipients_config: Mapped[Dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class NotificationHistory(Base):
    __tablename__ = "notification_history"

    uuid: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    instance_uuid: Mapped[UUID] = mapped_column(index=True, nullable=False)
    template_uuid: Mapped[UUID] = mapped_column(
        ForeignKey("notification_templates.uuid", ondelete="SET NULL"),
        nullable=True,  # Теперь может быть null, если шаблон удален
    )

    compiled_title: Mapped[str] = mapped_column(String, nullable=False)
    compiled_body: Mapped[str] = mapped_column(String, nullable=False)

    success_count: Mapped[int] = mapped_column(Integer, default=0)
    target_channels: Mapped[List[str]] = mapped_column(ARRAY(String), default=list)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class NotificationInbox(Base):
    __tablename__ = "notification_inbox"

    uuid: Mapped[UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_uuid: Mapped[UUID] = mapped_column(
        ForeignKey("users.uuid", ondelete="CASCADE"), index=True, nullable=False
    )
    history_uuid: Mapped[UUID] = mapped_column(
        ForeignKey("notification_history.uuid", ondelete="CASCADE"), nullable=False
    )

    is_read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    # Связь для джоина истории (чтобы сразу отдавать текст пользователю)
    history: Mapped["NotificationHistory"] = relationship("NotificationHistory")
