# mongo/tests/test_history.py

import pytest
from uuid import uuid4
from mongo.history import HistoryRepository


@pytest.mark.asyncio
class TestHistoryRepository:

    async def test_log_change_and_get_history_success(self, test_client):
        """
        Проверяет успешное логирование изменений записи.
        Убеждаемся, что все поля (включая автора user_uuid) сохраняются,
        а метод получения истории сортирует данные по версии DESC.
        """
        # 1. Получаем базу данных Mongo из переопределений или фикстуры.
        # Поскольку в conftest.py база настраивается внутри test_client,
        # мы можем вытащить её прямо из dependency_overrides, либо использовать напрямую.
        from main import app
        from mongo.db import get_mongo_db

        # Получаем тестовый инстанс Mongo, который завязал conftest
        db = await anext(app.dependency_overrides[get_mongo_db]())

        repository = HistoryRepository(db)

        # Данные для теста
        instance_id = str(uuid4())
        record_id = str(uuid4())
        user_id = str(uuid4())  # КТО делает действие

        # Снапшоты разных версий документа
        snapshot_v1 = {"title": "First Version", "status": "draft"}
        snapshot_v2 = {"title": "Second Version", "status": "active"}

        # 2. Логируем версию 1
        doc_v1 = await repository.log_change(
            instance_uuid=instance_id,
            record_uuid=record_id,
            user_uuid=user_id,
            version=1,
            snapshot=snapshot_v1,
        )

        # Проверяем, что в возвращенном документе есть всё необходимое
        assert doc_v1["instance_uuid"] == instance_id
        assert doc_v1["record_uuid"] == record_id
        assert doc_v1["user_uuid"] == user_id  # Главная проверка: автор записан!
        assert doc_v1["version"] == 1
        assert doc_v1["snapshot"] == snapshot_v1
        assert "updated_at" in doc_v1 or "created_at" in doc_v1 or "_id" in doc_v1

        # 3. Логируем версию 2 (тот же юзер или другой — неважно)
        await repository.log_change(
            instance_uuid=instance_id,
            record_uuid=record_id,
            user_uuid=user_id,
            version=2,
            snapshot=snapshot_v2,
        )

        # 4. Выгребаем историю для этой записи
        history = await repository.get_record_history(
            instance_uuid=instance_id, record_uuid=record_id
        )

        # Должно быть 2 записи в истории
        assert len(history) == 2

        # Проверяем сортировку по версии DESC (сначала свежие)
        assert history[0]["version"] == 2
        assert history[0]["snapshot"]["title"] == "Second Version"
        assert history[0]["user_uuid"] == user_id

        assert history[1]["version"] == 1
        assert history[1]["snapshot"]["title"] == "First Version"

    async def test_get_snapshot_by_version(self, test_client):
        """
        Проверяет точечное получение конкретного снапшота по номеру версии (для Rollback).
        """
        from main import app
        from mongo.db import get_mongo_db

        db = await anext(app.dependency_overrides[get_mongo_db]())
        repository = HistoryRepository(db)

        instance_id = str(uuid4())
        record_id = str(uuid4())
        user_id = str(uuid4())

        # Логируем пару версий
        await repository.log_change(instance_id, record_id, user_id, 1, {"step": 1})
        await repository.log_change(instance_id, record_id, user_id, 2, {"step": 2})
        await repository.log_change(instance_id, record_id, user_id, 3, {"step": 3})

        # Запрашиваем конкретно версию 2
        target_snapshot_doc = await repository.get_snapshot_by_version(
            instance_uuid=instance_id, record_uuid=record_id, version=2
        )

        assert target_snapshot_doc is not None
        assert target_snapshot_doc["version"] == 2
        assert target_snapshot_doc["snapshot"] == {"step": 2}
        assert target_snapshot_doc["user_uuid"] == user_id

    async def test_history_isolation_by_instance(self, test_client):
        """
        Проверяет, что история изменений изолирована между разными инстансами.
        Запрос истории в Инстансе А не должен видеть записи из Инстанса Б,
        даже если record_uuid совпали.
        """
        from main import app
        from mongo.db import get_mongo_db

        db = await anext(app.dependency_overrides[get_mongo_db]())
        repository = HistoryRepository(db)

        instance_a = str(uuid4())
        instance_b = str(uuid4())
        shared_record_id = str(uuid4())  # Одинаковый ID записи чисто гипотетически
        user_id = str(uuid4())

        # Сохраняем историю в инстансе А
        await repository.log_change(
            instance_a, shared_record_id, user_id, 1, {"data": "A"}
        )
        # Сохраняем историю в инстансе Б
        await repository.log_change(
            instance_b, shared_record_id, user_id, 1, {"data": "B"}
        )

        # Запрашиваем историю для инстанса А
        history_a = await repository.get_record_history(instance_a, shared_record_id)
        assert len(history_a) == 1
        assert history_a[0]["snapshot"]["data"] == "A"
        assert history_a[0]["instance_uuid"] == instance_a

        # Запрашиваем для Б
        history_b = await repository.get_record_history(instance_b, shared_record_id)
        assert len(history_b) == 1
        assert history_b[0]["snapshot"]["data"] == "B"
        assert history_b[0]["instance_uuid"] == instance_b

    async def test_get_field_history_success(self, test_client):
        """
        Проверяет успешное извлечение истории изменений конкретного поля.
        Убеждаемся, что возвращаются только значения целевого поля,
        а версии без изменений или без этого поля не ломают логику.
        """
        from main import app
        from mongo.db import get_mongo_db

        db = await anext(app.dependency_overrides[get_mongo_db]())
        repository = HistoryRepository(db)

        instance_id = str(uuid4())
        record_id = str(uuid4())

        # Разные пользователи делают разные изменения
        user_v1 = str(uuid4())
        user_v2 = str(uuid4())
        user_v3 = str(uuid4())

        # Шаг 1: Создание записи (v1) -> статус 'draft'
        await repository.log_change(
            instance_uuid=instance_id,
            record_uuid=record_id,
            user_uuid=user_v1,
            version=1,
            snapshot={"title": "Task 1", "status": "draft", "priority": "low"},
        )

        # Шаг 2: Обновление записи (v2) -> статус меняется на 'in_progress'
        await repository.log_change(
            instance_uuid=instance_id,
            record_uuid=record_id,
            user_uuid=user_v2,
            version=2,
            snapshot={"title": "Task 1", "status": "in_progress", "priority": "low"},
        )

        # Шаг 3: Обновление другого поля (v3) -> статус остался прежним, меняется priority
        await repository.log_change(
            instance_uuid=instance_id,
            record_uuid=record_id,
            user_uuid=user_v3,
            version=3,
            snapshot={"title": "Task 1", "status": "in_progress", "priority": "high"},
        )

        # Вызываем наш новый метод: ХОТИМ ПОСМОТРЕТЬ ТОЛЬКО ИСТОРИЮ ИЗМЕНЕНИЯ ПОЛЯ "status"
        status_history = await repository.get_field_history(
            instance_uuid=instance_id, record_uuid=record_id, field_name="status"
        )

        # Должно вернуться 3 записи (так как во всех трех снапшотах поле status присутствовало)
        assert len(status_history) == 3

        # Проверяем сортировку DESC (от свежих к старым) и правильность авторов действий
        # Последний снапшот (v3)
        assert status_history[0]["version"] == 3
        assert status_history[0]["value"] == "in_progress"
        assert status_history[0]["user_uuid"] == user_v3

        # Средний снапшот (v2)
        assert status_history[1]["version"] == 2
        assert status_history[1]["value"] == "in_progress"
        assert status_history[1]["user_uuid"] == user_v2

        # Самый первый снапшот (v1)
        assert status_history[2]["version"] == 1
        assert status_history[2]["value"] == "draft"
        assert status_history[2]["user_uuid"] == user_v1

        # ДОПОЛНИТЕЛЬНО: Проверим поле, которого не было в помине, список должен быть пустым
        ghost_history = await repository.get_field_history(
            instance_uuid=instance_id,
            record_uuid=record_id,
            field_name="non_existent_field",
        )
        assert len(ghost_history) == 0
