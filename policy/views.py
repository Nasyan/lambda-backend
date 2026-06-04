# policy/views.py

from fastapi import APIRouter, Depends, status
from typing import List
from uuid import UUID
from policy.schemas import PolicyCreate, PolicyUpdate, PolicyResponse
from policy.service import PolicyAdminService
from users.models import Users
from policy.dependecies import verify_creator_instance_access, get_policy_admin_service
from users.auth import RequireTool
from users.models import AppTools

router = APIRouter(
    prefix="/instances/{instance_uuid}/storefront-configs",
    tags=["CRM Admin: Настройка Витрин"],
    dependencies=[Depends(RequireTool(AppTools.POLICY))],
)


@router.post("", response_model=PolicyResponse, status_code=status.HTTP_201_CREATED)
async def create_storefront_policy(
    instance_uuid: UUID,
    payload: PolicyCreate,
    # Применяем зависимость верификации инстанса и роли Креатора
    current_user: Users = Depends(verify_creator_instance_access),
    service: PolicyAdminService = Depends(get_policy_admin_service),
):
    """
    Создать новое правило безопасности и отображения для витрины.
    Доступно только владельцу инстанса (CREATOR).
    """
    return await service.create_policy(instance_uuid, payload)


@router.get("", response_model=List[PolicyResponse])
async def list_storefront_policies(
    instance_uuid: UUID,
    current_user: Users = Depends(verify_creator_instance_access),
    service: PolicyAdminService = Depends(get_policy_admin_service),
):
    """
    Получить список всех настроенных правил витрины для инстанса.
    Доступно только владельцу инстанса (CREATOR).
    """
    return await service.get_policies_list(instance_uuid)


@router.patch("/{policy_id}", response_model=PolicyResponse)
async def update_storefront_policy(
    instance_uuid: UUID,
    policy_id: UUID,
    payload: PolicyUpdate,
    current_user: Users = Depends(verify_creator_instance_access),
    service: PolicyAdminService = Depends(get_policy_admin_service),
):
    """
    Обновить существующую маску доступа или фильтры витрины.
    Доступно только владельцу инстанса (CREATOR).
    """
    return await service.update_policy(instance_uuid, policy_id, payload)


@router.delete("/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_storefront_policy(
    instance_uuid: UUID,
    policy_id: UUID,
    current_user: Users = Depends(verify_creator_instance_access),
    service: PolicyAdminService = Depends(get_policy_admin_service),
):
    """
    Удалить политику безопасности витрины.
    Доступно только владельцу инстанса (CREATOR).
    """
    await service.delete_policy(instance_uuid, policy_id)
