# core/services/history.py

from uuid import UUID
from typing import List, Dict, Any
from mongo.history import HistoryRepository
from core.exceptions.history import UserInstanceNotFoundError
from users.models import Users


class HistoryService:
    def __init__(self, history_repo: HistoryRepository):
        # Репозиторий внедряется через конструктор (Dependency Injection)
        self.history_repo = history_repo

    async def get_field_history(
        self, current_user: Users, record_uuid: UUID, field_name: str
    ) -> List[Dict[str, Any]]:
        """
        Бизнес-логика получения истории поля с проверкой прав инстанса.
        Ничего не знает про HTTP-статусы.
        """
        # 1. Бизнес-валидация
        if not current_user.instance_id:
            raise UserInstanceNotFoundError()

        # 2. Обращение к слою данных (Data Access Layer)
        return await self.history_repo.get_field_history(
            instance_uuid=str(current_user.instance_id),
            record_uuid=str(record_uuid),
            field_name=field_name,
        )

    async def get_full_record_history(
        self, current_user: Users, record_uuid: UUID
    ) -> List[Dict[str, Any]]:
        """
        Бизнес-логика получения ПОЛНОЙ истории изменений (все снапшоты).
        Проверяет привязку к инстансу и запрашивает данные из репозитория.
        """
        # 1. Бизнес-валидация (переиспользуем ту же ошибку)
        if not current_user.instance_id:
            raise UserInstanceNotFoundError()

        # 2. Обращение к слою данных
        return await self.history_repo.get_record_history(
            instance_uuid=str(current_user.instance_id), record_uuid=str(record_uuid)
        )
