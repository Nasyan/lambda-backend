# mongo/tests/test_template.py

import pytest
from mongo.template import TemplateRepository
from mongo.exceptions.template import TemplateNotFoundError

TEST_INSTANCE_ID = "company_a"
TEST_USER_ID = "user_admin"


@pytest.mark.asyncio
class TestTemplateRepositoryLifecycle:

    async def test_create_template_success(self, mongo_db):
        """Проверяем, что глупый репозиторий успешно сохраняет структуру в базу."""
        repo = TemplateRepository(mongo_db)
        valid_schema = {"age": {"type": "number", "required": True}}

        created = await repo.create_template(
            instance_uuid=TEST_INSTANCE_ID,
            name="Users Table",
            schema=valid_schema,
            user_uuid=TEST_USER_ID,
        )

        assert created["_id"] is not None
        assert created["name"] == "Users Table"
        assert created["schema"]["age"]["type"] == "number"
        assert created["schema"]["age"]["required"] is True

    async def test_get_template_by_uuid_success(self, mongo_db):
        """Проверяем успешное получение сырого и нормализованного шаблона по UUID."""
        repo = TemplateRepository(mongo_db)
        created = await repo.create_template(
            TEST_INSTANCE_ID, "Profiles", {"bio": {"type": "string"}}, TEST_USER_ID
        )

        # Проверяем получение через get_template_by_uuid
        fetched = await repo.get_template_by_uuid(TEST_INSTANCE_ID, created["_id"])
        assert fetched["_id"] == created["_id"]
        assert fetched["name"] == "Profiles"

        # Проверяем получение через get_template (сырой документ)
        raw_fetched = await repo.get_template(TEST_INSTANCE_ID, created["_id"])
        assert raw_fetched["_id"] == created["_id"]

    async def test_template_not_found_errors(self, mongo_db):
        """Проверяем, что операции над несуществующим шаблоном выбрасывают TemplateNotFoundError."""
        repo = TemplateRepository(mongo_db)
        fake_uuid = "fake-uuid-123"

        with pytest.raises(TemplateNotFoundError) as exc_info:
            await repo.get_template_by_uuid(TEST_INSTANCE_ID, fake_uuid)

        assert exc_info.value.error_code == "TEMPLATE_NOT_FOUND"
        assert exc_info.value.details["template_uuid"] == fake_uuid

    async def test_get_all_templates_and_pagination(self, mongo_db):
        """Проверяем получение списка шаблонов конкретного инстанса."""
        repo = TemplateRepository(mongo_db)

        # Создаем два шаблона для TEST_INSTANCE_ID
        await repo.create_template(TEST_INSTANCE_ID, "Table 1", {}, TEST_USER_ID)
        await repo.create_template(TEST_INSTANCE_ID, "Table 2", {}, TEST_USER_ID)
        # Создаем один шаблон для другого инстанса (изоляция данных)
        await repo.create_template("other_company", "Other Table", {}, TEST_USER_ID)

        templates = await repo.get_all_templates(TEST_INSTANCE_ID, limit=10, offset=0)

        assert len(templates) == 2
        assert any(t["name"] == "Table 1" for t in templates)
        assert any(t["name"] == "Table 2" for t in templates)

    async def test_update_template_metadata_success(self, mongo_db):
        """Проверяем обновление метаданных (например, имени шаблона)."""
        repo = TemplateRepository(mongo_db)
        created = await repo.create_template(
            TEST_INSTANCE_ID, "Old Name", {}, TEST_USER_ID
        )

        updated = await repo.update_template_metadata(
            TEST_INSTANCE_ID, created["_id"], name="New Name", user_uuid=TEST_USER_ID
        )

        assert updated["name"] == "New Name"

        # Проверяем, что изменения применились в БД
        db_check = await repo.get_template_by_uuid(TEST_INSTANCE_ID, created["_id"])
        assert db_check["name"] == "New Name"

    async def test_add_and_drop_column_lifecycle(self, mongo_db):
        """Проверяем успешную модификацию схемы (добавление и удаление колонок)."""
        repo = TemplateRepository(mongo_db)
        created = await repo.create_template(
            TEST_INSTANCE_ID, "Dynamic Table", {}, TEST_USER_ID
        )

        # 1. Добавляем колонку
        field_meta = {"type": "string", "required": False}
        updated_add = await repo.add_column(
            TEST_INSTANCE_ID,
            created["_id"],
            column_name="status",
            field_meta=field_meta,
            user_uuid=TEST_USER_ID,
        )
        assert "status" in updated_add["schema"]
        assert updated_add["schema"]["status"]["type"] == "string"

        # 2. Удаляем колонку
        updated_drop = await repo.drop_column(
            TEST_INSTANCE_ID,
            created["_id"],
            column_name="status",
            user_uuid=TEST_USER_ID,
        )
        assert "status" not in updated_drop["schema"]

    async def test_update_column_meta_success(self, mongo_db):
        """Проверяем точечное обновление метаданных существующей колонки."""
        repo = TemplateRepository(mongo_db)
        initial_schema = {"amount": {"type": "number", "required": False}}
        created = await repo.create_template(
            TEST_INSTANCE_ID, "Finance", initial_schema, TEST_USER_ID
        )

        new_meta = {"type": "number", "required": True, "description": "Сумма сделки"}
        updated = await repo.update_column_meta(
            TEST_INSTANCE_ID,
            created["_id"],
            column_name="amount",
            new_meta=new_meta,
            user_uuid=TEST_USER_ID,
        )

        assert updated["schema"]["amount"]["required"] is True
        assert updated["schema"]["amount"].get("description") == "Сумма сделки"

    async def test_delete_template_success(self, mongo_db):
        """Проверяем успешное удаление шаблона."""
        repo = TemplateRepository(mongo_db)
        created = await repo.create_template(
            TEST_INSTANCE_ID, "To Delete", {}, TEST_USER_ID
        )

        await repo.delete_template(TEST_INSTANCE_ID, created["_id"])

        # Убеждаемся, что повторный запрос возвращает ошибку 404
        with pytest.raises(TemplateNotFoundError):
            await repo.get_template_by_uuid(TEST_INSTANCE_ID, created["_id"])
