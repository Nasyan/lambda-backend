# users/schemas.py

from datetime import datetime, timezone
from typing import Optional, List
from uuid import UUID
from users.models import AppTools
from pydantic import BaseModel, EmailStr, Field, field_validator, ConfigDict
from pydantic_core import PydanticCustomError
import re

PASSWORD_REGEX = re.compile(r"^(?=.*[a-z])(?=(.*[A-Z]))(?=.*\d).{8,}$")


class SettingsBase(BaseModel):
    bitrate: Optional[str] = None


class SettingsRead(SettingsBase):
    uuid: UUID
    user_uuid: UUID

    model_config = ConfigDict(from_attributes=True)


class UsersCreate(BaseModel):
    email: EmailStr
    password: str
    name: Optional[str] = None


class UsersModify(BaseModel):
    name: Optional[str] = None
    telegram: Optional[str] = None


class UsersReplace(UsersModify):
    pass


class UserRead(BaseModel):
    uuid: UUID
    email: EmailStr
    name: Optional[str] = None
    telegram: Optional[str] = None
    active: bool = False

    model_config = ConfigDict(from_attributes=True)


class ResendCodeRequest(BaseModel):
    email: str


class UsersList(BaseModel):
    uuid: UUID
    name: Optional[str]
    telegram: Optional[str]
    active: bool

    model_config = ConfigDict(from_attributes=True)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str


class CodeData(BaseModel):
    code: str
    email: EmailStr


class ChangePasswordRequest(BaseModel):
    code: str
    new_password: str = Field(..., min_length=5)


class RedisCode(BaseModel):
    code: str
    email: EmailStr
    user: str
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


class TokenData(BaseModel):
    uuid: str


class DeleteAccountRequest(BaseModel):
    password: str


class UserRegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6, description="Пароль пользователя")
    name: str = Field(..., min_length=2, max_length=50, description="Имя или никнейм")


class InstanceCreateRequest(BaseModel):
    title: str = Field(
        ..., min_length=2, max_length=100, description="Название инстанса/компании"
    )


class InstanceResponse(BaseModel):
    uuid: UUID
    title: str
    active: bool

    model_config = ConfigDict(from_attributes=True)


class CreatorInviteRequest(BaseModel):
    email: EmailStr
    instance_id: UUID  # Передаем UUID инстанса, куда приглашаем креатора


class UserInviteRequest(BaseModel):
    email: EmailStr


class VerifyRegistrationRequest(BaseModel):
    email: EmailStr
    code: str = Field(..., min_length=6, max_length=6, pattern=r"^\d{6}$")


class ResendVerificationCodeRequest(BaseModel):
    email: EmailStr


class PromoteUserRequest(BaseModel):
    user_uuid: UUID


class UserRoleChangeRequest(BaseModel):
    user_uuid: UUID


class UpdateUserPermissionsRequest(BaseModel):
    user_uuid: UUID
    allowed_tools: List[AppTools]


class CreatorResponse(BaseModel):
    uuid: UUID
    email: str  # или EmailStr, в зависимости от того, как возвращает твое проперти/поле модели
    role: str
    active: bool

    model_config = ConfigDict(from_attributes=True)


class ClientRegisterRequest(BaseModel):
    email: EmailStr = Field(
        ...,
        description="Email адрес клиента для регистрации и отправки кодов",
        examples=["customer@example.com"],
    )
    password: str = Field(
        ...,
        min_length=8,
        max_length=128,
        description="Пароль учетной записи (минимум 8 символов, заглавная, строчная буква и цифра)",
        examples=["SecurePass123!"],
    )
    name: str = Field(
        ...,
        min_length=2,
        max_length=64,
        description="Имя или никнейм покупателя",
        examples=["Александр"],
    )
    instance_id: UUID = Field(
        ...,
        description="Идентификатор инстанса (UUID) интернет-магазина, к которому привязывается клиент",
        examples=["4a3b2c1d-e5f6-7a8b-9c0d-1e2f3a4b5c6d"],
    )

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    @field_validator("password")
    @classmethod
    def validate_password_strength(cls, value: str) -> str:
        """Дополнительная строгая валидация сложности пароля на уровне схемы."""
        if not PASSWORD_REGEX.match(value):
            raise PydanticCustomError(
                "password_too_weak",
                "Password must be at least 8 characters long, contain at least one uppercase letter, one lowercase letter, and one number.",
            )
        return value


class ClientProfileResponse(BaseModel):
    uuid: UUID
    email: EmailStr
    name: str
    instance_id: UUID

    model_config = ConfigDict(from_attributes=True)
