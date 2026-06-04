# core/permissions.py

from fastapi import Depends
from users.models import Users, UserRole, AppTools
from jsonwebtoken.utils import get_current_active_user

# Импортируем профессиональные доменные ошибки безопасности
from core.exceptions.permission import (
    PermissionsNotConfiguredError,
    ToolAccessDeniedError,
)


class RequireTool:
    def __init__(self, required_tool: AppTools):
        """Принимает инструмент, который запрашивает эндпоинт."""
        self.required_tool = required_tool

    async def __call__(
        self, current_user: Users = Depends(get_current_active_user)
    ) -> Users:
        # 1. Если это ADMIN или CREATOR — у них сквозной доступ ко всему по умолчанию
        if current_user.role in (UserRole.ADMIN, UserRole.CREATOR):
            return current_user

        # 2. Проверяем связь с таблицей разрешений
        user_perms = current_user.permissions
        if not user_perms:
            # Исключение само соберет нужный JSON-контекст
            raise PermissionsNotConfiguredError(
                user_uuid=current_user.uuid, role=current_user.role.value
            )

        # 3. Проверяем, есть ли у пользователя глобальный доступ ("all")
        # или конкретный запрашиваемый инструмент
        has_all_access = AppTools.ALL.value in user_perms.allowed_tools
        has_specific_tool = self.required_tool.value in user_perms.allowed_tools

        if has_all_access or has_specific_tool:
            return current_user

        raise ToolAccessDeniedError(
            user_uuid=current_user.uuid,
            role=current_user.role.value,
            required_tool=self.required_tool.value,
            allowed_tools=user_perms.allowed_tools,
        )
