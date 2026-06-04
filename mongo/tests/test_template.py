# mongo/tests/test_template.py

import pytest
from mongo.template import TemplateRepository

# Импортируем наши профессиональные исключения
from mongo.exceptions.template import (
    TemplateNotFoundError,
    TemplateValidationError,
)

TEST_INSTANCE_ID = "company_a"
TEST_USER_ID = "user_admin"


@pytest.mark.asyncio
class TestTemplateRepositoryLifecycle:

    async def test_create_template_success(self, mongo_db):
        repo = TemplateRepository(mongo_db)
        valid_schema = {"age": {"type": "number", "required": True}}

        created = await repo.create_template(
            instance_uuid=TEST_INSTANCE_ID,
            name="Users Table",
            schema=valid_schema,
            user_uuid=TEST_USER_ID,
        )

        assert created["name"] == "Users Table"
        assert created["schema"]["age"]["type"] == "number"

    async def test_template_not_found_errors(self, mongo_db):
        """Проверяем, что операции над несуществующим шаблоном выбрасывают TemplateNotFoundError."""
        repo = TemplateRepository(mongo_db)

        with pytest.raises(TemplateNotFoundError) as exc_info:
            await repo.get_template_by_uuid(TEST_INSTANCE_ID, "fake-uuid")

        # Проверяем код ошибки и метаданные
        assert exc_info.value.error_code == "TEMPLATE_NOT_FOUND"
        assert exc_info.value.details["template_uuid"] == "fake-uuid"

    @pytest.mark.parametrize(
        "column_name, field_meta, expected_code",
        [
            (
                "avatar",
                {"type": "file_mock", "required": False},
                "TEMPLATE_VALIDATION_FAILED",
            ),
            ("_id", {"type": "string"}, "TEMPLATE_VALIDATION_FAILED"),
            ("123field", {"type": "string"}, "TEMPLATE_VALIDATION_FAILED"),
        ],
    )
    async def test_schema_validation_failures(
        self, mongo_db, column_name, field_meta, expected_code
    ):
        """Проверяем, что валидация схемы выбрасывает TemplateValidationError."""
        repo = TemplateRepository(mongo_db)
        created = await repo.create_template(
            TEST_INSTANCE_ID, "Table", {}, TEST_USER_ID
        )

        with pytest.raises(TemplateValidationError) as exc_info:
            await repo.add_column(
                TEST_INSTANCE_ID, created["_id"], column_name, field_meta
            )

        assert exc_info.value.error_code == expected_code
        # Дополнительно проверяем, что в details попало имя колонки
        assert column_name in str(exc_info.value)

    async def test_invalid_required_type(self, mongo_db):
        """Проверяем, что некорректный тип для 'required' выбрасывает TemplateValidationError."""
        repo = TemplateRepository(mongo_db)
        invalid_schema = {"name": {"type": "string", "required": "yes"}}

        with pytest.raises(TemplateValidationError) as exc_info:
            await repo.create_template(
                TEST_INSTANCE_ID, "Users", invalid_schema, TEST_USER_ID
            )

        assert exc_info.value.error_code == "TEMPLATE_VALIDATION_FAILED"

    async def test_delete_template_success(self, mongo_db):
        repo = TemplateRepository(mongo_db)
        created = await repo.create_template(
            TEST_INSTANCE_ID, "To Delete", {}, TEST_USER_ID
        )

        await repo.delete_template(TEST_INSTANCE_ID, created["_id"])

        with pytest.raises(TemplateNotFoundError):
            await repo.get_template_by_uuid(TEST_INSTANCE_ID, created["_id"])
