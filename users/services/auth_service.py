# users/services/auth_service.py

from uuid import UUID
from redisdb.utils import generate_key
from config import INVITE_PREFIX, USER_INVITE_PREFIX, JOIN_PREFIX
from typing import Tuple
from datetime import datetime, timezone, timedelta
from fastapi import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from jsonwebtoken.utils import encode_jwt, decode_jwt
from users.services.verification_notifier import RegistrationVerificationNotifier
from sqlalchemy.future import select
from users.models import Users, UserRole


from users.exceptions.auth_service import (
    AuthRateLimitExceededError,
    InvitationRequiredError,
    InvitationExpiredError,
    VerificationCodeExpiredError,
    InvalidVerificationCodeError,
    UserAlreadyRegisteredError,
    UserNotFoundError,
    InvalidCredentialsError,
    InvalidTokenCredentialsError,
    StorageDataCorruptedError,
)


class AuthRedisService:
    def __init__(self, redis_client):
        self.redis = redis_client

    async def check_rate_limit(self, email: str) -> None:
        """Проверяет анти-спам лимит (60 секунд) для отправки писем."""
        join_redis_key = generate_key(prefix=JOIN_PREFIX, sub=email)
        existing_ttl = await self.redis.ttl(join_redis_key)

        if existing_ttl > 840:  # 900 - 60 секунд
            raise AuthRateLimitExceededError(
                email=email, ttl_remaining=existing_ttl - 840
            )

    async def find_invite(self, email: str) -> Tuple[UUID, bool, str]:
        """Ищет инвайт. Возвращает (instance_id, is_creator_flow, invite_redis_key)."""
        creator_key = generate_key(prefix=INVITE_PREFIX, sub=email)
        user_key = generate_key(prefix=USER_INVITE_PREFIX, sub=email)

        invite_bytes = await self.redis.get(creator_key)
        if invite_bytes:
            return self._parse_invite(creator_key, invite_bytes), True, creator_key

        invite_bytes = await self.redis.get(user_key)
        if invite_bytes:
            return self._parse_invite(user_key, invite_bytes), False, user_key

        raise InvitationRequiredError(email=email)

    async def find_active_invite_key(self, email: str) -> str:
        """Ищет только сам ключ инвайта для /resend-code/."""
        creator_key = generate_key(prefix=INVITE_PREFIX, sub=email)
        user_key = generate_key(prefix=USER_INVITE_PREFIX, sub=email)

        if await self.redis.exists(creator_key):
            return creator_key
        elif await self.redis.exists(user_key):
            return user_key

        raise InvitationExpiredError(email=email)

    def _parse_invite(self, redis_key: str, invite_bytes) -> UUID:
        instance_id_str = (
            invite_bytes.decode("utf-8")
            if isinstance(invite_bytes, bytes)
            else invite_bytes
        )
        try:
            return UUID(instance_id_str)
        except ValueError:
            raise StorageDataCorruptedError(key=redis_key, raw_value=invite_bytes)

    async def save_verification_code(
        self, email: str, code: str, invite_key: str
    ) -> None:
        """Сохраняет код верификации в связке с ключом инвайта."""
        join_key = generate_key(prefix=JOIN_PREFIX, sub=email)
        redis_value = f"{code}:{invite_key}"
        await self.redis.setex(join_key, 900, redis_value)

    async def verify_and_delete_code(self, email: str, input_code: str) -> None:
        """Проверяет код. Если верный — удаляет его и инвайт из базы."""
        join_key = generate_key(prefix=JOIN_PREFIX, sub=email)
        stored_data_bytes = await self.redis.get(join_key)

        if not stored_data_bytes:
            raise VerificationCodeExpiredError(email=email)

        stored_data = (
            stored_data_bytes.decode("utf-8")
            if isinstance(stored_data_bytes, bytes)
            else stored_data_bytes
        )
        try:
            saved_code, invite_key = stored_data.split(":", 1)
        except ValueError:
            raise StorageDataCorruptedError(key=join_key, raw_value=stored_data)

        if input_code != saved_code:
            raise InvalidVerificationCodeError(email=email)

        await self.redis.delete(join_key)
        await self.redis.delete(invite_key)


class AuthService:
    def __init__(self, db_session: AsyncSession, redis_auth_service: AuthRedisService):
        self.db = db_session
        self.redis_auth = redis_auth_service

    async def get_user_by_email(self, email: str) -> Users | None:
        result = await self.db.execute(select(Users).where(Users._email == email))
        return result.scalar_one_or_none()

    async def register_user(self, payload) -> None:
        existing_user = await self.get_user_by_email(payload.email)

        if existing_user and existing_user.active:
            raise UserAlreadyRegisteredError(email=payload.email)

        if existing_user:
            await self.redis_auth.check_rate_limit(payload.email)

        target_instance_id, is_creator_flow, matched_redis_key = (
            await self.redis_auth.find_invite(payload.email)
        )

        if existing_user:
            existing_user.password = payload.password
            existing_user.name = payload.name
            existing_user.role = UserRole.CREATOR if is_creator_flow else UserRole.USER
            existing_user.instance_id = target_instance_id
        else:
            new_user = Users()
            new_user.email = payload.email
            new_user.password = payload.password
            new_user.name = payload.name
            new_user.role = UserRole.CREATOR if is_creator_flow else UserRole.USER
            new_user.instance_id = target_instance_id
            new_user.active = False
            self.db.add(new_user)

        await self.db.commit()

        verification_code = RegistrationVerificationNotifier.generate_code()
        await self.redis_auth.save_verification_code(
            payload.email, verification_code, matched_redis_key
        )

        # Побочное действие (email) вынесено в отдельный нотификатор (task3, ГЗ-1 Этап 2)
        RegistrationVerificationNotifier.send_code(
            email=payload.email, name=payload.name, code=verification_code
        )

    async def verify_registration(self, payload) -> Users:
        await self.redis_auth.verify_and_delete_code(payload.email, payload.code)

        user = await self.get_user_by_email(payload.email)
        if not user:
            raise UserNotFoundError(email=payload.email)

        if not user.active:
            user.active = True
            await self.db.commit()
            await self.db.refresh(user)

        return user

    async def resend_code(self, payload) -> None:
        user = await self.get_user_by_email(payload.email)
        if not user:
            raise UserNotFoundError(email=payload.email)

        if user.active:
            raise UserAlreadyRegisteredError(email=payload.email)

        await self.redis_auth.check_rate_limit(payload.email)
        matched_redis_key = await self.redis_auth.find_active_invite_key(payload.email)

        new_verification_code = RegistrationVerificationNotifier.generate_code()
        await self.redis_auth.save_verification_code(
            payload.email, new_verification_code, matched_redis_key
        )

        RegistrationVerificationNotifier.send_code(
            email=payload.email,
            name=user.name,
            code=new_verification_code,
            repeat=True,
        )

    async def authenticate_and_issue_tokens(
        self, form_data, response: Response
    ) -> dict:
        user = await self.get_user_by_email(form_data.username)

        if not user or not user.active or not user.verify_password(form_data.password):
            raise InvalidCredentialsError()

        return self._generate_tokens_context(user, response)

    async def refresh_session_tokens(
        self, refresh_token: str | None, response: Response
    ) -> dict:
        if not refresh_token:
            raise InvalidTokenCredentialsError(reason="Refresh token cookie is missing")

        try:
            payload = decode_jwt(refresh_token, expected_type="refresh")
            user_uuid = payload.get("sub")
            if user_uuid is None:
                raise InvalidTokenCredentialsError(reason="Token 'sub' claim is empty")
        except Exception as e:
            raise InvalidTokenCredentialsError(reason=f"JWT decode failed: {str(e)}")

        stmt = (
            select(Users)
            .options(joinedload(Users.permissions))
            .where(Users.uuid == user_uuid)
        )
        db_response = await self.db.execute(stmt)
        user = db_response.scalar_one_or_none()

        if not user or not user.active:
            raise InvalidTokenCredentialsError(
                reason="User session is blocked or deactivated"
            )

        return self._generate_tokens_context(user, response)

    def _generate_tokens_context(self, user: Users, response: Response) -> dict:
        """Внутренний хелпер для сборки пары токенов и упаковки Refresh в Cookie."""
        now = datetime.now(timezone.utc)

        access_token = encode_jwt(
            payload={
                "sub": str(user.uuid),
                "role": user.role.value,
                "instance_id": str(user.instance_id) if user.instance_id else None,
                "type": "access",
                "exp": now + timedelta(minutes=30),
            }
        )

        refresh_token = encode_jwt(
            payload={
                "sub": str(user.uuid),
                "type": "refresh",
                "exp": now + timedelta(days=30),
            }
        )

        response.set_cookie(
            key="refresh_token",
            value=refresh_token,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=30 * 24 * 60 * 60,
        )

        return {"access_token": access_token, "token_type": "bearer"}
