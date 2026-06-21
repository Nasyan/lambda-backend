# users/services/creator_service.py

from uuid import UUID
from redisdb.utils import generate_key
from config import USER_INVITE_PREFIX
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload
from sqlalchemy.future import select
from users.models import Users, UserRole, UserPermissions, AppTools

# Импортируем профессиональные доменные исключения
from users.exceptions.creator_service import (
    TargetUserNotFoundError,
    InstanceAccessDeniedError,
    TargetUserAlreadyExistsError,
    UserRoleStateError,
    SelfManagementDeniedError,
    CreatorPermissionsUpdateError,
    CreatorDeactivationDeniedError,
    TargetUserAlreadyInactiveError,
    InfrastructureStorageError,
)


class CreatorService:
    def __init__(self, db_session: AsyncSession, redis_client):
        self.db = db_session
        self.redis = redis_client

    async def _get_target_user_or_404(
        self, target_uuid: UUID, creator_instance_id: UUID
    ) -> Users:
        """
        Внутренний хелпер: Ищет пользователя вместе с правами.
        Автоматически блокирует доступ, если юзер из чужого инстанса.
        """
        stmt = (
            select(Users)
            .where(Users.uuid == target_uuid)
            .options(joinedload(Users.permissions))
        )
        result = await self.db.execute(stmt)
        target_user = result.scalar_one_or_none()

        if not target_user:
            raise TargetUserNotFoundError(target_uuid=target_uuid)

        if target_user.instance_id != creator_instance_id:
            raise InstanceAccessDeniedError(
                target_uuid=target_uuid,
                target_instance_id=target_user.instance_id,
                creator_instance_id=creator_instance_id,
            )

        return target_user

    async def invite_user(self, creator: Users, email: str) -> None:
        result = await self.db.execute(select(Users).where(Users._email == email))
        if result.scalar_one_or_none():
            raise TargetUserAlreadyExistsError(email=email)

        redis_key = generate_key(prefix=USER_INVITE_PREFIX, sub=email)
        try:
            await self.redis.set(
                name=redis_key, ex=86400, value=str(creator.instance_id)
            )
        except Exception as e:
            raise InfrastructureStorageError(
                context_message="Failed to save employee invite to Redis", reason=str(e)
            )

    async def promote_to_creator(self, creator: Users, target_uuid: UUID) -> Users:
        target_user = await self._get_target_user_or_404(
            target_uuid, creator.instance_id
        )

        if target_user.role == UserRole.CREATOR:
            raise UserRoleStateError(
                target_uuid=target_uuid,
                current_role=UserRole.CREATOR.value,
                action="promote_to_creator",
            )

        target_user.role = UserRole.CREATOR

        if target_user.permissions:
            target_user.permissions.allowed_tools = [AppTools.ALL.value]
        else:
            new_permissions = UserPermissions(
                user_uuid=target_user.uuid, allowed_tools=[AppTools.ALL.value]
            )
            self.db.add(new_permissions)

        await self._commit_or_rollback("Failed to update user role and permissions")
        return target_user

    async def demote_to_user(self, creator: Users, target_uuid: UUID) -> Users:
        target_user = await self._get_target_user_or_404(
            target_uuid, creator.instance_id
        )

        if target_user.role == UserRole.USER:
            raise UserRoleStateError(
                target_uuid=target_uuid,
                current_role=UserRole.USER.value,
                action="demote_to_user",
            )

        if target_user.uuid == creator.uuid:
            raise SelfManagementDeniedError(
                creator_uuid=creator.uuid, action="demote_self"
            )

        target_user.role = UserRole.USER

        if target_user.permissions:
            target_user.permissions.allowed_tools = []
        else:
            new_permissions = UserPermissions(
                user_uuid=target_user.uuid, allowed_tools=[]
            )
            self.db.add(new_permissions)

        await self._commit_or_rollback("Failed to update user role and permissions")
        return target_user

    async def update_permissions(
        self, creator: Users, target_uuid: UUID, allowed_tools: list
    ) -> Users:
        target_user = await self._get_target_user_or_404(
            target_uuid, creator.instance_id
        )

        if target_user.role == UserRole.CREATOR:
            raise CreatorPermissionsUpdateError(target_uuid=target_uuid)

        tools_list = [tool.value for tool in allowed_tools]

        if target_user.permissions:
            target_user.permissions.allowed_tools = tools_list
        else:
            new_permissions = UserPermissions(
                user_uuid=target_user.uuid, allowed_tools=tools_list
            )
            self.db.add(new_permissions)

        await self._commit_or_rollback("Failed to update user permissions")
        return target_user

    async def deactivate_user(self, creator: Users, target_uuid: UUID) -> Users:
        target_user = await self._get_target_user_or_404(
            target_uuid, creator.instance_id
        )

        if target_user.uuid == creator.uuid:
            raise SelfManagementDeniedError(
                creator_uuid=creator.uuid, action="deactivate_self"
            )

        if target_user.role == UserRole.CREATOR:
            raise CreatorDeactivationDeniedError(target_uuid=target_uuid)

        if not target_user.active:
            raise TargetUserAlreadyInactiveError(target_uuid=target_uuid)

        target_user.active = False
        await self._commit_or_rollback("Failed to deactivate user")
        return target_user

    async def _commit_or_rollback(self, error_message: str) -> None:
        """Вспомогательный метод для безопасного коммита транзакций."""
        try:
            await self.db.commit()
        except Exception as e:
            await self.db.rollback()
            raise InfrastructureStorageError(
                context_message=error_message, reason=str(e)
            )

    async def list_instance_users(self, creator: Users) -> list[Users]:
        """
        Возвращает список всех пользователей (сотрудников и других креаторов),
        привязанных к текущему инстансу компании.
        """
        stmt = (
            select(Users)
            .where(Users.instance_id == creator.instance_id)
            .options(joinedload(Users.permissions))
            .order_by(Users._email)
        )
        result = await self.db.execute(stmt)
        return result.scalars().all()
