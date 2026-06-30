# playground/tests/test_api.py

import pytest
from users.models import UserRole
from mongo.tools.exceptions import SchemaValidationError
from mongo.tools.validators import validate_schema_definition
from mongo.tools.schema_constants import ALLOWED_UI_WIDGETS


class TestTemplateConstraints:

    @pytest.mark.asyncio
    async def test_create_duplicate_template_name_fails(
        self, test_client, create_test_environment, template_payload_factory
    ):
        """Негативный сценарий: создание дубликата шаблона по имени в одном инстансе (409)."""
        _, instance_uuid, headers = await create_test_environment()
        url = f"/instances/{instance_uuid}/templates"

        payload = template_payload_factory(name="Пользователи")

        # 1. Создаем первый шаблон успешно
        first_resp = await test_client.post(url, json=payload, headers=headers)
        assert first_resp.status_code == 201

        # 2. Повторный запрос с тем же именем гарантированно возвращает 409 Conflict
        second_resp = await test_client.post(url, json=payload, headers=headers)
        assert second_resp.status_code == 409

    @pytest.mark.asyncio
    async def test_rename_template_with_linked_trigger_fails(
        self,
        test_client,
        create_test_environment,
        template_payload_factory,
        create_test_trigger,
    ):
        """
        Негативный сценарий: Попытка переименовать шаблон, к которому
        привязан активный автоматический триггер в PostgreSQL (внешний триггер).
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        # 1. Создаем базовый шаблон через фабрику
        payload = template_payload_factory(name="Исходное Имя Таблицы")
        create_tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=payload,
            headers=headers,
        )
        assert create_tpl_resp.status_code == 201
        template_uuid = create_tpl_resp.json()["_id"]

        # 2. Регистрируем триггер в Postgres с помощью фикстуры из conftest
        await create_test_trigger(test_client, instance_uuid, template_uuid, headers)

        # 3. Пытаемся изменить имя таблицы (name) через PATCH запрос
        update_payload = {"name": "Новое Запрещенное Имя", "schema": payload["schema"]}

        rename_resp = await test_client.patch(
            f"/instances/{instance_uuid}/templates/{template_uuid}",
            json=update_payload,
            headers=headers,
        )

        if rename_resp.status_code == 200:
            print(f"\n[DEBUG] Rename allowed for linked template: {rename_resp.json()}")

        # Сервер должен отклонить запрос из-за нарушения целостности схемы (400 или 422)
        assert rename_resp.status_code in [400, 422]

    @pytest.mark.asyncio
    async def test_rename_template_with_embedded_mongo_triggers_fails(
        self,
        test_client,
        create_test_environment,
        template_payload_factory,
        create_test_template,
    ):
        """
        Негативный сценарий: Попытка переименовать шаблон, содержащий встроенные
        (embedded) триггеры автоматизации внутри самой схемы MongoDB (кейс из дебаг-лога).
        """
        # 1. Задаем структуру встроенного триггера
        embedded_trigger = {
            "trigger_id": "80fab87f-a09d-4b5c-98f6-54605dbd5016",
            "trigger_type": "AUTOMATION",
            "event": "ON_RECORD_UPDATE",
            "target_field": "email",
        }

        # Генерируем payload со встроенным триггером
        payload = template_payload_factory(
            name="Таблица со встроенным триггером", embedded_triggers=[embedded_trigger]
        )

        # 2. Создаем шаблон в БД с помощью нашей прокачанной фикстуры
        template_uuid, _, instance_uuid, headers = await create_test_template(
            test_client, create_test_environment, custom_payload=payload
        )

        # 3. Пытаемся выполнить PATCH переименования
        update_payload = {"name": "Новое Запрещенное Имя", "schema": payload["schema"]}

        rename_resp = await test_client.patch(
            f"/instances/{instance_uuid}/templates/{template_uuid}",
            json=update_payload,
            headers=headers,
        )

        # Новая валидация в сервисе обязана поймать этот триггер и выдать ошибку
        assert rename_resp.status_code == 422


class TestTriggersPermissions:

    @pytest.mark.asyncio
    async def test_regular_user_cannot_create_trigger(
        self, test_client, create_test_environment
    ):
        """
        НЕГАТИВНЫЙ ТЕСТ: Пользователь с ролью USER пытается зарегистрировать
        автоматический триггер. Ожидаем блокировку операции (403 Forbidden).
        """
        # 1. Создаем инстанс и таблицу через CREATOR, так как USER создавать таблицы тоже не должен
        _, instance_uuid, creator_headers = await create_test_environment(
            role=UserRole.CREATOR
        )

        tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json={
                "name": "Рабочая Таблица Сделок",
                "schema": {"email": {"type": "string", "required": True}},
            },
            headers=creator_headers,
        )
        assert tpl_resp.status_code == 201
        template_uuid = tpl_resp.json()["_id"]

        # 2. Генерируем токен обычного USER внутри этого же инстанса
        _, _, user_headers = await create_test_environment(
            role=UserRole.USER, custom_instance_id=instance_uuid
        )

        # Payload попытки создания триггера
        trigger_payload = {
            "name": "Секретный хук пользователя",
            "trigger_type": "AUTOMATION",
            "source_template_uuid": template_uuid,
            "target_template_uuid": template_uuid,
            "target_field": "email",
            "event_type": "ON_RECORD_UPDATE",
            "action_name": "test_action",
            "action_params": {"required_text": "forbidden"},
            "condition_ast": {
                "type": "binary_op",
                "operator": "eq",
                "left": {"type": "field", "value": "email"},
                "right": {"type": "literal", "value": "test@example.com"},
            },
            "payload_ast": {"type": "field", "value": "email"},
        }

        # 3. Пытаемся отправить запрос от лица обычного пользователя
        response = await test_client.post(
            f"/instances/{instance_uuid}/triggers",
            json=trigger_payload,
            headers=user_headers,
        )

        # Защита обязана вернуть 403 Forbidden
        assert response.status_code == 403


class TestSchemaUiWidgetValidation:

    @pytest.mark.parametrize("widget_name", list(ALLOWED_UI_WIDGETS))
    def test_validate_schema_with_allowed_ui_widgets_success(self, widget_name):
        """
        УСПЕШНЫЙ КЕЙС: Проверяем, что все виджеты из белого списка
        ALLOWED_UI_WIDGETS успешно проходят валидацию схемы.
        """
        valid_schema = {
            "qr_code_field": {
                "type": "string",  # Предположим, что тип данных string зарегистрирован
                "required": False,
                "ui_widget": widget_name,
            }
        }

        # Если валидация успешна, исключение не вызывается
        try:
            validate_schema_definition(valid_schema)
        except SchemaValidationError as e:
            pytest.fail(
                f"Валидация упала на разрешенном ui_widget '{widget_name}': {e}"
            )

    def test_validate_schema_without_ui_widget_success(self):
        """
        УСПЕШНЫЙ КЕЙС: Поле ui_widget является необязательным.
        Схема без этого ключа должна валидироваться без ошибок.
        """
        schema_without_widget = {
            "phone_number": {
                "type": "string",
                "required": True,
            }
        }
        try:
            validate_schema_definition(schema_without_widget)
        except SchemaValidationError as e:
            pytest.fail(f"Валидация упала на схеме без ui_widget: {e}")

    @pytest.mark.parametrize(
        "invalid_widget",
        [
            "invalid_widget_name",  # Опечатка / несуществующий виджет
            "barcode",  # Логически подходящий, но отсутствующий в ALLOWED_UI_WIDGETS
            "",  # Пустая строка
            123,  # Неверный тип данных (integer)
            None,  # None значение
        ],
    )
    def test_validate_schema_with_forbidden_ui_widgets_fails(self, invalid_widget):
        """
        НЕГАТИВНЫЙ КЕЙС: Любые значения ui_widget, не входящие в белый список,
        должны приводить к ошибке SchemaValidationError с понятным описанием.
        """
        invalid_schema = {
            "scanner_field": {
                "type": "string",
                "ui_widget": invalid_widget,
            }
        }

        with pytest.raises(SchemaValidationError) as exc_info:
            validate_schema_definition(invalid_schema)

        # Проверяем, что в тексте ошибки фигурирует имя ошибочного виджета и имя колонки
        error_msg = str(exc_info.value)
        assert "Invalid ui_widget" in error_msg
        assert "scanner_field" in error_msg

    def test_validate_schema_ui_widget_is_allowed_meta_key(self):
        """
        ДОПОЛНИТЕЛЬНЫЙ КЕЙС: Проверяем, что ui_widget не триггерит ошибку
        'Unknown metadata keys', так как он добавлен в ALLOWED_META_KEYS.
        """
        schema = {"avatar": {"type": "string", "ui_widget": "file_upload"}}

        try:
            validate_schema_definition(schema)
        except SchemaValidationError as e:
            # Если ui_widget забыли добавить в ALLOWED_META_KEYS, ошибка будет содержать слова "Unknown metadata keys"
            assert "Unknown metadata keys" not in str(
                e
            ), f"ui_widget распознан как неизвестный ключ: {e}"


@pytest.mark.asyncio
async def test_add_column_with_valid_ui_widget_success(
    test_client, create_test_environment
):
    """
    Позитивный сценарий: Успешное добавление новой колонки со специальным ui_widget ('qr').
    """
    user_uuid, instance_uuid, headers = await create_test_environment()

    # 1. Создаем базовый шаблон
    base_payload = {
        "name": "Документы",
        "schema": {"title": {"type": "string", "required": True}},
    }
    create_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates", json=base_payload, headers=headers
    )
    template_uuid = create_resp.json()["_id"]

    # 2. Добавляем новую колонку с ui_widget
    column_payload = {
        "column_name": "scanner",
        "field_meta": {
            "type": "string",
            "required": False,
            "ui_widget": "qr",  # Валидный виджет из ALLOWED_UI_WIDGETS
        },
    }
    response = await test_client.post(
        f"/instances/{instance_uuid}/templates/{template_uuid}/columns",
        json=column_payload,
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()

    # Проверяем, что колонка добавилась и ui_widget сохранился в метаданных поля
    assert data["schema"]["title"]["type"] == "string"
    assert data["schema"]["scanner"]["type"] == "string"
    assert data["schema"]["scanner"]["ui_widget"] == "qr"
    assert data["updated_by"] == user_uuid


@pytest.mark.asyncio
async def test_add_column_with_invalid_ui_widget_fails(
    test_client, create_test_environment
):
    """
    Негативный сценарий: Валидация validate_schema_definition падает с кодом 400,
    если передан неизвестный ui_widget.
    """
    _, instance_uuid, headers = await create_test_environment()

    # 1. Создаем базовый шаблон
    base_payload = {
        "name": "Документы",
        "schema": {"title": {"type": "string", "required": True}},
    }
    create_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates", json=base_payload, headers=headers
    )
    template_uuid = create_resp.json()["_id"]

    # 2. Пытаемся добавить колонку с невалидным ui_widget
    bad_column_payload = {
        "column_name": "hologram_field",
        "field_meta": {
            "type": "string",
            "required": False,
            "ui_widget": "hologram_3d",  # Несуществующий виджет!
        },
    }
    response = await test_client.post(
        f"/instances/{instance_uuid}/templates/{template_uuid}/columns",
        json=bad_column_payload,
        headers=headers,
    )

    # API должно перехватить SchemaValidationError и отдать 400 Bad Request
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_update_column_with_invalid_ui_widget_fails(
    test_client, create_test_environment
):
    """
    Негативный сценарий: Попытка обновить метаданные существующей колонки,
    заменив валидный ui_widget на недопустимый. Ожидаем статус 400.
    """
    _, instance_uuid, headers = await create_test_environment()

    # 1. Создаем шаблон, где у колонки "scanner" изначально установлен валидный виджет "qr"
    base_payload = {
        "name": "Пропускной пункт",
        "schema": {
            "scanner": {
                "type": "string",
                "required": False,
                "ui_widget": "qr",  # Изначально всё хорошо
            }
        },
    }
    create_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates", json=base_payload, headers=headers
    )
    template_uuid = create_resp.json()["_id"]

    # 2. Пытаемся обновить эту же колонку, передав запрещенный ui_widget
    bad_update_payload = {
        "column_name": "scanner",
        "field_meta": {
            "type": "string",
            "required": False,
            "ui_widget": "invalid_face_id",  # Несуществующий виджет!
        },
    }
    response = await test_client.patch(
        f"/instances/{instance_uuid}/templates/{template_uuid}/columns",
        json=bad_update_payload,
        headers=headers,
    )

    # Валидатор должен отловить некорректное значение в ALLOWED_UI_WIDGETS и вернуть 400
    assert response.status_code == 400
