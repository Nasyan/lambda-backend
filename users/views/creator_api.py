# users/views/creator_api.py

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import get_db
from redisdb.utils import get_redis_db
from users.models import Users
from users.schemas import (
    UserInviteRequest,
    PromoteUserRequest,
    UserRoleChangeRequest,
    UpdateUserPermissionsRequest,
)

from jsonwebtoken.utils import get_current_creator
from users.services.creator_service import CreatorService

router = APIRouter(prefix="/creator", tags=["creator"])


def get_creator_service(
    session: AsyncSession = Depends(get_db),
    redis_client=Depends(get_redis_db("EMAIL_DB")),
) -> CreatorService:
    """Провайдер зависимости для сборки сервиса Креатора."""
    return CreatorService(db_session=session, redis_client=redis_client)


@router.post("/invite-user")
async def invite_user(
    payload: UserInviteRequest,
    current_user: Users = Depends(get_current_creator),
    service: CreatorService = Depends(get_creator_service),
):
    """Генерация инвайта для обычного USER (сотрудника) на 24 часа."""
    await service.invite_user(current_user, payload.email)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "success",
            "message": f"Employee email {payload.email} successfully invited to your instance for 24 hours.",
        },
    )


@router.post("/promote-to-creator")
async def promote_to_creator(
    payload: PromoteUserRequest,
    current_user: Users = Depends(get_current_creator),
    service: CreatorService = Depends(get_creator_service),
):
    """Повышение обычного USER до CREATOR с выдачей полных прав."""
    target_user = await service.promote_to_creator(current_user, payload.user_uuid)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "success",
            "message": f"User {target_user.email} has been successfully promoted to CREATOR with full tool access.",
        },
    )


@router.post("/demote-to-user")
async def demote_to_user(
    payload: UserRoleChangeRequest,
    current_user: Users = Depends(get_current_creator),
    service: CreatorService = Depends(get_creator_service),
):
    """Понижение пользователя до роли USER со сбросом глобальных доступов."""
    target_user = await service.demote_to_user(current_user, payload.user_uuid)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "success",
            "message": f"User {target_user.email} has been successfully demoted to USER. Permissions cleared.",
        },
    )


@router.post("/update-permissions")
async def update_user_permissions(
    payload: UpdateUserPermissionsRequest,
    current_user: Users = Depends(get_current_creator),
    service: CreatorService = Depends(get_creator_service),
):
    """Настройка точечных доступов к инструментам CRM для обычного USER."""
    target_user = await service.update_permissions(
        current_user, payload.user_uuid, payload.allowed_tools
    )
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "success",
            "message": f"Permissions for user {target_user.email} successfully updated.",
            "allowed_tools": [tool.value for tool in payload.allowed_tools],
        },
    )


@router.post("/deactivate-user")
async def deactivate_user(
    payload: UserRoleChangeRequest,
    current_user: Users = Depends(get_current_creator),
    service: CreatorService = Depends(get_creator_service),
):
    """Деактивация (бан) сотрудника в рамках своего инстанса."""
    target_user = await service.deactivate_user(current_user, payload.user_uuid)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "success",
            "message": f"User {target_user.email} has been successfully deactivated.",
        },
    )


@router.get("/users", response_model=None)
async def list_instance_users(
    current_user: Users = Depends(get_current_creator),
    service: CreatorService = Depends(get_creator_service),
):
    """
    Получение списка всех пользователей, зарегистрированных в инстансе текущего Креатора.
    Учитывает связь One-to-One с UserPermissions и массив строк allowed_tools.
    """
    users = await service.list_instance_users(current_user)

    result = []
    for user in users:
        # Безопасно извлекаем инструменты.
        # Если записи в user_permissions еще нет, по дефолту у твоей модели ["all"],
        # но на случай None в БД подстрахуемся пустым списком.
        allowed_tools_list = []
        if user.permissions and user.permissions.allowed_tools:
            allowed_tools_list = user.permissions.allowed_tools

        result.append(
            {
                "uuid": str(user.uuid),
                "email": user.email,  # Использует твой hybrid_property
                "name": user.name,
                "role": user.role.value,  # Конвертируем Enum в строку для фронтенда
                "active": user.active,
                "allowed_tools": allowed_tools_list,
            }
        )

    return result
