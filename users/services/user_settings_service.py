from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession

from users.models import Users, UserSettings, UserLanguage
from users.ui_schemas import UiKitSchema, UiKitItemSchema, PositionSchema
from users.services.ui_kit_service import UiKitManager


class UserSettingsService:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def _ensure_settings_exist(self, user: Users) -> UserSettings:
        if not user.settings:
            settings = UserSettings(user_uuid=user.uuid, god_mode=False, ui_kits={})
            user.settings = settings
            self.db.add(settings)
            await self.db.flush()
        return user.settings

    # --- Существующие методы ---
    async def toggle_god_mode(self, user: Users, enabled: bool) -> UserSettings:
        settings = await self._ensure_settings_exist(user)
        settings.god_mode = enabled
        await self.db.commit()
        return settings

    async def change_language(
        self, user: Users, language: UserLanguage
    ) -> UserSettings:
        settings = await self._ensure_settings_exist(user)
        settings.language = language
        await self.db.commit()
        return settings

    async def update_ui_kit(
        self, user: Users, new_kit_data: UiKitSchema
    ) -> UserSettings:
        """Полная перезапись UI-кита (Массовое обновление)"""
        settings = await self._ensure_settings_exist(user)
        manager = UiKitManager(new_kit_data.model_dump(mode="json"))
        settings.ui_kits = manager.get_dict()
        await self.db.commit()
        return settings

    # --- НОВЫЕ CRUD МЕТОДЫ ДЛЯ UI KIT ---

    async def add_ui_kit_item(self, user: Users, item: UiKitItemSchema) -> UserSettings:
        """Добавление одного нового элемента в сетку"""
        settings = await self._ensure_settings_exist(user)
        manager = UiKitManager(settings.ui_kits)

        manager.add_favorite(item)
        settings.ui_kits = manager.get_dict()

        await self.db.commit()
        return settings

    async def update_item_position(
        self, user: Users, item_uuid: Any, position: PositionSchema
    ) -> UserSettings:
        """Обновление позиции конкретного виджета (Drag & Drop)"""
        settings = await self._ensure_settings_exist(user)
        manager = UiKitManager(settings.ui_kits)

        is_updated = manager.update_position(item_uuid, position)
        if not is_updated:
            raise ValueError(f"Элемент с UUID {item_uuid} не найден в UI Kit")

        settings.ui_kits = manager.get_dict()
        await self.db.commit()
        return settings

    async def remove_item_from_ui_kit(
        self, user: Users, item_uuid: Any
    ) -> UserSettings:
        """Удаление одного элемента"""
        settings = await self._ensure_settings_exist(user)
        manager = UiKitManager(settings.ui_kits)

        manager.remove_favorite(item_uuid)
        settings.ui_kits = manager.get_dict()

        await self.db.commit()
        return settings

    async def clear_ui_kit(self, user: Users) -> UserSettings:
        """Полная очистка сетки"""
        settings = await self._ensure_settings_exist(user)
        manager = UiKitManager(settings.ui_kits)

        manager.clear_favorites()
        settings.ui_kits = manager.get_dict()

        await self.db.commit()
        return settings
