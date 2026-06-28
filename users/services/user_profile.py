from typing import List, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from users.models import Users
from users.properties import get_user_account_summary, get_safe_name, get_safe_tools
from sqlalchemy.orm import joinedload


class UserProfileService:
    """
    Сервис для агрегации и отдачи данных о пользователе.
    Работает только на чтение (Read-only).
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    async def get_team_members(self, user: Users) -> List[Dict[str, Any]]:
        """
        Получение списка коллег по инстансу (команды).
        """
        if not user.instance_id:
            return []

        # Ищем всех пользователей, привязанных к тому же инстансу
        stmt = select(Users).where(
            Users.instance_id == user.instance_id,
            Users.active,
        )
        result = await self.db.execute(stmt)
        teammates = result.scalars().all()

        # Собираем безопасный список коллег
        return [
            {
                "uuid": str(t.uuid),
                "name": get_safe_name(t),
                "email": t.email,
                "role": t.role.value,
                # Флаг, чтобы фронтенд понимал, где в списке сам текущий юзер
                "is_current_user": t.uuid == user.uuid,
            }
            for t in teammates
        ]

    async def get_dashboard_context(self, user: Users) -> Dict[str, Any]:
        """
        Главный метод сборки контекста для старта приложения.
        Отдает профиль, настройки, инструменты и команду в одном JSON.
        """
        # 1. Берем базовую безопасную информацию из селектора (которую мы писали ранее)
        profile_data = get_user_account_summary(user)

        # 2. Подтягиваем список команды из базы данных
        team_data = await self.get_team_members(user)

        # 3. Формируем единый ответ
        return {
            "profile": profile_data,
            "team": team_data,
            # В будущем этот метод можно легко расширить, добавив:
            # "notifications": await self.get_unread_alerts(user),
            # "recent_activity": await self.get_recent_logs(user),
            # "subscription": await self.get_billing_info(user.instance_id)
        }

    async def get_team_with_permissions(self, instance_id: Any) -> List[Dict[str, Any]]:
        """Получение всех членов команды инстанса вместе с их пермишенами."""
        if not instance_id:
            return []

        stmt = (
            select(Users)
            .where(Users.instance_id == instance_id)
            .options(joinedload(Users.permissions))
        )
        result = await self.db.execute(stmt)
        members = result.scalars().unique().all()

        return [
            {
                "uuid": str(member.uuid),
                "name": member.name or "User",
                "email": member.email,
                "role": member.role.value,
                "active": member.active,
                "allowed_tools": get_safe_tools(
                    member
                ),  # Извлекаем пермишены каждого сотрудника
            }
            for member in members
        ]

    async def get_creator_context(self, creator: Users) -> Dict[str, Any]:
        """
        Специализированный контекст для Креатора.
        Включает полную сводку команды с правами доступа каждого юзера.
        """
        # 1. Сводка самого Креатора (профиль, его личные настройки и права)
        profile_data = get_user_account_summary(creator)

        # 2. Список команды, расширенный их пермишенами
        team_with_perms = await self.get_team_with_permissions(creator.instance_id)

        return {
            "profile": profile_data,
            "team": team_with_perms,
        }
