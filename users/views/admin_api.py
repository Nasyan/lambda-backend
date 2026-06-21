# users/views/admin_api.py

from typing import Annotated, List
from uuid import UUID
from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import get_db
from redisdb.utils import get_redis_db
from users.models import Users
from users.schemas import (
    CreatorInviteRequest,
    TokenResponse,
    InstanceCreateRequest,
    InstanceResponse,
    CreatorResponse,
)
from jsonwebtoken.utils import get_current_admin
from users.services.admin_service import AdminService

router = APIRouter(prefix="/admin", tags=["admin"])


# Хелпер для получения сервиса
def get_admin_service(
    session: AsyncSession = Depends(get_db),
    redis_client=Depends(get_redis_db("EMAIL_DB")),
) -> AdminService:
    return AdminService(session, redis_client)


@router.post("/login/", response_model=TokenResponse)
async def admin_login(
    form_data: Annotated[OAuth2PasswordRequestForm, Depends()],
    service: AdminService = Depends(get_admin_service),
):
    token = await service.authenticate_admin(form_data.username, form_data.password)
    return TokenResponse(access_token=token, token_type="bearer")


@router.post("/invite-creator/")
async def invite_creator(
    payload: CreatorInviteRequest,
    service: AdminService = Depends(get_admin_service),
    current_user: Users = Depends(get_current_admin),
):
    instance_title = await service.invite_creator(payload.email, payload.instance_id)
    return JSONResponse(
        status_code=status.HTTP_200_OK,
        content={
            "status": "success",
            "message": f"Email {payload.email} successfully invited to instance '{instance_title}' for 24 hours.",
        },
    )


@router.post(
    "/instances/", response_model=InstanceResponse, status_code=status.HTTP_201_CREATED
)
async def create_instance(
    payload: InstanceCreateRequest,
    service: AdminService = Depends(get_admin_service),
    current_user: Users = Depends(get_current_admin),
):
    return await service.create_instance(payload.title)


@router.get("/instances/", response_model=List[InstanceResponse])
async def list_instances(
    service: AdminService = Depends(get_admin_service),
    current_user: Users = Depends(get_current_admin),
):
    return await service.list_all_instances()


@router.get("/creators/", response_model=List[CreatorResponse])
async def list_creators(
    service: AdminService = Depends(get_admin_service),
    current_user: Users = Depends(get_current_admin),
):
    return await service.list_creators()


@router.get("/creators/{creator_uuid}", response_model=CreatorResponse)
async def get_creator_by_uuid(
    creator_uuid: UUID,
    service: AdminService = Depends(get_admin_service),
    current_user: Users = Depends(get_current_admin),
):
    return await service.get_creator_or_404(creator_uuid)


@router.patch("/creators/{creator_uuid}/deactivate", response_model=CreatorResponse)
async def deactivate_creator(
    creator_uuid: UUID,
    service: AdminService = Depends(get_admin_service),
    current_user: Users = Depends(get_current_admin),
):
    return await service.deactivate_creator(creator_uuid)


@router.delete("/users/{user_uuid}/", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    user_uuid: UUID,
    service: AdminService = Depends(get_admin_service),
    current_user: Users = Depends(get_current_admin),
):
    """
    Эндпоинт для полного удаления пользователя из CRM по его UUID.
    """
    await service.delete_user(user_uuid)


@router.delete("/instances/{instance_id}/", status_code=status.HTTP_204_NO_CONTENT)
async def delete_instance(
    instance_id: UUID,
    service: AdminService = Depends(get_admin_service),
    current_user: Users = Depends(get_current_admin),
):
    """
    Эндпоинт для полного удаления инстанса компании по его UUID.
    """
    await service.delete_instance(instance_id)
