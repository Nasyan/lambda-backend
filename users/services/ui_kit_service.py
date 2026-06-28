from typing import Optional, Dict, Any
from pydantic import ValidationError
from users.ui_schemas import UiKitSchema, UiKitItemSchema, PositionSchema


class UiKitManager:
    """
    Низкоуровневый класс для валидации, чтения и безопасной модификации JSON поля ui_kits
    """

    def __init__(self, raw_data: Optional[Dict[str, Any]] = None):
        if not raw_data:
            self.data = UiKitSchema()
        else:
            try:
                self.data = UiKitSchema(**raw_data)
            except ValidationError as e:
                print(f" Warning: Искаженная структура ui_kit в БД: {e}")
                self.data = UiKitSchema()

    def get_dict(self) -> Dict[str, Any]:
        """Превращает Pydantic-модель обратно в dict, безопасно сериализуя UUID и Enum"""
        return self.data.model_dump(mode="json")

    def add_favorite(self, item: UiKitItemSchema) -> None:
        """Добавить элемент (или перезаписать, если такой UUID уже есть)"""
        self.data.favorites = [f for f in self.data.favorites if f.uuid != item.uuid]
        self.data.favorites.append(item)

    def update_position(self, item_uuid: Any, new_position: PositionSchema) -> bool:
        """
        Точечное обновление позиции элемента.
        Возвращает True, если элемент найден и обновлен, иначе False.
        """
        str_uuid = str(item_uuid)
        for item in self.data.favorites:
            if str(item.uuid) == str_uuid:
                item.position = new_position
                return True
        return False

    def remove_favorite(self, entity_uuid: Any) -> None:
        """Удалить элемент из избранного"""
        str_uuid = str(entity_uuid)
        self.data.favorites = [
            f for f in self.data.favorites if str(f.uuid) != str_uuid
        ]

    def clear_favorites(self) -> None:
        """Полная очистка списка"""
        self.data.favorites = []
