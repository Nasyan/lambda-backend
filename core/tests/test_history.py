# core/tests/test_history.py

import pytest
from uuid import uuid4
from datetime import datetime, timezone
from users.models import UserRole, Instances
from mongo.history import HistoryRepository


@pytest.mark.asyncio
class TestHistoryAPI:

    async def test_get_field_history_api_success(self, auth_client, db_session):
        """
        Успешное получение истории конкретного поля через API-эндпоинт.
        """
        client, current_user = auth_client

        # 1. Привязываем нашего авторизованного пользователя к тест-инстансу в Postgres
        test_instance = Instances(
            uuid=uuid4(), title="Audit Analytics Inc", active=True
        )
        db_session.add(test_instance)

        current_user.role = UserRole.USER
        current_user.instance_id = test_instance.uuid
        await db_session.commit()

        # 2. Перехватываем тестовую базу Mongo из переопределений зависимостей приложения
        from main import app
        from mongo.db import get_mongo_db

        mongo_db = await anext(app.dependency_overrides[get_mongo_db]())

        history_repo = HistoryRepository(mongo_db)

        # Подготавливаем тестовые данные для истории в MongoDB
        record_uuid = uuid4()
        user_who_changed = uuid4()  # Кто-то, кто менял запись в прошлом

        # Записываем пару версий изменения поля "status" напрямую в Mongo через репозиторий
        await history_repo.collection.insert_many(
            [
                {
                    "instance_uuid": str(test_instance.uuid),
                    "record_uuid": str(record_uuid),
                    "user_uuid": str(user_who_changed),
                    "version": 1,
                    "snapshot": {"status": "draft", "title": "Report v1"},
                    "updated_at": datetime.now(timezone.utc),
                },
                {
                    "instance_uuid": str(test_instance.uuid),
                    "record_uuid": str(record_uuid),
                    "user_uuid": str(current_user.uuid),  # Изменил текущий юзер
                    "version": 2,
                    "snapshot": {"status": "published", "title": "Report v2"},
                    "updated_at": datetime.now(timezone.utc),
                },
            ]
        )

        # 3. Делаем GET запрос к нашему новому эндпоинту
        url = f"/history/field/{record_uuid}/status/"
        response = await client.get(url)

        # 4. Проверяем структуру ответа и сортировку
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "success"
        assert data["record_uuid"] == str(record_uuid)
        assert data["field_name"] == "status"

        history_list = data["history"]
        assert len(history_list) == 2

        # Проверяем сортировку по версии DESC (от последней к первой)
        assert history_list[0]["version"] == 2
        assert history_list[0]["value"] == "published"
        assert history_list[0]["user_uuid"] == str(current_user.uuid)

        assert history_list[1]["version"] == 1
        assert history_list[1]["value"] == "draft"
        assert history_list[1]["user_uuid"] == str(user_who_changed)

    async def test_get_field_history_api_isolation_forbidden(
        self, auth_client, db_session
    ):
        """
        Проверка безопасности: история пуста или не возвращается, если запись
        принадлежит чужому инстансу (жесткая фильтрация по инстансу текущего пользователя).
        """
        client, current_user = auth_client

        # Наш инстанс
        my_instance = Instances(uuid=uuid4(), title="My Vault", active=True)
        db_session.add(my_instance)
        current_user.instance_id = my_instance.uuid
        await db_session.commit()

        # Чужой инстанс
        foreign_instance_uuid = uuid4()

        # Наполняем базу Mongo чужими секретными логами
        from main import app
        from mongo.db import get_mongo_db

        mongo_db = await anext(app.dependency_overrides[get_mongo_db]())
        history_repo = HistoryRepository(mongo_db)

        secret_record_uuid = uuid4()

        await history_repo.log_change(
            instance_uuid=str(foreign_instance_uuid),  # Чужой инстанс!
            record_uuid=str(secret_record_uuid),
            user_uuid=str(uuid4()),
            version=1,
            snapshot={"status": "critical_data"},
        )

        # Пытаемся прочитать историю чужого record_uuid через наш аккаунт
        url = f"/history/field/{secret_record_uuid}/status/"
        response = await client.get(url)

        assert response.status_code == 200
        # Возвращается пустой список изменений, так как Mongo отфильтровал по текущему my_instance.uuid
        assert len(response.json()["history"]) == 0

    async def test_get_full_record_history_api_success(self, auth_client, db_session):
        """
        Успешное получение полной истории изменений всей записи через API.
        """
        client, current_user = auth_client

        # 1. Привязываем пользователя к тест-инстансу
        test_instance = Instances(uuid=uuid4(), title="Full Audit Studio", active=True)
        db_session.add(test_instance)
        current_user.instance_id = test_instance.uuid
        await db_session.commit()

        # 2. Перехватываем тестовую базу Mongo
        from main import app
        from mongo.db import get_mongo_db

        mongo_db = await anext(app.dependency_overrides[get_mongo_db]())
        history_repo = HistoryRepository(mongo_db)

        record_uuid = uuid4()
        user_modifier = uuid4()

        # Напрямую генерируем в Монго историю с полными объектами
        await history_repo.collection.insert_many(
            [
                {
                    "instance_uuid": str(test_instance.uuid),
                    "record_uuid": str(record_uuid),
                    "user_uuid": str(user_modifier),
                    "version": 1,
                    "snapshot": {
                        "title": "Task Alpha",
                        "status": "backlog",
                        "priority": "low",
                    },
                    "updated_at": datetime.now(timezone.utc),
                },
                {
                    "instance_uuid": str(test_instance.uuid),
                    "record_uuid": str(record_uuid),
                    "user_uuid": str(current_user.uuid),
                    "version": 2,
                    "snapshot": {
                        "title": "Task Alpha Modified",
                        "status": "in_progress",
                        "priority": "high",
                    },
                    "updated_at": datetime.now(timezone.utc),
                },
            ]
        )

        # 3. Делаем GET-запрос к новому эндпоинту полной истории
        url = f"/history/record/{record_uuid}/"
        response = await client.get(url)

        # 4. Проверяем валидность ответа
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == "success"
        assert data["record_uuid"] == str(record_uuid)

        history = data["history"]
        assert len(history) == 2

        # Проверяем сортировку DESC (наверху самая последняя версия)
        assert history[0]["version"] == 2
        assert history[0]["user_uuid"] == str(current_user.uuid)
        assert history[0]["snapshot"]["title"] == "Task Alpha Modified"
        assert history[0]["snapshot"]["status"] == "in_progress"

        # Первая версия
        assert history[1]["version"] == 1
        assert history[1]["user_uuid"] == str(user_modifier)
        assert history[1]["snapshot"]["title"] == "Task Alpha"
        assert history[1]["snapshot"]["priority"] == "low"
