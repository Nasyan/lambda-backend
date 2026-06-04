# notifications/views.py
from typing import List
from uuid import UUID
from fastapi import APIRouter, Depends, status, Response
from middleware.schemas import ListParameters

from database.db import get_db
from jsonwebtoken.utils import get_current_active_user
from mongo.dependecies import (
    get_template_repository,
)
from users.models import Users

from notifications.schemas import (
    TemplateCreate,
    TemplateUpdate,
    TemplateResponse,
    InboxItemResponse,
)
from notifications.dependecies import verify_creator_and_instance, verify_user_instance
from notifications.service import NotificationTemplateService
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(
    prefix="/instances/{instance_uuid}/notifications",
    tags=["Notifications"],
)


@router.post("/templates", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_template(
    instance_uuid: UUID,
    payload: TemplateCreate,
    current_user: Users = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
    mongo_template_repo=Depends(get_template_repository),
):
    """Создание нового шаблона уведомлений (Доступно только Создателю инстанса)"""
    verify_creator_and_instance(instance_uuid, current_user)

    template_uuid = await NotificationTemplateService.create_template(
        db=db,
        instance_uuid=instance_uuid,
        payload_data=payload.model_dump(),
        mongo_template_repo=mongo_template_repo,
    )
    return {"uuid": template_uuid}


@router.get("/templates", response_model=List[TemplateResponse])
async def get_templates(
    instance_uuid: UUID,
    params: ListParameters = Depends(),
    current_user: Users = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Получение списка всех шаблонов инстанса с поддержкой фильтрации и сортировки."""
    verify_creator_and_instance(instance_uuid, current_user)
    return await NotificationTemplateService.get_templates(db, instance_uuid, params)


@router.get("/templates/{template_uuid}", response_model=TemplateResponse)
async def get_template_by_uuid(
    instance_uuid: UUID,
    template_uuid: UUID,
    current_user: Users = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Получение детальной информации о конкретном шаблоне"""
    verify_creator_and_instance(instance_uuid, current_user)
    return await NotificationTemplateService.get_template_by_uuid(
        db, instance_uuid, template_uuid
    )


@router.patch("/templates/{template_uuid}", response_model=dict)
async def update_template(
    instance_uuid: UUID,
    template_uuid: UUID,
    payload: TemplateUpdate,
    current_user: Users = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
    mongo_template_repo=Depends(get_template_repository),
):
    """Частичное обновление настроек или текста шаблона"""
    verify_creator_and_instance(instance_uuid, current_user)

    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        return {"status": "no_changes"}

    updated_uuid = await NotificationTemplateService.update_template(
        db=db,
        instance_uuid=instance_uuid,
        template_uuid=template_uuid,
        update_data=update_data,
        mongo_template_repo=mongo_template_repo,
    )
    return {"status": "updated", "uuid": updated_uuid}


@router.delete("/templates/{template_uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    instance_uuid: UUID,
    template_uuid: UUID,
    current_user: Users = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Удаление шаблона уведомлений с проверкой целостности схемы системы."""
    verify_creator_and_instance(instance_uuid, current_user)
    await NotificationTemplateService.delete_template(db, instance_uuid, template_uuid)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/inbox", response_model=List[InboxItemResponse])
async def get_my_inbox(
    instance_uuid: UUID,
    current_user: Users = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Получение списка уведомлений («колокольчик») текущего сотрудника"""
    verify_user_instance(instance_uuid, current_user)

    inbox_items = await NotificationTemplateService.get_user_inbox(
        db, current_user.uuid
    )
    return [
        InboxItemResponse(
            uuid=item.uuid,
            is_read=item.is_read,
            created_at=item.created_at,
            title=item.history.compiled_title,
            body=item.history.compiled_body,
        )
        for item in inbox_items
    ]


@router.patch("/inbox/{notification_uuid}/read", response_model=dict)
async def mark_as_read(
    instance_uuid: UUID,
    notification_uuid: UUID,
    current_user: Users = Depends(get_current_active_user),
    db: AsyncSession = Depends(get_db),
):
    """Пометить уведомление в инбоксе как прочитанное"""
    verify_user_instance(instance_uuid, current_user)

    await NotificationTemplateService.mark_inbox_as_read(
        db=db, user_uuid=current_user.uuid, notification_uuid=notification_uuid
    )
    return {"status": "ok"}
