from pathlib import Path
from typing import Annotated
import jwt
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
import structlog  # Импортируем structlog

from database.db import get_db

# Импортируем профессиональные доменные ошибки аутентификации
from jsonwebtoken.exceptions.utils import (
    AuthDomainException,
    CryptoKeyNotFoundError,
    InstanceAssociationError,
    InsufficientPermissionsError,
    InvalidTokenError,
    UserAccountNotFoundError,
)
from logs.context import bind_log_context  # Наш метод привязки контекста
from users.models import UserRole, Users

# Настраиваем логгер для слоя аутентификации
logger = structlog.get_logger("auth.dependencies")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/admin/login/", auto_error=False)
oauth2_admin_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login/", auto_error=False)
oauth2_client_scheme = OAuth2PasswordBearer(
    tokenUrl="/storefront-auth/login/", auto_error=False
)


def encode_jwt(payload: dict) -> str:
    private_key_path = Path(__file__).parent / "certs" / "jwt-private.pem"
    try:
        with open(private_key_path, "r") as key_file:
            private_key = key_file.read()
    except FileNotFoundError:
        raise CryptoKeyNotFoundError(
            "JWT private key file not found on server storage."
        )

    return jwt.encode(payload=payload, key=private_key, algorithm="RS256")


def decode_jwt(token: str, expected_type: str = "access") -> dict:
    """
    Декодирует токен и проверяет его тип ('access' или 'refresh').
    По умолчанию жестко требует 'access', защищая старые эндпоинты.
    """
    public_key_path = Path(__file__).parent / "certs" / "jwt-public.pem"

    try:
        with open(public_key_path, "r") as key_file:
            public_key = key_file.read()
    except FileNotFoundError:
        raise CryptoKeyNotFoundError("JWT public key file not found on server storage.")

    try:
        payload = jwt.decode(token, key=public_key, algorithms=["RS256"])

        # Проверка типа токена для защиты от подмены access на refresh
        token_type = payload.get("type", "access")
        if token_type != expected_type:
            raise InvalidTokenError(
                detail_message=f"Invalid token type. Expected {expected_type}, got {token_type}",
                reason="token_type_mismatch",
            )

        return payload

    except jwt.PyJWTError:
        raise InvalidTokenError(
            detail_message="Invalid or expired signature token.",
            reason="signature_expired_or_corrupted",
        )


async def get_current_user(
    token: Annotated[str, Depends(oauth2_scheme)],
    session: AsyncSession = Depends(get_db),
) -> Users:
    if not token:
        raise InvalidTokenError(
            "Missing authentication token header.", reason="token_missing"
        )

    try:
        payload = decode_jwt(token)
        uuid: str = payload.get("sub")
        if uuid is None:
            raise InvalidTokenError(
                "Subject identifier field is missing in token.", reason="sub_missing"
            )
    except AuthDomainException:
        raise
    except Exception:
        raise InvalidTokenError(
            "Could not validate authorization credentials.", reason="malformed_payload"
        )

    stmt = (
        select(Users)
        .options(joinedload(Users.permissions), joinedload(Users.settings))
        .where(Users.uuid == uuid)
    )

    response = await session.execute(stmt)
    user = response.scalar_one_or_none()

    if user is None:
        raise UserAccountNotFoundError(
            "User credentials validated, but account does not exist."
        )

    # 🚀 АВТОМАТИЗАЦИЯ: Как только пользователь успешно найден,
    # намертво вшиваем его UUID и роль в контекст логов текущего асинхронного запроса.
    bind_log_context(user_uuid=str(user.uuid), user_role=user.role.value)

    return user


async def get_current_active_user(
    current_user: Users = Depends(get_current_user),
) -> Users:
    # Здесь можно добавить проверку current_user.active, если это поле актуально
    return current_user


async def get_current_admin(
    current_user: Users = Depends(get_current_active_user),
) -> Users:
    if current_user.role != UserRole.ADMIN:
        # Если кто-то ломится под чужой ролью в админку — пишем предупреждение
        logger.warning(
            "Unauthorized admin resource access attempt",
            attempted_by_user_uuid=str(current_user.uuid),
            user_role=current_user.role.value,
        )
        raise InsufficientPermissionsError(
            "Only system administrators can access this resource."
        )

    # 🚨 УРОВЕНЬ ОПАСНОСТИ / АЛЕРТ: Сюда зашел настоящий админ платформы.
    # Используем logger.critical, чтобы это триггерило любые системы мониторинга.
    logger.critical(
        "SECURITY ALERT: System Administrator context initialized for request",
        admin_user_uuid=str(current_user.uuid),
    )
    return current_user


async def get_current_client(
    token: Annotated[str, Depends(oauth2_client_scheme)],
    session: AsyncSession = Depends(get_db),
) -> Users:
    """
    🔒 Выделенная изолированная зависимость для клиентов.
    Защищает от лишних JOIN-ов таблицы разрешений и проверяет роль.
    """
    if not token:
        raise InvalidTokenError(
            "Client authentication token is missing.", reason="token_missing"
        )

    try:
        payload = decode_jwt(token, expected_type="access")
        uuid: str = payload.get("sub")
        role: str = payload.get("role")

        # Быстрая проверка роли прямо из JWT без похода в базу данных!
        if uuid is None or role != UserRole.CLIENT.value:
            raise InvalidTokenError(
                "Invalid token metadata context for storefront client.",
                reason="invalid_role_context",
            )
    except AuthDomainException:
        raise
    except Exception:
        raise InvalidTokenError(
            "Could not validate client credentials.", reason="malformed_payload"
        )

    # Оптимизированный запрос БЕЗ joinedload(Users.permissions)
    stmt = select(Users).where(Users.uuid == uuid, Users.active)
    response = await session.execute(stmt)
    client = response.scalar_one_or_none()

    if client is None:
        raise UserAccountNotFoundError("Client account not found or deactivated.")

    # 🚀 АВТОМАТИЗАЦИЯ ДЛЯ КЛИЕНТОВ: Привязываем контекст клиента к логам
    bind_log_context(user_uuid=str(client.uuid), user_role=UserRole.CLIENT.value)

    return client


async def get_current_user_optional(
    token: str | None = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_db),
) -> Users | None:
    if not token:
        return None

    try:
        payload = decode_jwt(token)
        uuid: str = payload.get("sub")
        if uuid is None:
            return None

        stmt = select(Users).where(Users.uuid == uuid)
        response = await session.execute(stmt)
        user = response.scalar_one_or_none()

        if user:
            # Если токен есть и валидный — тоже логируем контекст
            bind_log_context(user_uuid=str(user.uuid), user_role=user.role.value)
        return user
    except Exception:
        # В опциональной зависимости глушим любые ошибки авторизации, возвращая анонима
        return None


async def get_current_creator(
    current_user: Users = Depends(get_current_active_user),
) -> Users:
    """
    Зависимость проверяет, что пользователь имеет роль CREATOR
    и привязан к активному инстансу.
    """
    if current_user.role != UserRole.CREATOR:
        raise InsufficientPermissionsError("Only creators can access this resource.")

    if not current_user.instance_id:
        raise InstanceAssociationError()

    return current_user


def get_current_instance_uuid(token: Annotated[str, Depends(oauth2_scheme)]) -> str:
    """
    ⚡ ВЫСОКОПРОИЗВОДИТЕЛЬНАЯ ЗАВИСИМОСТЬ.
    Извлекает инстанс-идентификатор напрямую из памяти (JWT Payload).
    Не делает ни одного запроса к PostgreSQL.
    """
    if not token:
        raise InvalidTokenError(
            "Missing authentication token header.", reason="token_missing"
        )

    try:
        payload = decode_jwt(token)
        instance_uuid: str = payload.get("instance_uuid")
        if not instance_uuid:
            raise InstanceAssociationError(
                "Данный пользовательский токен не ассоциирован с инстансом."
            )
        return instance_uuid
    except AuthDomainException:
        raise
    except Exception:
        raise InvalidTokenError(
            "Не удалось валидировать контекст аренды (Multi-tenant) из токена.",
            reason="malformed_payload",
        )


async def get_current_instance_creator(
    token: Annotated[str, Depends(oauth2_scheme)],
    current_user: Users = Depends(get_current_active_user),
) -> Users:
    """
    🛡️ ОПТИМИЗИРОВАННАЯ КЛИЕНТСКАЯ ПРОВЕРКА.
    Сначала быстро проверяет метаданные роли и инстанса в JWT,
    чтобы гарантировать безопасность перед тяжелыми операциями.
    """
    try:
        payload = decode_jwt(token)
        role = payload.get("role")
        instance_uuid = payload.get("instance_uuid")

        if role != UserRole.CREATOR.value:
            raise InsufficientPermissionsError(
                "Доступ разрешен только создателям инстанса."
            )

        if not instance_uuid:
            raise InstanceAssociationError()
    except AuthDomainException:
        raise
    except Exception:
        pass  # Фолбэк на классическую валидацию из БД, если токен старого формата

    # Проверяем классический контракт через SQLAlchemy на случай деактивации аккаунта
    if current_user.role != UserRole.CREATOR:
        raise InsufficientPermissionsError("Only creators can access this resource.")

    if not current_user.instance_id:
        raise InstanceAssociationError()

    return current_user
