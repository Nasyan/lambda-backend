# users/services/admin_service.py

from uuid import UUID
from redisdb.utils import generate_key
from config import INVITE_PREFIX
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from jsonwebtoken.utils import encode_jwt
from datetime import datetime, timezone, timedelta
from users.models import Users, UserRole, Instances

# Импортируем наши новые профессиональные исключения
from users.exceptions.admin_service import (
    InstanceNotFoundError,
    InstanceAlreadyExistsError,
    InstanceDeactivatedError,
    CreatorNotFoundError,
    UserAlreadyExistsError,
    CreatorAlreadyDeactivatedError,
    InvalidAdminCredentialsError,
)


class AdminService:
    def __init__(self, db_session: AsyncSession, redis_client):
        self.db = db_session
        self.redis = redis_client

    async def get_instance_or_404(self, instance_id: UUID) -> Instances:
        result = await self.db.execute(
            select(Instances).where(Instances.uuid == instance_id)
        )
        instance = result.scalar_one_or_none()
        if not instance:
            raise InstanceNotFoundError(instance_id=instance_id)
        return instance

    async def invite_creator(self, email: str, instance_id: UUID) -> str:
        instance = await self.get_instance_or_404(instance_id)
        if not instance.active:
            raise InstanceDeactivatedError(instance_id=instance_id)

        result = await self.db.execute(select(Users).where(Users._email == email))
        if result.scalar_one_or_none():
            raise UserAlreadyExistsError(email=email)

        redis_key = generate_key(prefix=INVITE_PREFIX, sub=email)
        await self.redis.set(name=redis_key, ex=86400, value=str(instance_id))
        return instance.title

    async def create_instance(self, title: str) -> Instances:
        result = await self.db.execute(
            select(Instances).where(Instances.title == title)
        )
        if result.scalar_one_or_none():
            raise InstanceAlreadyExistsError(title=title)

        new_instance = Instances(title=title, active=True)
        self.db.add(new_instance)
        await self.db.commit()
        await self.db.refresh(new_instance)
        return new_instance

    async def get_creator_or_404(self, creator_uuid: UUID) -> Users:
        result = await self.db.execute(select(Users).where(Users.uuid == creator_uuid))
        user = result.scalar_one_or_none()
        if not user or user.role != UserRole.CREATOR:
            raise CreatorNotFoundError(creator_uuid=creator_uuid)
        return user

    async def deactivate_creator(self, creator_uuid: UUID) -> Users:
        user = await self.get_creator_or_404(creator_uuid)
        if not user.active:
            raise CreatorAlreadyDeactivatedError(creator_uuid=creator_uuid)

        user.active = False
        await self.db.commit()
        await self.db.refresh(user)
        return user

    async def list_creators(self) -> list[Users]:
        result = await self.db.execute(
            select(Users).where(Users.role == UserRole.CREATOR).order_by(Users._email)
        )
        return result.scalars().all()

    async def authenticate_admin(self, email: str, password: str) -> str:
        # Ищем пользователя
        result = await self.db.execute(select(Users).where(Users._email == email))
        user = result.scalar_one_or_none()

        # Профессиональная комплексная валидация безопасности без утечки внутренней структуры
        if (
            not user
            or not user.active
            or user.role != UserRole.ADMIN
            or not user.verify_password(password)
        ):
            raise InvalidAdminCredentialsError(email=email)

        payload = {
            "sub": str(user.uuid),
            "email": user.email,
            "exp": datetime.now(timezone.utc) + timedelta(hours=24),
        }
        return encode_jwt(payload=payload)

    async def list_all_instances(self) -> list[Instances]:
        result = await self.db.execute(select(Instances).order_by(Instances.title))
        return result.scalars().all()
