from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from database.db import get_db
from jsonwebtoken.utils import (
    get_current_user,
    get_current_creator,
)
from users.models import Users, UserLanguage
from users.ui_schemas import UiKitSchema, UiKitItemSchema, PositionSchema
from users.services.user_settings_service import UserSettingsService
from users.services.user_profile import UserProfileService

router = APIRouter(prefix="/users/me", tags=["User Settings & Profile"])


@router.get("/context")
async def get_my_context(
    db: AsyncSession = Depends(get_db), current_user: Users = Depends(get_current_user)
):
    service = UserProfileService(db)
    return await service.get_dashboard_context(current_user)


@router.get("/creator/context", tags=["Creator Management"])
async def get_creator_dashboard_context(
    db: AsyncSession = Depends(get_db),
    current_creator: Users = Depends(get_current_creator),
):
    service = UserProfileService(db)
    return await service.get_creator_context(current_creator)


@router.post("/settings/god-mode")
async def set_god_mode(
    enabled: bool,
    db: AsyncSession = Depends(get_db),
    current_user: Users = Depends(get_current_creator),
):
    service = UserSettingsService(db)
    await service.toggle_god_mode(current_user, enabled)
    return {"status": "success", "god_mode": enabled}


@router.post("/settings/language")
async def set_language(
    lang: UserLanguage,
    db: AsyncSession = Depends(get_db),
    current_user: Users = Depends(get_current_user),
):
    service = UserSettingsService(db)
    await service.change_language(current_user, lang)
    return {"status": "success", "language": lang.value}


@router.get("/ui-kit", response_model=UiKitSchema)
async def get_ui_kit(
    db: AsyncSession = Depends(get_db),
    current_user: Users = Depends(get_current_user),
):
    """READ: Получить только текущий UI Kit пользователя"""
    settings = current_user.settings
    if not settings or not settings.ui_kits:
        return UiKitSchema()
    return UiKitSchema(**settings.ui_kits)


@router.put("/ui-kit")
async def replace_ui_kit(
    payload: UiKitSchema,
    db: AsyncSession = Depends(get_db),
    current_user: Users = Depends(get_current_user),
):
    """UPDATE ALL: Полная перезапись всего UI-кита (например, массовый импорт)"""
    service = UserSettingsService(db)
    await service.update_ui_kit(current_user, payload)
    return {"status": "success", "message": "UI Kit полностью обновлен"}


@router.post("/ui-kit/item")
async def add_ui_kit_item(
    item: UiKitItemSchema,
    db: AsyncSession = Depends(get_db),
    current_user: Users = Depends(get_current_user),
):
    """CREATE: Добавить новую карточку/виджет на доску"""
    service = UserSettingsService(db)
    await service.add_ui_kit_item(current_user, item)
    return {"status": "success", "message": "Элемент добавлен"}


@router.patch("/ui-kit/item/{item_uuid}/position")
async def update_item_position(
    item_uuid: str,
    position: PositionSchema,
    db: AsyncSession = Depends(get_db),
    current_user: Users = Depends(get_current_user),
):
    """UPDATE PARTIAL: Изменить только координаты (X, Y) при Drag & Drop"""
    service = UserSettingsService(db)
    try:
        await service.update_item_position(current_user, item_uuid, position)
        return {"status": "success", "message": "Позиция обновлена"}
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.delete("/ui-kit/item/{item_uuid}")
async def remove_ui_kit_item(
    item_uuid: str,
    db: AsyncSession = Depends(get_db),
    current_user: Users = Depends(get_current_user),
):
    """DELETE ONE: Удалить конкретный виджет с доски"""
    service = UserSettingsService(db)
    await service.remove_item_from_ui_kit(current_user, item_uuid)
    return {"status": "success", "message": "Элемент удален"}


@router.delete("/ui-kit")
async def clear_ui_kit(
    db: AsyncSession = Depends(get_db),
    current_user: Users = Depends(get_current_user),
):
    """DELETE ALL: Очистить всю доску избранного"""
    service = UserSettingsService(db)
    await service.clear_ui_kit(current_user)
    return {"status": "success", "message": "UI Kit очищен"}
