# users/services/client_auth_redis_service.py

from redisdb.utils import generate_key
from config import JOIN_PREFIX

# Импортируем профессиональные клиентские исключения
from users.exceptions.client_auth_redis_service import (
    ClientAuthRateLimitExceededError,
    ClientVerificationCodeExpiredError,
    ClientInvalidVerificationCodeError,
)


class ClientAuthRedisService:
    def __init__(self, redis_client):
        self.redis = redis_client

    async def check_rate_limit(self, email: str) -> None:
        """Анти-спам лимит (60 секунд) на публичную форму регистрации."""
        join_redis_key = generate_key(prefix=JOIN_PREFIX, sub=email)
        existing_ttl = await self.redis.ttl(join_redis_key)

        if existing_ttl > 840:  # 900 - 60 секунд
            raise ClientAuthRateLimitExceededError(
                email=email, ttl_remaining=existing_ttl - 840
            )

    async def save_verification_code(self, email: str, code: str) -> None:
        """Сохраняем только код (без инвайта, так как это саморегистрация)."""
        join_key = generate_key(prefix=JOIN_PREFIX, sub=email)
        await self.redis.set(name=join_key, ex=900, value=code)

    async def verify_and_delete_code(self, email: str, input_code: str) -> None:
        """Проверяем код публичного пользователя."""
        join_key = generate_key(prefix=JOIN_PREFIX, sub=email)
        stored_code_bytes = await self.redis.get(join_key)

        if not stored_code_bytes:
            raise ClientVerificationCodeExpiredError(email=email)

        stored_code = (
            stored_code_bytes.decode("utf-8")
            if isinstance(stored_code_bytes, bytes)
            else stored_code_bytes
        )

        if input_code != stored_code:
            raise ClientInvalidVerificationCodeError(email=email)

        await self.redis.delete(join_key)
