# engine/tests/test_integrity_unit.py

import pytest
import logging
from uuid import uuid4
from engine.integrity import SchemaIntegrityValidator
from engine.exceptions.integrity import SchemaDependencyError, SchemaValidationError
from users.models import Instances  # Импортируем модель инстанса для сохранения FK
from policy.models import StorefrontPolicies

logger = logging.getLogger(__name__)


class TestSchemaIntegrityCascadeUnit:

    @pytest.mark.asyncio
    async def test_unit_prevent_delete_cascade_root_column(self, db_session):
        """
        Юнит-тест: проверяем напрямую check_field_mutation_safe.
        Смотрим, поймает ли функция использование 'attributes.Материал'
        при попытке изменить/удалить корневую колонку 'attributes'.
        """
        instance_uuid = uuid4()
        template_uuid = uuid4()
        template_name = "test_products"

        # Мокаем структуру текущей схемы в памяти
        current_schema = {"title": {"type": "string"}, "attributes": {"type": "string"}}

        # 🌟 ИСПРАВЛЕНИЕ: Создаем и сохраняем Инстанс в БД, чтобы удовлетворить ForeignKey констреинт
        db_instance = Instances(
            uuid=instance_uuid, title="Integrity Test Workspace", active=True
        )
        db_session.add(db_instance)
        await db_session.flush()  # Выталкиваем в базу, чтобы ключ стал валидным

        # Шаг 1. Создаем запись политики витрины
        policy = StorefrontPolicies(
            instance_uuid=instance_uuid,
            template_name=template_name,
            read_mask=["title", "attributes.Материал"],  # Глубокий путь через точку
            write_mask=["title"],
            read_filters={},
        )
        db_session.add(policy)
        await db_session.commit()

        with pytest.raises(SchemaDependencyError) as exc_info:
            await SchemaIntegrityValidator.check_field_mutation_safe(
                instance_uuid=instance_uuid,
                template_uuid=template_uuid,
                template_name=template_name,
                column_name="attributes",  # Пытаемся удалить корень
                current_schema=current_schema,
                db=db_session,
            )

        assert (
            "невозможно изменить или удалить поле 'attributes'"
            in str(exc_info.value).lower()
        )
        assert "невозможно изменить или удалить поле" in str(exc_info.value).lower()

    def test_unit_validate_storefront_policy_dot_notation(self):
        """
        Юнит-тест: проверяем validate_storefront_policy на правильный парсинг точек.
        """
        schema = {"device_name": {"type": "string"}, "config": {"type": "string"}}

        # 1. Этот паттерн должен пройти (config существует)
        valid_policy = {
            "read_mask": ["device_name", "config.cpu.frequency"],
            "write_mask": ["device_name"],
            "read_filters": {},
        }

        SchemaIntegrityValidator.validate_storefront_policy(schema, valid_policy)

        # 2. Этот паттерн должен упасть (wrong_root не существует в схеме)
        invalid_policy = {
            "read_mask": ["device_name", "wrong_root.ram_size"],
            "write_mask": ["device_name"],
            "read_filters": {},
        }

        # Меняем ожидаемое исключение на SchemaValidationError
        with pytest.raises(SchemaValidationError) as exc_info:
            SchemaIntegrityValidator.validate_storefront_policy(schema, invalid_policy)

        exception = exc_info.value

        # Проверяем человекочитаемый текст сообщения
        assert "ошибка конфигурации read_mask" in exception.message.lower()

        # Проверяем строгое соответствие контекста и невалидных полей в details
        assert exception.details["context"] == "read_mask"
        assert "wrong_root.ram_size" in exception.details["invalid_fields"]
        assert (
            exception.details["reason"] == "Несуществующие поля в маске чтения витрины"
        )

    def test_unit_extract_used_fields_with_new_ast_nodes(self):
        """
        Юнит-тест: проверяем extract_used_fields на обход новых AST узлов
        (condition, array_reduce, logical_op).
        """
        complex_ast = {
            "type": "condition",
            "condition": {
                "type": "logical_op",
                "left": {"type": "field", "value": "status"},
                "right": {"type": "field", "value": "attributes.Active"},
            },
            "true_expr": {
                "type": "array_reduce",
                "array_field": "orders",
                "filter_expression": {
                    "type": "binary_op",
                    "left": {"type": "field", "value": "item.price"},
                    "right": {"type": "field", "value": "global_limit"},
                },
            },
            "false_expr": {"type": "field", "value": "fallback_column"},
        }

        used_fields = SchemaIntegrityValidator.extract_used_fields(complex_ast)

        assert "status" in used_fields
        assert "attributes.Active" in used_fields
        assert "orders" in used_fields
        assert "item.price" in used_fields
        assert "global_limit" in used_fields
        assert "fallback_column" in used_fields
