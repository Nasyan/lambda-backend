from typing import List, Dict, Any
from users.models import Users, UserLanguage, UserRole


def get_safe_name(user: Users) -> str:
    """Безопасное получение имени пользователя (защита от None)"""
    return user.name if user.name else "User"


def get_safe_ui_kits(user: Users) -> dict:
    """Безопасное получение ui_kits, даже если настроек нет в БД"""
    if user.settings and user.settings.ui_kits:
        return user.settings.ui_kits
    return {}


def is_god_mode_enabled(user: Users) -> bool:
    """Безопасное получение статуса god_mode"""
    if user.settings:
        return user.settings.god_mode
    return False


def get_safe_language(user: Users) -> str:
    """Безопасное получение языка интерфейса (дефолт 'ru')"""
    if user.settings and user.settings.language:
        return user.settings.language.value
    return UserLanguage.RU.value


def get_safe_tools(user: Users) -> List[str]:
    """Безопасное получение списка разрешенных инструментов"""
    if user.permissions and user.permissions.allowed_tools:
        return user.permissions.allowed_tools
    return ["all"]


def is_user_admin(user: Users) -> bool:
    """Проверка: является ли пользователь админом"""
    return user.role == UserRole.ADMIN


def is_user_creator(user: Users) -> bool:
    """Проверка: является ли пользователь создателем инстанса"""
    return user.role == UserRole.CREATOR


def get_user_account_summary(user: Users) -> Dict[str, Any]:
    """
    Максимальный профиль безопасной публичной информации пользователя.
    Используется для сериализации (например, при ответе на фронтенд)
    """
    return {
        "uuid": str(user.uuid),
        "email": user.email,
        "name": get_safe_name(user),
        "role": user.role.value,
        "active": user.active,
        "instance_id": str(user.instance_id) if user.instance_id else None,
        "permissions": {
            "allowed_tools": get_safe_tools(user),
        },
        "settings": {
            "god_mode": is_god_mode_enabled(user),
            "language": get_safe_language(user),
            "ui_kits": get_safe_ui_kits(user),
        },
    }
