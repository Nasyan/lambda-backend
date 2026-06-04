# users/models.py

from typing import Tuple, Optional, List
from uuid import UUID, uuid4
import enum

import bcrypt
from sqlalchemy import ForeignKey, String, Boolean, Enum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.ext.hybrid import hybrid_property

from database.db import Base

from sqlalchemy import ARRAY  # 🔥 Импортируем поддержку массивов для Postgres


class AppTools(str, enum.Enum):
    """Список всех доступных инструментов (интерфейсов) в системе."""

    ALL = "all"
    NOTES = "notes"
    TABLES = "tables"
    WORKFLOW = "workflow"
    ANALYTICS = "analytics"
    POLICY = "policy"
    STORE = "store"
    TRIGGERS = "triggers"
    TEMPLATES = "templates"


class UserPermissions(Base):
    __tablename__ = "user_permissions"

    # Используем Shared Primary Key: uuid является и PK, и ссылается на users.uuid
    user_uuid: Mapped[UUID] = mapped_column(
        ForeignKey("users.uuid", ondelete="CASCADE"), primary_key=True
    )

    # Массив разрешенных инструментов. По дефолту пустой список.
    # Будем хранить строки (значения из AppTools), чтобы база была гибкой.
    allowed_tools: Mapped[List[str]] = mapped_column(
        ARRAY(String(50)), nullable=False, default=["all"]
    )

    # Обратная связь «Один к Одному» с пользователем
    user: Mapped["Users"] = relationship("Users", back_populates="permissions")


class UserRole(str, enum.Enum):
    ADMIN = "ADMIN"
    CREATOR = "CREATOR"
    USER = "USER"
    CLIENT = "CLIENT"


def validate_email(email: str) -> Tuple[bool, Optional[str]]:
    if not email:
        return False, "Email cannot be empty"
    if "@" not in email:
        return False, "Email must contain @"
    return True, None


def validate_password(password: str) -> Tuple[bool, Optional[str]]:
    if not password:
        return False, "Password cannot be empty"
    min_length = 5
    if len(password) < min_length:
        return False, f"Password must be at least {min_length} characters long"
    return True, None


class Instances(Base):
    __tablename__ = "instances"

    uuid: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    users: Mapped[List["Users"]] = relationship(
        "Users", back_populates="instance", cascade="all, delete-orphan"
    )

    @property
    def creator(self) -> Optional["Users"]:
        for user in self.users:
            if user.role == UserRole.CREATOR:
                return user
        return None


class Users(Base):
    __tablename__ = "users"

    uuid: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole), nullable=False, default=UserRole.USER
    )

    instance_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("instances.uuid", ondelete="SET NULL"), nullable=True
    )

    instance: Mapped[Instances | None] = relationship(
        "Instances", back_populates="users"
    )

    _email: Mapped[str] = mapped_column(String(150), nullable=False, unique=True)
    hash_password: Mapped[str] = mapped_column(String, nullable=False)

    permissions: Mapped[Optional["UserPermissions"]] = relationship(
        "UserPermissions",
        back_populates="user",
        uselist=False,  # 🔥 ГАРАНТИРУЕТ связь ОДИН-К-ОДНОМУ
        cascade="all, delete-orphan",
    )

    @hybrid_property
    def email(self) -> str:
        return self._email

    @email.setter
    def email(self, value: str):
        if not value:
            raise ValueError("Email cannot be empty")
        is_valid, message = validate_email(value)
        if not is_valid:
            raise ValueError(f"Invalid email: {message}")
        self._email = value

    @hybrid_property
    def password(self):
        raise AttributeError("Password is not readable")

    @password.setter
    def password(self, plain_password: str):
        is_valid, message = validate_password(plain_password)
        if not is_valid:
            raise ValueError(f"Invalid password: {message}")
        hashed = bcrypt.hashpw(plain_password.encode("utf-8"), bcrypt.gensalt())
        self.hash_password = hashed.decode("utf-8")

    def verify_password(self, plain_password: str) -> bool:
        try:
            return bcrypt.checkpw(
                plain_password.encode("utf-8"), self.hash_password.encode("utf-8")
            )
        except (AttributeError, ValueError, TypeError):
            return False
