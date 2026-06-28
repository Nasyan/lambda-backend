# core/views/template.py

from typing import List
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import get_db
from core.dependencies import get_current_instance_creator, get_template_service
from users.models import Instances, Users, AppTools
from users.auth import RequireTool
from jsonwebtoken.utils import get_current_user
from core.services.template import TemplateService

from core.schemas.template import (
    ColumnAddOrUpdateRequest,
    TemplateCreateRequest,
    TemplateResponse,
    TemplateUpdateMetadataRequest,
)
from middleware.schemas import ListParameters

router = APIRouter(
    prefix="/instances/{instance_uuid}/templates",
    tags=["Templates"],
    dependencies=[Depends(RequireTool(AppTools.TEMPLATES))],
)


@router.get("", response_model=List[TemplateResponse])
async def get_templates(
    instance_uuid: UUID,
    params: ListParameters = Depends(),
    instance: Instances = Depends(get_current_instance_creator),
    template_service: TemplateService = Depends(get_template_service),
):
    """Получение всех No-Code таблиц инстанса с поддержкой фильтрации и сортировки."""
    return await template_service.get_all_templates(
        instance_uuid=instance_uuid, params=params
    )


@router.get("/deleted", response_model=List[TemplateResponse])
async def get_deleted_templates(
    instance_uuid: UUID,
    params: ListParameters = Depends(),
    instance: Instances = Depends(get_current_instance_creator),
    template_service: TemplateService = Depends(get_template_service),
):
    return await template_service.get_deleted_templates(
        instance_uuid=instance_uuid, params=params
    )


@router.post("", response_model=TemplateResponse, status_code=status.HTTP_201_CREATED)
async def create_template(
    instance_uuid: UUID,
    payload: TemplateCreateRequest,
    instance: Instances = Depends(get_current_instance_creator),
    current_user: Users = Depends(get_current_user),
    template_service: TemplateService = Depends(get_template_service),
):
    return await template_service.create_template(
        instance_uuid=instance_uuid,
        name=payload.name,
        schema_definition=payload.schema_definition,
        user_uuid=current_user.uuid,
    )


@router.delete("/{template_uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(
    instance_uuid: UUID,
    template_uuid: UUID,
    instance: Instances = Depends(get_current_instance_creator),
    current_user: Users = Depends(get_current_user),
    template_service: TemplateService = Depends(get_template_service),
    db: AsyncSession = Depends(get_db),
):
    await template_service.delete_template(
        instance_uuid=instance_uuid,
        template_uuid=template_uuid,
        db=db,
    )


@router.post("/{template_uuid}/restore", response_model=TemplateResponse)
async def restore_template(
    instance_uuid: UUID,
    template_uuid: UUID,
    instance: Instances = Depends(get_current_instance_creator),
    current_user: Users = Depends(get_current_user),
    template_service: TemplateService = Depends(get_template_service),
):
    return await template_service.restore_template(
        instance_uuid=instance_uuid,
        template_uuid=template_uuid,
    )


@router.post("/{template_uuid}/columns", response_model=TemplateResponse)
async def add_column(
    instance_uuid: UUID,
    template_uuid: UUID,
    payload: ColumnAddOrUpdateRequest,
    instance: Instances = Depends(get_current_instance_creator),
    current_user: Users = Depends(get_current_user),
    template_service: TemplateService = Depends(get_template_service),
):
    return await template_service.add_column(
        instance_uuid=instance_uuid,
        template_uuid=template_uuid,
        column_name=payload.column_name,
        field_meta=payload.field_meta,
        user_uuid=current_user.uuid,
    )


@router.patch("/{template_uuid}", response_model=TemplateResponse)
async def update_template_metadata(
    instance_uuid: UUID,
    template_uuid: UUID,
    payload: TemplateUpdateMetadataRequest,
    instance: Instances = Depends(get_current_instance_creator),
    current_user: Users = Depends(get_current_user),
    template_service: TemplateService = Depends(get_template_service),
):
    return await template_service.update_template_metadata(
        instance_uuid=instance_uuid,
        template_uuid=template_uuid,
        name=payload.name,
        user_uuid=current_user.uuid,
    )


@router.delete(
    "/{template_uuid}/columns/{column_name}",
    response_model=TemplateResponse,
)
async def drop_column(
    instance_uuid: UUID,
    template_uuid: UUID,
    column_name: str,
    instance: Instances = Depends(get_current_instance_creator),
    current_user: Users = Depends(get_current_user),
    template_service: TemplateService = Depends(get_template_service),
    db: AsyncSession = Depends(get_db),
):
    return await template_service.drop_column(
        instance_uuid=instance_uuid,
        template_uuid=template_uuid,
        column_name=column_name,
        user_uuid=current_user.uuid,
        db=db,
    )


@router.patch("/{template_uuid}/columns", response_model=TemplateResponse)
async def update_column_meta(
    instance_uuid: UUID,
    template_uuid: UUID,
    payload: ColumnAddOrUpdateRequest,
    instance: Instances = Depends(get_current_instance_creator),
    current_user: Users = Depends(get_current_user),
    template_service: TemplateService = Depends(get_template_service),
    db: AsyncSession = Depends(get_db),
):
    return await template_service.update_column_meta(
        instance_uuid=instance_uuid,
        template_uuid=template_uuid,
        column_name=payload.column_name,
        new_meta=payload.field_meta,
        user_uuid=current_user.uuid,
        db=db,
    )


@router.get("/{template_uuid}", response_model=TemplateResponse)
async def get_template(
    instance_uuid: UUID,
    template_uuid: UUID,
    instance: Instances = Depends(get_current_instance_creator),
    current_user: Users = Depends(get_current_user),
    template_service: TemplateService = Depends(get_template_service),
):
    return await template_service.get_template(
        instance_uuid=instance_uuid,
        template_uuid=template_uuid,
    )


@router.delete("/{template_uuid}/force", status_code=status.HTTP_204_NO_CONTENT)
async def force_delete_template(
    instance_uuid: UUID,
    template_uuid: UUID,
    instance: Instances = Depends(get_current_instance_creator),
    current_user: Users = Depends(get_current_user),
    template_service: TemplateService = Depends(get_template_service),
):
    """Безвозвратное (hard delete) удаление шаблона, его записей и истории.
    Возможно только для шаблонов, находящихся в корзине (is_deleted=True).
    """
    await template_service.force_delete_template(
        instance_uuid=instance_uuid,
        template_uuid=template_uuid,
        user_uuid=current_user.uuid,
    )
