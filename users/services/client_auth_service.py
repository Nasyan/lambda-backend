# users/services/client_auth_service.py

import random
from datetime import datetime, timezone, timedelta
from fastapi import Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from users.models import Users, UserRole, Instances
from jsonwebtoken.utils import encode_jwt, decode_jwt
from workers.email_tasks import send_email
from config import SENDER_EMAIL, EMAIL_PASSWORD

# Импортируем профессиональные доменные исключения
from users.exceptions.client_auth_service import (
    StorefrontInstanceNotFoundError,
    ClientAlreadyRegisteredError,
    ClientNotFoundError,
    InvalidResendRequestError,
    InvalidClientCredentialsError,
    InvalidClientTokenSessionError,
)


class ClientAuthService:
    def __init__(self, db_session: AsyncSession, redis_auth_service):
        self.db = db_session
        self.redis_auth = redis_auth_service

    async def get_user_by_email(self, email: str) -> Users | None:
        result = await self.db.execute(select(Users).where(Users._email == email))
        return result.scalar_one_or_none()

    async def register_client(self, payload) -> None:
        """
        Саморегистрация клиента.
        В payload обязательно должен приходить instance_id магазина.
        """
        # 1. Валидация инстанса (Существует ли вообще этот магазин?)
        instance_result = await self.db.execute(
            select(Instances).where(Instances.uuid == payload.instance_id)
        )
        if not instance_result.scalar_one_or_none():
            raise StorefrontInstanceNotFoundError(instance_id=payload.instance_id)

        # 2. Проверка пользователя
        existing_user = await self.get_user_by_email(payload.email)

        if existing_user and existing_user.active:
            raise ClientAlreadyRegisteredError(email=payload.email)

        # 3. Анти-спам защита (Rate Limiting)
        await self.redis_auth.check_rate_limit(payload.email)

        # 4. Mass Assignment защита: жестко мапим поля руками, хардкодим роль CLIENT
        if existing_user:
            existing_user.password = payload.password
            existing_user.name = payload.name
            existing_user.role = UserRole.CLIENT  # 🔒 ЖЕСТКАЯ ФИКСАЦИЯ
            existing_user.instance_id = payload.instance_id
        else:
            new_user = Users()
            new_user.email = payload.email
            new_user.password = payload.password
            new_user.name = payload.name
            new_user.role = UserRole.CLIENT  # 🔒 ЖЕСТКАЯ ФИКСАЦИЯ
            new_user.instance_id = payload.instance_id
            new_user.active = False
            self.db.add(new_user)

        await self.db.commit()

        # 5. Генерация и отправка кода
        verification_code = str(random.randint(100000, 999999))
        await self.redis_auth.save_verification_code(payload.email, verification_code)

        send_email.send(
            sender_email=SENDER_EMAIL,
            password=EMAIL_PASSWORD,
            receiver_email=payload.email,
            subject="Код подтверждения (Магазин)",
            body=f"Ваш код для входа в магазин: {verification_code}",
        )

    async def verify_registration(self, payload) -> Users:
        await self.redis_auth.verify_and_delete_code(payload.email, payload.code)

        user = await self.get_user_by_email(payload.email)
        if not user:
            raise ClientNotFoundError(email=payload.email)

        if not user.active:
            user.active = True
            await self.db.commit()
            await self.db.refresh(user)

        return user

    async def resend_code(self, payload) -> None:
        user = await self.get_user_by_email(payload.email)
        if not user or user.active:
            raise InvalidResendRequestError(email=payload.email)

        await self.redis_auth.check_rate_limit(payload.email)

        new_code = str(random.randint(100000, 999999))
        await self.redis_auth.save_verification_code(payload.email, new_code)

        send_email.send(
            sender_email=SENDER_EMAIL,
            password=EMAIL_PASSWORD,
            receiver_email=payload.email,
            subject="Новый код подтверждения (Магазин)",
            body=f"Ваш новый код: {new_code}",
        )

    async def authenticate_and_issue_tokens(
        self, form_data, response: Response
    ) -> dict:
        user = await self.get_user_by_email(form_data.username)

        # 🔒 Проверка Role Escalation: Только клиенты могут логиниться через эту форму!
        if (
            not user
            or not user.active
            or user.role != UserRole.CLIENT
            or not user.verify_password(form_data.password)
        ):
            raise InvalidClientCredentialsError(email=form_data.username)

        return self._generate_tokens_context(user, response)

    async def refresh_session_tokens(
        self, refresh_token: str | None, response: Response
    ) -> dict:
        if not refresh_token:
            raise InvalidClientTokenSessionError(reason="Missing client refresh cookie")

        try:
            payload = decode_jwt(refresh_token, expected_type="refresh")
            user_uuid = payload.get("sub")
        except Exception as e:
            raise InvalidClientTokenSessionError(reason=f"JWT Decode error: {str(e)}")

        user = await self.db.execute(select(Users).where(Users.uuid == user_uuid))
        user = user.scalar_one_or_none()

        # 🔒 Снова проверяем, что это именно активный клиент
        if not user or not user.active or user.role != UserRole.CLIENT:
            raise InvalidClientTokenSessionError(
                reason="User missing, inactive or role mismatch"
            )

        return self._generate_tokens_context(user, response)

    def _generate_tokens_context(self, user: Users, response: Response) -> dict:
        now = datetime.now(timezone.utc)

        # 🔒 В токене навсегда зашивается role: CLIENT и ID магазина
        access_token = encode_jwt(
            payload={
                "sub": str(user.uuid),
                "role": user.role.value,
                "instance_id": str(user.instance_id),
                "type": "access",
                "exp": now + timedelta(hours=2),
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
            key="client_refresh_token",
            value=refresh_token,
            httponly=True,
            secure=True,
            samesite="lax",
            max_age=30 * 24 * 60 * 60,
        )
        return {"access_token": access_token, "token_type": "bearer"}
