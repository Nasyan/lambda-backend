# core/tests/test_record.py

import pytest
from engine.schema_rules import NoCodeSchemaValidator
from engine.exceptions.integrity import SchemaValidationError
from policy.models import StorefrontPolicies


class TestSchemaIntegrityProtection:

    @pytest.mark.asyncio
    async def test_prevent_template_deletion_used_in_trigger(
        self, test_client, create_test_environment
    ):
        """
        Тест 1: Блокировка удаления таблицы, если к ней привязан триггер.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        template_payload = {
            "name": "Статусы макетов",
            "schema": {"status": {"type": "string", "required": True}},
        }
        tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=template_payload,
            headers=headers,
        )
        assert tpl_resp.status_code == 201
        template_uuid = tpl_resp.json()["_id"]

        trigger_payload = {
            "name": "Уведомление админу о статусе",
            "trigger_type": "AUTOMATION",
            "event_type": "ON_RECORD_CREATE",
            "action_name": "test_action",
            "action_params": {"required_text": "Новый макет добавлен в трекер"},
            "source_template_uuid": template_uuid,
            "target_template_uuid": template_uuid,
            "condition_ast": {
                "type": "binary_op",
                "operator": "eq",
                "left": {"type": "field", "value": "status"},
                "right": {"type": "literal", "value": "needs_review"},
            },
            "payload_ast": {"type": "field", "value": "status"},
        }
        trigger_resp = await test_client.post(
            f"/instances/{instance_uuid}/triggers",
            json=trigger_payload,
            headers=headers,
        )
        assert trigger_resp.status_code == 201

        delete_resp = await test_client.delete(
            f"/instances/{instance_uuid}/templates/{template_uuid}",
            headers=headers,
        )

        # Проверяем корректный статус-код
        assert delete_resp.status_code == 422

    @pytest.mark.asyncio
    async def test_prevent_column_drop_used_in_formula(
        self, test_client, create_test_environment
    ):
        """
        Тест 2: Блокировка удаления колонки, которая участвует в локальной формуле.
        Создаем колонки 'часы', 'ставка' и формулу 'итого'.
        Пытаемся удалить 'ставку' -> ожидаем 400.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        # 1. Создаем таблицу с формулой
        formula_ast = {
            "type": "binary_op",
            "operator": "multiply",
            "left": {"type": "field", "value": "hours_spent"},
            "right": {"type": "field", "value": "hourly_rate"},
        }
        template_payload = {
            "name": "Трудозатраты фрилансеров",
            "schema": {
                "hours_spent": {"type": "number", "required": True},
                "hourly_rate": {"type": "number", "required": True},
                "total_cost": {
                    "type": "formula",
                    "required": False,
                    "ast": formula_ast,
                },
            },
        }
        tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=template_payload,
            headers=headers,
        )
        assert tpl_resp.status_code == 201
        template_uuid = tpl_resp.json()["_id"]

        # 2. Пытаемся удалить колонку 'hourly_rate', от которой зависит 'total_cost'
        delete_col_resp = await test_client.delete(
            f"/instances/{instance_uuid}/templates/{template_uuid}/columns/hourly_rate",
            headers=headers,
        )

        # 3. Валидатор должен заблокировать мутацию
        assert delete_col_resp.status_code == 422

    @pytest.mark.asyncio
    async def test_prevent_column_type_mutation_used_in_trigger(
        self, test_client, create_test_environment
    ):
        """
        Тест 3: Блокировка деструктивного изменения типа колонки (number -> string)
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        template_payload = {
            "name": "Клиентские оплаты",
            "schema": {"payment_amount": {"type": "number", "required": True}},
        }
        tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=template_payload,
            headers=headers,
        )
        template_uuid = tpl_resp.json()["_id"]

        # ИСПРАВЛЕНО: event_type изменен на 'ON_RECORD_UPDATE'
        trigger_payload = {
            "name": "Уведомление о крупной оплате",
            "trigger_type": "AUTOMATION",
            "event_type": "ON_RECORD_UPDATE",
            "action_name": "test_action",
            "action_params": {"required_text": "Внимание"},
            "source_template_uuid": template_uuid,
            "target_template_uuid": template_uuid,
            "condition_ast": {
                "type": "binary_op",
                "operator": "gt",
                "left": {"type": "field", "value": "payment_amount"},
                "right": {"type": "literal", "value": 50000},
            },
            "payload_ast": {"type": "field", "value": "payment_amount"},
        }
        trigger_create_resp = await test_client.post(
            f"/instances/{instance_uuid}/triggers",
            json=trigger_payload,
            headers=headers,
        )
        assert trigger_create_resp.status_code == 201

        patch_col_payload = {
            "column_name": "payment_amount",
            "field_meta": {"type": "string", "required": True},
        }
        patch_col_resp = await test_client.patch(
            f"/instances/{instance_uuid}/templates/{template_uuid}/columns",
            json=patch_col_payload,
            headers=headers,
        )

        assert patch_col_resp.status_code == 422

    @pytest.mark.asyncio
    async def test_circular_dependency_formula_creation(
        self, test_client, create_test_environment
    ):
        """
        Тест 4: Отлов циклических зависимостей внутри схемы при создании.
        Попытка создать формулу A, зависящую от B, где B зависит от A.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        # Создаем заведомо сломанную схему (Уроборос)
        template_payload = {
            "name": "Сломанная аналитика",
            "schema": {
                "metric_a": {
                    "type": "formula",
                    "ast": {
                        "type": "binary_op",
                        "operator": "add",
                        "left": {"type": "field", "value": "metric_b"},
                        "right": {"type": "literal", "value": 10},
                    },
                },
                "metric_b": {
                    "type": "formula",
                    "ast": {
                        "type": "binary_op",
                        "operator": "multiply",
                        "left": {"type": "field", "value": "metric_a"},
                        "right": {"type": "literal", "value": 2},
                    },
                },
            },
        }

        # Отправляем запрос на создание
        tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=template_payload,
            headers=headers,
        )

        # Валидатор (check_circular_dependencies) должен перехватить это на лету
        assert tpl_resp.status_code in [400, 422]
        error_msg = str(tpl_resp.json()).lower()
        # Проверяем, что в сообщении упоминается цикличность или конкретные поля
        assert "cyclic" in error_msg or "циклич" in error_msg or "metric_a" in error_msg


class TestSchemaIntegrityCascade:

    @pytest.mark.asyncio
    async def test_prevent_delete_cascade_root_column_used_in_storefront(
        self, test_client, crm_environment_factory, db_session
    ):
        """
        Проверка блокировки изменения метаданных поля, если его вложенный путь (dot-notation)
        используется в активных правилах StorefrontPolicies.
        """
        env = await crm_environment_factory()
        instance_uuid = str(env["instance_uuid"])
        headers = env["creator_headers"]
        template_name = "products_table"

        # 1. Создаем шаблон с базовым полем
        tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            headers=headers,
            json={
                "name": template_name,
                "schema": {
                    "title": {"type": "string", "required": True},
                    "attributes": {"type": "string"},
                },
            },
        )
        tpl_id = tpl_resp.json()["_id"]

        # 2. Связываем поле вложенным путем внутри Storefront-политики через Postgres
        policy = StorefrontPolicies(
            instance_uuid=env["instance_uuid"],
            template_name=template_name,
            read_mask=["title", "attributes.Материал"],
            write_mask=["title"],
            read_filters={},
        )
        db_session.add(policy)
        await db_session.commit()

        # 3. Мутация поля должна быть заблокирована валидатором целостности схемы
        update_payload = {
            "column_name": "attributes",
            "field_meta": {"type": "select", "options": ["A", "B"]},
        }
        url = f"/instances/{instance_uuid}/templates/{tpl_id}/columns"
        response = await test_client.patch(url, headers=headers, json=update_payload)

        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_validate_storefront_policy_accepts_dot_notation_integration(
        self, db_session
    ):
        """
        Интеграционный тест бизнес-логики (без HTTP 404):
        Проверяем, что метод интеграции безболезненно пропускает dot-notation
        для валидных корней схем.
        """
        schema = {
            "device_name": {"type": "string"},
            "config": {"type": "string"},
        }

        valid_policy_payload = {
            "read_mask": ["device_name", "config.cpu_frequency"],
            "write_mask": ["device_name"],
            "read_filters": {},
        }

        # Вызов не должен вызывать исключений
        NoCodeSchemaValidator.validate_storefront_policy(schema, valid_policy_payload)

        invalid_policy_payload = {
            "read_mask": ["device_name", "wrong_root.ram"],
            "write_mask": ["device_name"],
            "read_filters": {},
        }

        # 1. Меняем класс исключения на SchemaValidationError
        with pytest.raises(SchemaValidationError) as exc_info:
            NoCodeSchemaValidator.validate_storefront_policy(
                schema, invalid_policy_payload
            )

        # 2. Переходим на строгое и чистое тестирование полей кастомного исключения
        exception = exc_info.value

        # Проверяем понятный текст ошибки
        assert "ошибка конфигурации read_mask" in exception.message.lower()

        # Проверяем структурированные детали, которые уйдут фронтенду
        assert exception.details["context"] == "read_mask"
        assert "wrong_root.ram" in exception.details["invalid_fields"]
        assert (
            exception.details["reason"] == "Несуществующие поля в маске чтения витрины"
        )


class TestCascadingTreeIntegration:

    @pytest.mark.asyncio
    async def test_cascading_tree_full_flow(self, test_client, crm_template_factory):
        """
        Проверка позитивной сквозной записи и негативной валидации структуры поля типа cascading_tree.
        """
        # 1. Описываем схему дерева конфигурации
        tree_schema = {
            "attributes": {
                "type": "cascading_tree",
                "tree_config": {
                    "floor_name": "Тип изделия",
                    "type": "fixed",
                    "options": {
                        "Брошь": {
                            "floor_name": "Стиль",
                            "type": "adaptive",
                            "options": {
                                "Винтаж": {
                                    "floor_name": "Материал",
                                    "type": "adaptive",
                                    "options": {"Дерево": None, "Металл": None},
                                }
                            },
                        },
                        "Кольцо": {
                            "floor_name": "Размер",
                            "type": "adaptive",
                            "options": {"17.5": None, "18.0": None},
                        },
                    },
                },
            }
        }

        # Создаем шаблон через фабрику
        tpl = await crm_template_factory(name="Товары", schema=tree_schema)
        url = tpl["base_url"]
        headers = tpl["headers"]

        # 2. Валидный кейс А: Полный путь пройден до листа дерева
        res_1 = await test_client.post(
            url,
            headers=headers,
            json={
                "data": {
                    "attributes": {
                        "Тип изделия": "Брошь",
                        "Стиль": "Винтаж",
                        "Материал": "Дерево",
                    }
                }
            },
        )
        assert res_1.status_code == 201

        # 3. Валидный кейс Б: Полный альтернативный путь до листа дерева
        res_2 = await test_client.post(
            url,
            headers=headers,
            json={"data": {"attributes": {"Тип изделия": "Кольцо", "Размер": "17.5"}}},
        )
        assert res_2.status_code == 201

        # 4. Негативный кейс А: Ключ из чужой ветки дерева ("Материал" недоступен внутри ветки "Кольцо")
        res_3 = await test_client.post(
            url,
            headers=headers,
            json={
                "data": {
                    "attributes": {
                        "Тип изделия": "Кольцо",
                        "Размер": "17.5",
                        "Материал": "Дерево",
                    }
                }
            },
        )
        assert res_3.status_code in [400, 422]

        # 5. Негативный кейс Б: Путь прерван, обязательный дочерний уровень пропущен
        res_4 = await test_client.post(
            url,
            headers=headers,
            json={"data": {"attributes": {"Тип изделия": "Брошь", "Стиль": "Винтаж"}}},
        )
        assert res_4.status_code in [400, 422]

    @pytest.mark.asyncio
    async def test_cascading_tree_negative_payloads(
        self, test_client, crm_template_factory
    ):
        """
        Негативные тесты валидации cascading_tree: отправка некорректных уровней и несуществующих опций.
        """
        # 1. Инициализируем шаблон с полем cascading_tree через фабрику
        tree_schema = {
            "attributes": {
                "type": "cascading_tree",
                "tree_config": {
                    "floor_name": "Тип изделия",
                    "type": "fixed",
                    "options": {
                        "Брошь": {
                            "floor_name": "Стиль",
                            "type": "adaptive",
                            "options": {
                                "Винтаж": {
                                    "floor_name": "Материал",
                                    "type": "adaptive",
                                    "options": {"Дерево": None, "Металл": None},
                                }
                            },
                        }
                    },
                },
            }
        }
        tpl = await crm_template_factory(name="Товары", schema=tree_schema)
        url, headers = tpl["base_url"], tpl["headers"]

        # 2. Негативный кейс 1: Передача выдуманного этажа ("Уровень"), отсутствующего в схеме
        res_trash_floor = await test_client.post(
            url,
            headers=headers,
            json={
                "data": {
                    "attributes": {
                        "Тип изделия": "Брошь",
                        "Стиль": "Винтаж",
                        "Материал": "Дерево",
                        "Уровень": "Секретный",
                    }
                }
            },
        )
        assert res_trash_floor.status_code in [400, 422]

        # 3. Негативный кейс 2: Передача выдуманного значения ("Пластик") для валидного этажа
        res_trash_value = await test_client.post(
            url,
            headers=headers,
            json={
                "data": {
                    "attributes": {
                        "Тип изделия": "Брошь",
                        "Стиль": "Винтаж",
                        "Материал": "Пластик",
                    }
                }
            },
        )
        assert res_trash_value.status_code in [400, 422]

    @pytest.mark.asyncio
    async def test_cascading_tree_uneven_branches(
        self, test_client, create_test_environment
    ):
        user_uuid, instance_uuid, headers = await create_test_environment()

        # 1. Создаем НЕРАВНОМЕРНОЕ дерево в шаблоне
        template_payload = {
            "name": "Ювелирные Изделия",
            "schema": {
                "attributes": {
                    "type": "cascading_tree",
                    "tree_config": {
                        "floor_name": "Тип изделия",
                        "type": "fixed",
                        "options": {
                            # КОРОТКАЯ ВЕТКА: всего 2 этажа
                            "Брошь": {
                                "floor_name": "Цвет",
                                "type": "adaptive",
                                "options": {"Красный": None, "Зеленый": None},
                            },
                            # ДЛИННАЯ ВЕТКА: 4 этажа (Тип -> Размер -> Камень -> Материал)
                            "Колье": {
                                "floor_name": "Размер",
                                "type": "fixed",
                                "options": {
                                    "45см": {
                                        "floor_name": "Камень",
                                        "type": "adaptive",
                                        "options": {
                                            "Изумруд": {
                                                "floor_name": "Материал",
                                                "type": "adaptive",
                                                "options": {
                                                    "Золото": None,
                                                    "Серебро": None,
                                                },
                                            }
                                        },
                                    }
                                },
                            },
                        },
                    },
                }
            },
        }

        tpl_resp = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=template_payload,
            headers=headers,
        )
        assert tpl_resp.status_code == 201
        template_uuid = tpl_resp.json()["_id"]

        # 2. Успешный кейс: Короткий путь (Брошь -> Цвет)
        short_path_data = {"attributes": {"Тип изделия": "Брошь", "Цвет": "Красный"}}
        resp_short = await test_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json={"data": short_path_data},
            headers=headers,
        )
        assert resp_short.status_code == 201

        # 3. Успешный кейс: Длинный путь (Колье -> Размер -> Камень -> Материал)
        long_path_data = {
            "attributes": {
                "Тип изделия": "Колье",
                "Размер": "45см",
                "Камень": "Изумруд",
                "Материал": "Золото",
            }
        }
        resp_long = await test_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json={"data": long_path_data},
            headers=headers,
        )
        assert resp_long.status_code == 201

        # 4. Негативный кейс: Пытаемся к Броши добавить этажи из длинной ветки
        mixed_invalid_data = {
            "attributes": {
                "Тип изделия": "Брошь",
                "Цвет": "Красный",
                "Камень": "Изумруд",  # Ошибка! У Броши нет камней в схеме
            }
        }
        resp_invalid = await test_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json={"data": mixed_invalid_data},
            headers=headers,
        )
        assert resp_invalid.status_code in [400, 422]

        # 5. Негативный кейс: Оборвали длинный путь на середине для Колье
        incomplete_long_data = {
            "attributes": {
                "Тип изделия": "Колье",
                "Размер": "45см",  # Ошибка! Забыли указать Камень и Материал
            }
        }
        resp_incomplete = await test_client.post(
            f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
            json={"data": incomplete_long_data},
            headers=headers,
        )
        assert resp_incomplete.status_code in [400, 422]


@pytest.mark.asyncio
async def test_create_primitive_record_in_tables_endpoint(
    test_client, create_test_environment
):
    """
    Верификация эндпоинта /tables: создание шаблона с примитивными полями
    и последующее добавление записи через метод POST.
    """
    # 1. Подготавливаем окружение (инстанс, юзер, авторизационные заголовки)
    user_uuid, instance_uuid, headers = await create_test_environment()

    # 2.Payload шаблона с базовыми типами: строка и число
    template_payload = {
        "name": "Тестовая Таблица Примитивов",
        "schema": {
            "title": {"type": "string", "required": True},
            "quantity": {"type": "number", "required": False},
        },
    }

    # Создаем шаблон
    tpl_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates",
        json=template_payload,
        headers=headers,
    )
    assert tpl_resp.status_code == 201, f"Не удалось создать шаблон: {tpl_resp.json()}"

    template_uuid = tpl_resp.json()["_id"]

    # 3. Тестируем отправку записи на эндпоинт /tables
    record_payload = {"data": {"title": "Первый тестовый элемент", "quantity": 10}}

    response = await test_client.post(
        f"/instances/{instance_uuid}/templates/{template_uuid}/tables",
        json=record_payload,
        headers=headers,
    )

    # Выводим детальный лог ошибки бэкенда, если тест упадет с 422
    if response.status_code != 201:
        print(f"\n[BACKEND ERROR DETAIL] Status: {response.status_code}")
        print(f"[RESPONSE JSON]: {response.json()}")

    # Проверяем успешность операции
    assert response.status_code == 201

    # Проверяем структуру ответа
    response_data = response.json()
    assert response_data["instance_uuid"] == str(instance_uuid)
    assert response_data["template_uuid"] == str(template_uuid)
    assert response_data["data"]["title"] == "Первый тестовый элемент"
    assert response_data["data"]["quantity"] == 10
    assert response_data["is_deleted"] is False
