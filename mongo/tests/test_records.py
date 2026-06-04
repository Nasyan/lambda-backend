# mongo/tests/test_records.py

import pytest
from mongo.template import TemplateRepository
from mongo.record import RecordRepository
from mongo.history import HistoryRepository

TEST_INSTANCE_ID = "company_a"
TEST_USER_ID = "user_manager"

# Базовая схема, которую мы будем прогонять через реальный репозиторий шаблонов
BASE_TEMPLATE_SCHEMA = {
    "name": {"type": "string", "required": True},
    "age": {"type": "number", "required": False},
    "is_active": {"type": "boolean", "required": True},
}


@pytest.mark.asyncio
async def test_create_record_missing_required_field(mongo_db):
    record_repo = RecordRepository(mongo_db)
    template = await create_test_template(mongo_db)

    # Пропустили обязательное поле 'is_active'
    invalid_data = {"name": "Arseniy", "age": 25}

    # 1. Отлавливаем базовый Exception, изолируя тест от изменений в импортах
    with pytest.raises(Exception) as exc_info:
        await record_repo.create_record(
            instance_uuid=TEST_INSTANCE_ID,
            template_uuid=template["_id"],
            data=invalid_data,
            schema=template["schema"],
            user_uuid=TEST_USER_ID,
        )

    # 2. Наглядно проверяем, что это наша доменная ошибка валидации
    error = exc_info.value

    assert "is_active" in str(error)
    # Если у ошибки есть словарь details, проверяем и его, безопасно через getattr
    error_details = getattr(error, "details", {})
    assert error_details.get("reason") == "schema_validation_error"


@pytest.mark.asyncio
async def test_create_record_invalid_type(mongo_db):
    record_repo = RecordRepository(mongo_db)
    template = await create_test_template(mongo_db)

    # 'age' в шаблоне имеет тип 'number', а мы передаем строку
    invalid_data = {"name": "Arseniy", "age": "twenty-five", "is_active": True}

    with pytest.raises(Exception) as exc_info:
        await record_repo.create_record(
            instance_uuid=TEST_INSTANCE_ID,
            template_uuid=template["_id"],
            data=invalid_data,
            schema=template["schema"],
            user_uuid=TEST_USER_ID,
        )

    error = exc_info.value

    # Проверяем, что в тексте ошибки фигурируют виновное поле 'age' и ожидаемый тип
    assert "age" in str(error)
    assert "number" in str(error).lower()

    error_details = getattr(error, "details", {})
    assert error_details.get("reason") == "schema_validation_error"


@pytest.mark.asyncio
async def test_get_record_multi_tenancy_security(mongo_db):
    record_repo = RecordRepository(mongo_db)
    template = await create_test_template(mongo_db)

    valid_data = {"name": "John", "is_active": False}

    # Создаем запись в рамках компании А (TEST_INSTANCE_ID)
    created_record = await record_repo.create_record(
        instance_uuid=TEST_INSTANCE_ID,
        template_uuid=template["_id"],
        data=valid_data,
        schema=template["schema"],
        user_uuid=TEST_USER_ID,
    )

    # Пытаемся прочитать эту же запись, но запрашиваем из-под инстанса "company_b"
    with pytest.raises(Exception) as exc_info:
        await record_repo.get_record_by_uuid(
            instance_uuid="company_b", record_uuid=created_record["_id"]
        )

    error = exc_info.value

    # Проверяем наглядный текст, который мы заложили в репозитории
    assert "не существует в пространстве" in str(error)

    # Безопасно вытаскиваем детали multi-tenancy защиты
    error_details = getattr(error, "details", {})
    assert error_details.get("instance_uuid") == "company_b"
    assert error_details.get("record_uuid") == created_record["_id"]


async def create_test_template(mongo_db, name: str = "Users") -> dict:
    """Хелпер для быстрого создания реального шаблона в тестах."""
    template_repo = TemplateRepository(mongo_db)
    return await template_repo.create_template(
        instance_uuid=TEST_INSTANCE_ID,
        name=name,
        schema=BASE_TEMPLATE_SCHEMA,
        user_uuid=TEST_USER_ID,
    )


@pytest.mark.asyncio
async def test_create_record_success(mongo_db):
    record_repo = RecordRepository(mongo_db)

    # 1. Создаем честный шаблон через TemplateRepository
    template = await create_test_template(mongo_db)
    template_uuid = template["_id"]
    schema = template["schema"]

    # 2. Передаем реальные данные схемы в репозиторий записей
    valid_data = {"name": "Arseniy", "age": 25, "is_active": True}
    record = await record_repo.create_record(
        instance_uuid=TEST_INSTANCE_ID,
        template_uuid=template_uuid,
        data=valid_data,
        schema=schema,
        user_uuid=TEST_USER_ID,
    )

    assert record["_id"] is not None
    assert record["template_uuid"] == template_uuid
    assert record["data"]["name"] == "Arseniy"
    assert record["data"]["age"] == 25
    assert record["instance_uuid"] == TEST_INSTANCE_ID


@pytest.mark.asyncio
async def test_get_records_with_filter_success(mongo_db):
    record_repo = RecordRepository(mongo_db)
    template = await create_test_template(mongo_db)
    t_id = template["_id"]
    schema = template["schema"]

    # Создаем двух пользователей с разным статусом активности
    await record_repo.create_record(
        TEST_INSTANCE_ID,
        t_id,
        {"name": "Alex", "is_active": True},
        schema,
        TEST_USER_ID,
    )
    await record_repo.create_record(
        TEST_INSTANCE_ID,
        t_id,
        {"name": "Ivan", "is_active": False},
        schema,
        TEST_USER_ID,
    )

    # Запрашиваем только активных (Исправлено: распаковываем кортеж результатов и total)
    active_records, total_count = await record_repo.get_records(
        instance_uuid=TEST_INSTANCE_ID, template_uuid=t_id, filters={"is_active": True}
    )

    # Проверяем метаданные количества
    assert total_count == 1

    # Проверяем сами записи
    assert len(active_records) == 1
    assert active_records[0]["data"]["name"] == "Alex"


@pytest.mark.asyncio
async def test_get_records_sorting_asc_desc(mongo_db):
    record_repo = RecordRepository(mongo_db)
    template = await create_test_template(mongo_db)
    t_id = template["_id"]
    schema = template["schema"]

    # Создаем записи с разным возрастом
    await record_repo.create_record(
        TEST_INSTANCE_ID,
        t_id,
        {"name": "John", "age": 20, "is_active": True},
        schema,
        TEST_USER_ID,
    )
    await record_repo.create_record(
        TEST_INSTANCE_ID,
        t_id,
        {"name": "Jane", "age": 30, "is_active": True},
        schema,
        TEST_USER_ID,
    )

    # Сортируем по возрасту по возрастанию (ASC) (Исправлено: распаковываем кортеж)
    records_asc, total_asc = await record_repo.get_records(
        instance_uuid=TEST_INSTANCE_ID,
        template_uuid=t_id,
        sort_by="age",
        sort_descending=False,
    )
    assert total_asc == 2
    assert len(records_asc) == 2
    assert records_asc[0]["data"]["name"] == "John"
    assert records_asc[1]["data"]["name"] == "Jane"

    # Сортируем по возрасту по убыванию (DESC) (Исправлено: распаковываем кортеж)
    records_desc, total_desc = await record_repo.get_records(
        instance_uuid=TEST_INSTANCE_ID,
        template_uuid=t_id,
        sort_by="age",
        sort_descending=True,
    )
    assert total_desc == 2
    assert len(records_desc) == 2
    assert records_desc[0]["data"]["name"] == "Jane"
    assert records_desc[1]["data"]["name"] == "John"


@pytest.mark.asyncio
async def test_record_history_logging_and_retrieval(mongo_db):
    record_repo = RecordRepository(mongo_db)
    history_repo = HistoryRepository(mongo_db)
    template = await create_test_template(mongo_db)

    t_id = template["_id"]
    schema = template["schema"]

    # 1. Создаем изначальную запись (Версия 1)
    initial_data = {"name": "Arseniy", "age": 25, "is_active": True}
    record = await record_repo.create_record(
        instance_uuid=TEST_INSTANCE_ID,
        template_uuid=t_id,
        data=initial_data,
        schema=schema,
        user_uuid=TEST_USER_ID,
    )

    record_uuid = record["_id"]
    # Имитируем структуру документа, где по умолчанию версия равна 1
    # (Если в твоем билдере записей поле называется иначе, поправь ключ)
    current_version = record.get("version", 1)

    # 2. Перед обновлением записи логируем её текущее (V1) состояние в историю
    await history_repo.log_change(
        instance_uuid=TEST_INSTANCE_ID,
        record_uuid=record_uuid,
        user_uuid=TEST_USER_ID,
        version=current_version,
        snapshot=record["data"],
    )

    # 3. Обновляем операционную запись (теперь в базе лежит Версия 2)
    updated_data = {
        "name": "Arseniy",
        "age": 26,
        "is_active": True,
    }  # Стал на год старше
    await record_repo.update_record_data(
        instance_uuid=TEST_INSTANCE_ID,
        template_uuid=t_id,  # 🔥 Добавили недостающий аргумент!
        record_uuid=record_uuid,
        new_data=updated_data,
        schema=schema,
    )
    # 4. Проверяем, что в истории успешно сохранился снимок именно ПЕРВОЙ версии
    history_lines = await history_repo.get_record_history(
        instance_uuid=TEST_INSTANCE_ID, record_uuid=record_uuid
    )

    assert len(history_lines) == 1
    assert history_lines[0]["version"] == 1
    assert history_lines[0]["snapshot"]["age"] == 25  # В истории остался возраст 25
    assert history_lines[0]["record_uuid"] == record_uuid
    assert history_lines[0]["instance_uuid"] == TEST_INSTANCE_ID


@pytest.mark.asyncio
async def test_get_snapshot_by_version_and_multi_tenancy(mongo_db):
    history_repo = HistoryRepository(mongo_db)
    record_uuid = "some-record-uuid"

    # Искусственно создаем пару записей в истории для одной сущности
    await history_repo.log_change(
        TEST_INSTANCE_ID, record_uuid, TEST_USER_ID, 1, {"status": "new"}
    )
    await history_repo.log_change(
        TEST_INSTANCE_ID, record_uuid, TEST_USER_ID, 2, {"status": "in_progress"}
    )

    # 1. Проверяем точечное извлечение конкретной версии
    snap_v1 = await history_repo.get_snapshot_by_version(
        TEST_INSTANCE_ID, record_uuid, version=1
    )
    snap_v2 = await history_repo.get_snapshot_by_version(
        TEST_INSTANCE_ID, record_uuid, version=2
    )

    assert snap_v1 is not None
    assert snap_v1["snapshot"]["status"] == "new"

    assert snap_v2 is not None
    assert snap_v2["snapshot"]["status"] == "in_progress"

    # 2. Проверяем защиту Multi-tenancy: чужой инстанс не должен получить доступ к этой версии истории
    hidden_snap = await history_repo.get_snapshot_by_version(
        instance_uuid="alien_company", record_uuid=record_uuid, version=1
    )
    assert hidden_snap is None
