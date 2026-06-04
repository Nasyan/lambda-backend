# users/auth.py

from fastapi import Depends, HTTPException, status
from users.models import Users, UserRole, AppTools

# Импортируем именно active_user, чтобы сохранить единую цепочку проверок
from jsonwebtoken.utils import get_current_active_user


class RequireTool:
    def __init__(self, required_tool: AppTools):
        """Фабрика принимает инструмент, который требуется для данного эндпоинта."""
        self.required_tool = required_tool

    async def __call__(
        self, current_user: Users = Depends(get_current_active_user)
    ) -> Users:
        # Сценарий 1: Админы и Создатели имеют полный доступ ко всему по умолчанию
        if current_user.role in (UserRole.ADMIN, UserRole.CREATOR):
            return current_user

        # Сценарий 2: Если у пользователя нет записи пермишенов
        if not current_user.permissions:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="У вас нет назначенных прав доступа к инструментам системы.",
            )

        allowed_tools = current_user.permissions.allowed_tools

        # Сценарий 3: Проверяем маркер "all" или явное присутствие инструмента
        if "all" in allowed_tools or self.required_tool.value in allowed_tools:
            return current_user

        # Сценарий 4: Доступ запрещен
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Доступ запрещен. Требуется доступ к инструменту: '{self.required_tool.value}'.",
        )
