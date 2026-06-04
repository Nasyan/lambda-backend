# core/tests/test_template.py

import asyncio

import pytest
from uuid import uuid4


@pytest.mark.asyncio
async def test_update_template_metadata_success(test_client, create_test_environment):
    """
    Позитивный сценарий: Успешное изменение имени таблицы (метаданных).
    """
    user_uuid, instance_uuid, headers = await create_test_environment()

    # 1. Создаем шаблон
    base_payload = {
        "name": "Старое имя",
        "schema": {"field": {"type": "string", "required": True}},
    }
    create_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates", json=base_payload, headers=headers
    )
    template_uuid = create_resp.json()["_id"]

    # 2. Обновляем имя
    update_payload = {"name": "Новое имя таблицы"}
    response = await test_client.patch(
        f"/instances/{instance_uuid}/templates/{template_uuid}",
        json=update_payload,
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Новое имя таблицы"
    assert data["updated_by"] == user_uuid


@pytest.mark.asyncio
async def test_delete_template_success(test_client, create_test_environment):
    """
    Позитивный сценарий: Успешное удаление существующего шаблона.
    Ожидаем статус 204 No Content.
    """
    user_uuid, instance_uuid, headers = await create_test_environment()

    # 1. Сначала создаем шаблон, который будем удалять
    payload = {
        "name": "Таблица для удаления",
        "schema": {"field": {"type": "string", "required": True}},
    }
    create_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates", json=payload, headers=headers
    )
    template_uuid = create_resp.json()["_id"]

    # 2. Вызываем эндпоинт удаления
    delete_resp = await test_client.delete(
        f"/instances/{instance_uuid}/templates/{template_uuid}", headers=headers
    )

    assert delete_resp.status_code == 204
    assert not delete_resp.content  # Тело ответа должно быть пустым


@pytest.mark.asyncio
async def test_delete_template_not_found(test_client, create_test_environment):
    """
    Негативный сценарий: Попытка удалить несуществующий шаблон.
    """
    _, instance_uuid, headers = await create_test_environment()
    fake_template_uuid = str(uuid4())

    response = await test_client.delete(
        f"/instances/{instance_uuid}/templates/{fake_template_uuid}", headers=headers
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_add_column_success(test_client, create_test_environment):
    """
    Позитивный сценарий: Добавление новой валидной колонки в схему.
    """
    user_uuid, instance_uuid, headers = await create_test_environment()

    # 1. Создаем базовый шаблон
    base_payload = {
        "name": "Склады",
        "schema": {"title": {"type": "string", "required": True}},
    }
    create_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates", json=base_payload, headers=headers
    )
    template_uuid = create_resp.json()["_id"]

    # 2. Добавляем новую колонку
    column_payload = {
        "column_name": "capacity",
        "field_meta": {"type": "number", "required": False},
    }
    response = await test_client.post(
        f"/instances/{instance_uuid}/templates/{template_uuid}/columns",
        json=column_payload,
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()

    # Проверяем, что старая колонка осталась, а новая успешно вмержилась в схему
    assert data["schema"]["title"]["type"] == "string"
    assert data["schema"]["capacity"]["type"] == "number"
    assert data["schema"]["capacity"]["required"] is False
    assert data["updated_by"] == user_uuid


@pytest.mark.asyncio
async def test_add_column_invalid_meta(test_client, create_test_environment):
    """
    Негативный сценарий: Валидация validate_schema_definition падает,
    если передан неподдерживаемый тип данных.
    """
    _, instance_uuid, headers = await create_test_environment()

    base_payload = {
        "name": "Склады",
        "schema": {"title": {"type": "string", "required": True}},
    }
    create_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates", json=base_payload, headers=headers
    )
    template_uuid = create_resp.json()["_id"]

    # Шлем невалидный тип 'invalid_type_name'
    bad_column_payload = {
        "column_name": "metadata",
        "field_meta": {"type": "invalid_type_name", "required": False},
    }
    response = await test_client.post(
        f"/instances/{instance_uuid}/templates/{template_uuid}/columns",
        json=bad_column_payload,
        headers=headers,
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_update_column_meta_success(test_client, create_test_environment):
    """
    Позитивный сценарий: Изменение метаданных существующей колонки.
    """
    user_uuid, instance_uuid, headers = await create_test_environment()

    # 1. Создаем шаблон с необязательным текстовым полем "description"
    base_payload = {
        "name": "Задачи",
        "schema": {"description": {"type": "string", "required": False}},
    }
    create_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates", json=base_payload, headers=headers
    )
    template_uuid = create_resp.json()["_id"]

    # 2. Перебиваем метаданные: делаем поле "description" обязательным
    update_payload = {
        "column_name": "description",
        "field_meta": {"type": "string", "required": True},
    }
    response = await test_client.patch(
        f"/instances/{instance_uuid}/templates/{template_uuid}/columns",
        json=update_payload,
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["schema"]["description"]["required"] is True  # Флаг изменился
    assert data["updated_by"] == user_uuid


@pytest.mark.asyncio
async def test_drop_column_success(test_client, create_test_environment):
    """
    Позитивный сценарий: Полное удаление (unset) ключа колонки из схемы.
    """
    user_uuid, instance_uuid, headers = await create_test_environment()

    # 1. Создаем шаблон с двумя колонками
    base_payload = {
        "name": "Лиды",
        "schema": {
            "email": {"type": "string", "required": True},
            "temp_mark": {"type": "boolean", "required": False},
        },
    }
    create_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates", json=base_payload, headers=headers
    )
    template_uuid = create_resp.json()["_id"]

    # 2. Вырезаем колонку "temp_mark" через DELETE c параметром в URL
    response = await test_client.delete(
        f"/instances/{instance_uuid}/templates/{template_uuid}/columns/temp_mark",
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()

    assert "email" in data["schema"]
    assert "temp_mark" not in data["schema"]  # Успешный $unset
    assert data["updated_by"] == user_uuid


@pytest.mark.asyncio
async def test_column_operations_template_not_found(
    test_client, create_test_environment
):
    """
    Негативный сценарий: Попытка модифицировать колонки в несуществующем шаблоне.
    """
    _, instance_uuid, headers = await create_test_environment()
    fake_template_uuid = str(uuid4())

    column_payload = {
        "column_name": "any_column",
        "field_meta": {"type": "string", "required": False},
    }

    response = await test_client.post(
        f"/instances/{instance_uuid}/templates/{fake_template_uuid}/columns",
        json=column_payload,
        headers=headers,
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_update_column_meta_fail_due_to_invalid_existing_records(
    test_client, create_test_environment
):
    """
    Негативный сценарий: Попытка изменить тип колонки с 'string' на 'number',
    когда в таблице уже лежат текстовые записи. Ожидаем 400 Bad Request.
    """
    user_uuid, instance_uuid, headers = await create_test_environment()

    # 1. Создаем шаблон с текстовым полем "age_group"
    base_payload = {
        "name": "Пользователи",
        "schema": {"age_group": {"type": "string", "required": False}},
    }
    create_template_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates", json=base_payload, headers=headers
    )
    template_uuid = create_template_resp.json()["_id"]

    # 2. Создаем запись, которая содержит строку в поле "age_group"
    record_payload = {
        "data": {"age_group": "young_adult"}  # Это строка, она не пролезет в number
    }
    create_record_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
        json=record_payload,
        headers=headers,
    )
    assert create_record_resp.status_code == 201  # Запись успешно создалась

    # 3. Пытаемся деструктивно изменить схему: меняем тип 'string' -> 'number'
    update_schema_payload = {
        "column_name": "age_group",
        "field_meta": {"type": "number", "required": False},
    }
    response = await test_client.patch(
        f"/instances/{instance_uuid}/templates/{template_uuid}/columns",
        json=update_schema_payload,
        headers=headers,
    )

    # Валидатор на курсоре должен поймать строку "young_adult" и заблокировать мутацию
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_update_column_meta_success_with_valid_existing_records(
    test_client, create_test_environment
):
    """
    Позитивный сценарий: Изменение метаданных колонки (например, добавление описания
    или переключение флага required, если данные не противоречат), при наличии записей.
    """
    user_uuid, instance_uuid, headers = await create_test_environment()

    # 1. Создаем шаблон с необязательным полем "score"
    base_payload = {
        "name": "Рейтинги",
        "schema": {"score": {"type": "number", "required": False}},
    }
    create_template_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates", json=base_payload, headers=headers
    )
    template_uuid = create_template_resp.json()["_id"]

    # 2. Создаем запись, где "score" заполнено валидным числом
    record_payload = {"data": {"score": 42}}
    create_record_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
        json=record_payload,
        headers=headers,
    )
    assert create_record_resp.status_code == 201

    # 3. Делаем поле "score" обязательным (required=True)
    # Поскольку у нас уже есть запись и в ней это поле заполнено (не None),
    # проверка должна пройти успешно.
    update_schema_payload = {
        "column_name": "score",
        "field_meta": {"type": "number", "required": True},
    }
    response = await test_client.patch(
        f"/instances/{instance_uuid}/templates/{template_uuid}/columns",
        json=update_schema_payload,
        headers=headers,
    )

    assert response.status_code == 200
    data = response.json()
    assert data["schema"]["score"]["required"] is True
    assert data["updated_by"] == user_uuid


class TestFormulaIntegration:

    @pytest.mark.asyncio
    async def test_create_record_with_calculated_formula(
        self, test_client, create_test_environment
    ):
        user_uuid, instance_uuid, headers = await create_test_environment()

        formula_ast = {
            "type": "binary_op",
            "operator": "subtract",
            "left": {"type": "field", "value": "price"},
            "right": {"type": "field", "value": "discount"},
        }

        template_payload = {
            "name": "Заказы",
            "schema": {
                "price": {"type": "number", "required": True},
                "discount": {"type": "number", "required": False},
                "total": {"type": "formula", "required": False, "ast": formula_ast},
            },
        }

        template_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=template_payload,
            headers=headers,
        )

        # Дебаг-принт
        if template_resp.status_code != 201:
            print(f"\n[DEBUG] Template creation failed: {template_resp.json()}")
        assert template_resp.status_code == 201
        template_uuid = template_resp.json()["_id"]

        record_payload = {"data": {"price": 1000, "discount": 150}}

        record_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json=record_payload,
            headers=headers,
        )

        # Дебаг-принт
        if record_resp.status_code != 201:
            print(f"\n[DEBUG] Record creation failed: {record_resp.json()}")
        assert record_resp.status_code == 201

        record_data = record_resp.json()["data"]
        assert record_data["total"] == 850.0

    @pytest.mark.asyncio
    async def test_update_record_formula_on_partial_data_change(
        self, test_client, create_test_environment
    ):
        user_uuid, instance_uuid, headers = await create_test_environment()

        formula_ast = {
            "type": "binary_op",
            "operator": "subtract",
            "left": {"type": "field", "value": "price"},
            "right": {"type": "field", "value": "discount"},
        }

        template_payload = {
            "name": "Инвойсы",
            "schema": {
                "price": {"type": "number", "required": True},
                "discount": {"type": "number", "required": True},
                "total": {"type": "formula", "required": False, "ast": formula_ast},
            },
        }
        template_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=template_payload,
            headers=headers,
        )
        template_uuid = template_resp.json()["_id"]

        create_record_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json={"data": {"price": 500, "discount": 50}},
            headers=headers,
        )

        # Дебаг-принт
        if create_record_resp.status_code != 201:
            print(f"\n[DEBUG] Setup record failed: {create_record_resp.json()}")

        record_uuid = create_record_resp.json()["_id"]

        update_resp = await test_client.patch(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes/{record_uuid}",
            json={"data": {"discount": 100, "price": 500}},
            headers=headers,
        )

        # Дебаг-принт
        if update_resp.status_code != 200:
            print(f"\n[DEBUG] Patch failed: {update_resp.json()}")

        assert update_resp.status_code == 200
        updated_data = update_resp.json()["data"]
        assert updated_data["total"] == 400.0

    @pytest.mark.asyncio
    async def test_add_column_with_invalid_ast_structure(
        self, test_client, create_test_environment
    ):
        """
        Негативный сценарий: Попытка добавить новую колонку-формулу с поломанным/невалидным AST.
        Ожидаем 422 или 400 (в зависимости от того, где отработает валидатор схемы).
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        # 1. Создаем пустой шаблон
        template_payload = {"name": "Склад", "schema": {}}
        template_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=template_payload,
            headers=headers,
        )
        template_uuid = template_resp.json()["_id"]

        # 2. Пытаемся добавить колонку-формулу, но умышленно совершаем ошибку в структуре AST
        # (Передаем невалидное имя оператора, которого нет в Pydantic Literal)
        invalid_column_payload = {
            "column_name": "margin",
            "field_meta": {
                "type": "formula",
                "ast": {
                    "type": "binary_op",
                    "operator": "INVALID_BURNING_OPERATOR",  # 💥 Ошибка тут
                    "left": {"type": "literal", "value": 10},
                    "right": {"type": "literal", "value": 5},
                },
            },
        }

        response = await test_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/columns",
            json=invalid_column_payload,
            headers=headers,
        )

        # Валидатор схемы (validate_schema_definition) должен отклонить этот запрос
        assert response.status_code in [400, 422]

    @pytest.mark.asyncio
    async def test_formula_chain_dependency(self, test_client, create_test_environment):
        user_uuid, instance_uuid, headers = await create_test_environment()

        template_payload = {
            "name": "Сложные расчеты",
            "schema": {
                "price": {"type": "number", "required": True},
                "discount": {"type": "number", "required": True},
                "total": {
                    "type": "formula",
                    "required": False,
                    "ast": {
                        "type": "binary_op",
                        "operator": "subtract",
                        "left": {"type": "field", "value": "price"},
                        "right": {"type": "field", "value": "discount"},
                    },
                },
                "final": {
                    "type": "formula",
                    "required": False,
                    "ast": {
                        "type": "binary_op",
                        "operator": "multiply",
                        "left": {"type": "field", "value": "total"},
                        "right": {"type": "literal", "value": 0.9},
                    },
                },
            },
        }

        template_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=template_payload,
            headers=headers,
        )
        template_uuid = template_resp.json()["_id"]

        resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json={"data": {"price": 1000, "discount": 100}},
            headers=headers,
        )

        assert resp.status_code == 201
        data = resp.json()["data"]
        assert data["total"] == 900.0
        assert data["final"] == 810.0

    @pytest.mark.asyncio
    async def test_add_column_with_invalid_ast_structure_clone(
        self, test_client, create_test_environment
    ):
        user_uuid, instance_uuid, headers = await create_test_environment()

        template_payload = {"name": "Склад", "schema": {}}
        template_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=template_payload,
            headers=headers,
        )
        template_uuid = template_resp.json()["_id"]

        invalid_column_payload = {
            "column_name": "margin",
            "field_meta": {
                "type": "formula",
                "ast": {
                    "type": "binary_op",
                    "operator": "INVALID_BURNING_OPERATOR",
                    "left": {"type": "literal", "value": 10},
                    "right": {"type": "literal", "value": 5},
                },
            },
        }

        response = await test_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/columns",
            json=invalid_column_payload,
            headers=headers,
        )

        assert response.status_code in [400, 422]

    @pytest.mark.asyncio
    async def test_formula_lifecycle_update_and_schema_change(
        self, test_client, create_test_environment
    ):
        user_uuid, instance_uuid, headers = await create_test_environment()

        # 1. Создаем шаблон
        formula_ast = {
            "type": "binary_op",
            "operator": "subtract",
            "left": {"type": "field", "value": "price"},
            "right": {"type": "field", "value": "discount"},
        }

        template_payload = {
            "name": "Тест жизненного цикла",
            "schema": {
                "price": {"type": "number", "required": True},
                "discount": {"type": "number", "required": True},
                "total": {"type": "formula", "required": False, "ast": formula_ast},
            },
        }

        template_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=template_payload,
            headers=headers,
        )
        assert template_resp.status_code == 201
        template_uuid = template_resp.json()["_id"]

        # 2. Создаем запись
        record_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json={"data": {"price": 1000, "discount": 200}},
            headers=headers,
        )
        assert record_resp.status_code == 201
        record_uuid = record_resp.json()["_id"]
        assert record_resp.json()["data"]["total"] == 800.0

        # 3. Обновляем значение (PATCH) - проверяем пересчет
        update_resp = await test_client.patch(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes/{record_uuid}",
            json={
                "data": {"price": 1000, "discount": 100}
            },  # Меняем discount с 200 на 100
            headers=headers,
        )
        assert update_resp.status_code == 200
        assert (
            update_resp.json()["data"]["total"] == 900.0
        )  # Ожидаем пересчет: 1000 - 100 = 900

        # 4. Попытка сменить тип поля (например, меняем формулу на просто число)
        # В реальной системе это обычно делается через изменение структуры шаблона или колонки
        new_schema_payload = {
            "name": "Тест жизненного цикла",
            "schema": {
                "price": {"type": "number", "required": True},
                "discount": {"type": "number", "required": True},
                "total": {
                    "type": "number",
                    "required": False,
                },  # Сменили с formula на number
            },
        }

        # Обычно это PUT запрос на обновление шаблона
        schema_update_resp = await test_client.patch(
            f"/instances/{instance_uuid}/templates/{template_uuid}",
            json=new_schema_payload,
            headers=headers,
        )

        # Ожидаем 200 или 400 (если архитектура запрещает менять тип поля с формулы на число)
        assert schema_update_resp.status_code == 200


@pytest.mark.asyncio
async def test_create_template_with_ui_widget_success(
    test_client, create_test_environment
):
    """Позитивный сценарий: Успешное создание шаблона с валидным опциональным полем ui_widget."""
    user_uuid, instance_uuid, headers = await create_test_environment()

    # Создаем шаблон, где у одного поля есть ui_widget: "qr", а у другого нет
    payload = {
        "name": "Товары с маркировкой",
        "schema": {
            "title": {"type": "string", "required": True},
            "qr_code": {
                "type": "string",
                "required": False,
                "ui_widget": "qr",  # Валидный виджет из ALLOWED_UI_WIDGETS
            },
        },
    }

    response = await test_client.post(
        f"/instances/{instance_uuid}/templates", json=payload, headers=headers
    )

    assert response.status_code == 201
    data = response.json()

    # Проверяем, что схема сохранилась корректно со всеми метаданными
    assert data["schema"]["title"]["type"] == "string"
    assert data["schema"]["qr_code"]["type"] == "string"
    assert data["schema"]["qr_code"]["ui_widget"] == "qr"
    assert data["created_by"] == user_uuid


@pytest.mark.asyncio
async def test_create_template_with_invalid_ui_widget(
    test_client, create_test_environment
):
    """Негативный сценарий: Валидация падает, если при создании шаблона

    передан несуществующий ui_widget.
    """
    _, instance_uuid, headers = await create_test_environment()

    # Передаем невалидный виджет 'invalid_mega_widget'
    bad_payload = {
        "name": "Ошибочная таблица",
        "schema": {
            "photo": {
                "type": "string",
                "required": False,
                "ui_widget": "invalid_mega_widget",
            }
        },
    }

    response = await test_client.post(
        f"/instances/{instance_uuid}/templates", json=bad_payload, headers=headers
    )

    # Ожидаем ошибку 400 из-за провала валидации схемы
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_add_column_invalid_ui_widget(test_client, create_test_environment):
    """Негативный сценарий: Валидация падает, если при добавлении новой колонки

    в существующий шаблон передан невалидный ui_widget.
    """
    _, instance_uuid, headers = await create_test_environment()

    # 1. Создаем базовый шаблон
    base_payload = {
        "name": "Профили",
        "schema": {"username": {"type": "string", "required": True}},
    }
    create_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates", json=base_payload, headers=headers
    )
    template_uuid = create_resp.json()["_id"]

    # 2. Пытаемся накатить колонку с кривым ui_widget
    bad_column_payload = {
        "column_name": "avatar",
        "field_meta": {
            "type": "string",
            "required": False,
            "ui_widget": "drop_database_widget",
        },
    }
    response = await test_client.post(
        f"/instances/{instance_uuid}/templates/{template_uuid}/columns",
        json=bad_column_payload,
        headers=headers,
    )

    assert response.status_code == 400


@pytest.mark.asyncio
async def test_get_templates_filter_and_sort_success(
    test_client, create_test_environment
):
    """
    Позитивный сценарий: Проверка сквозного контракта фильтрации и сортировки списков.
    Создаем 3 шаблона: 'Apple', 'Banana', 'Application'.
    Проверяем:
    - Поиск по подстроке (регистронезависимый).
    - Сортировку по имени (asc / desc).
    """
    user_uuid, instance_uuid, headers = await create_test_environment()

    # 1. Создаем 3 тестовых шаблона с паузой, чтобы гарантировать разный порядок (если сортировать по дате)
    # Имена подобраны специально для тестирования поиска 'app' и сортировки по алфавиту
    template_names = ["Apple", "Banana", "Application"]

    for name in template_names:
        payload = {
            "name": name,
            "schema": {"title": {"type": "string", "required": True}},
        }
        create_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates", json=payload, headers=headers
        )
        assert create_resp.status_code == 201
        # Небольшая пауза, чтобы метки времени создания отличались (полезно для дефолтной сортировки)
        await asyncio.sleep(0.01)

    # 2. ТЕСТ 1: Проверяем фильтрацию (поиск по строке "app" в нижнем регистре)
    # Должны вернуться "Apple" и "Application", но НЕ "Banana"
    filter_resp = await test_client.get(
        f"/instances/{instance_uuid}/templates?search=app", headers=headers
    )
    assert filter_resp.status_code == 200
    filter_data = filter_resp.json()

    assert len(filter_data) == 2
    returned_names = [item["name"] for item in filter_data]
    assert "Apple" in returned_names
    assert "Application" in returned_names
    assert "Banana" not in returned_names

    # 3. ТЕСТ 2: Проверяем сортировку по имени по возрастанию (name:asc)
    # Ожидаемый порядок: "Apple" -> "Application" -> "Banana"
    sort_asc_resp = await test_client.get(
        f"/instances/{instance_uuid}/templates?sort_by=name:asc", headers=headers
    )
    assert sort_asc_resp.status_code == 200
    sort_asc_data = sort_asc_resp.json()

    assert len(sort_asc_data) == 3
    assert sort_asc_data[0]["name"] == "Apple"
    assert sort_asc_data[1]["name"] == "Application"
    assert sort_asc_data[2]["name"] == "Banana"

    # 4. ТЕСТ 3: Проверяем сортировку по имени по убыванию (name:desc)
    # Ожидаемый порядок: "Banana" -> "Application" -> "Apple"
    sort_desc_resp = await test_client.get(
        f"/instances/{instance_uuid}/templates?sort_by=name:desc", headers=headers
    )
    assert sort_desc_resp.status_code == 200
    sort_desc_data = sort_desc_resp.json()

    assert len(sort_desc_data) == 3
    assert sort_desc_data[0]["name"] == "Banana"
    assert sort_desc_data[1]["name"] == "Application"
    assert sort_desc_data[2]["name"] == "Apple"

    # 5. ТЕСТ 4: Комбинированный тест (Поиск + Сортировка)
    # Ищем "app" с сортировкой по убыванию (name:desc)
    # Ожидаемый порядок: "Application" -> "Apple"
    combined_resp = await test_client.get(
        f"/instances/{instance_uuid}/templates?search=app&sort_by=name:desc",
        headers=headers,
    )
    assert combined_resp.status_code == 200
    combined_data = combined_resp.json()

    assert len(combined_data) == 2
    assert combined_data[0]["name"] == "Application"
    assert combined_data[1]["name"] == "Apple"
