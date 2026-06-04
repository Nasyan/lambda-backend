# analytics/views.py

from uuid import UUID
from typing import List
from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession
from motor.motor_asyncio import AsyncIOMotorDatabase

from database.db import get_db
from mongo.db import get_mongo_db
from core.dependencies import get_current_instance_creator
from users.models import Instances, Users, AppTools
from jsonwebtoken.utils import get_current_user

from analytics.schemas import WidgetCreateRequest, WidgetResponse, WidgetUpdateRequest

# 🔥 Убедись, что путь импорта соответствует твоей структуре папок
from analytics.widget import WidgetService
from users.auth import RequireTool

router = APIRouter(
    prefix="/instances/{instance_uuid}/widgets",
    tags=["Analytics Widgets"],
    dependencies=[Depends(RequireTool(AppTools.TEMPLATES))],
)


@router.post("", response_model=WidgetResponse, status_code=status.HTTP_201_CREATED)
async def create_widget(
    instance_uuid: UUID,
    payload: WidgetCreateRequest,
    instance: Instances = Depends(get_current_instance_creator),
    current_user: Users = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Создает новый график/виджет для дашборда текущего инстанса"""
    # Дополнительная защита: принудительно пишем UUID инстанса из проверенной зависимости,
    # чтобы пользователь не мог подделать его внутри payload.
    return await WidgetService.create_widget(
        instance_uuid=instance.uuid, payload=payload, db=db
    )


@router.get("/{widget_uuid}/data", response_model=List[dict])
async def get_widget_data(
    instance_uuid: UUID,
    widget_uuid: UUID,
    instance: Instances = Depends(get_current_instance_creator),
    current_user: Users = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    mongo_db: AsyncIOMotorDatabase = Depends(
        get_mongo_db
    ),  # 🔥 Добавили явный тип Motor
):
    """
    Главный эндпоинт аналитики.
    Агрегирует данные в MongoDB на лету с полной поддержкой AST-формул и кросс-таблиц.
    Возвращает легкий массив вида [{"label": "2026-05", "value": 15000}, ...]
    """
    # 🔥 Нам нужно передавать instance_uuid в сервис для сквозной проверки
    # (чтобы пользователь из Тенанта А не мог вытащить данные виджета Тенанта Б).
    return await WidgetService.get_widget_data(
        widget_uuid=widget_uuid,
        instance_uuid=instance.uuid,  # Передаем изолированный ID инстанса
        db=db,
        mongo_db=mongo_db,
    )


@router.patch("/{widget_uuid}", response_model=WidgetResponse)
async def update_widget(
    instance_uuid: UUID,
    widget_uuid: UUID,
    payload: WidgetUpdateRequest,
    instance: Instances = Depends(get_current_instance_creator),
    current_user: Users = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Частичное обновление настроек, фильтров или типа графика"""
    return await WidgetService.update_widget(
        widget_uuid=widget_uuid,
        instance_uuid=instance.uuid,  # Защита изоляции
        payload=payload,
        db=db,
    )


@router.delete("/{widget_uuid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_widget(
    instance_uuid: UUID,
    widget_uuid: UUID,
    instance: Instances = Depends(get_current_instance_creator),
    current_user: Users = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Удаление виджета аналитики"""
    await WidgetService.delete_widget(
        widget_uuid=widget_uuid, instance_uuid=instance.uuid, db=db  # Защита изоляции
    )
