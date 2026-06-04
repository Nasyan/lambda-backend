# core/tests/test_dynamic_fields_and_migrations.py

import pytest


class TestDynamicFieldsAndMigrations:

    @pytest.mark.asyncio
    async def test_new_fields_lifecycle_and_normalization(
        self, test_client, create_test_environment
    ):
        """
        Проверка валидации, очистки и нормализации новых типов полей (phone, datetime, checkbox).
        """
        user_uuid, instance_uuid, headers = await create_test_environment()

        # 1. Создание шаблона с новыми типами данных
        schema = {
            "client_phone": {"type": "phone", "required": True},
            "appointment_at": {"type": "datetime", "required": False},
            "is_vip": {"type": "checkbox", "default": False},
        }
        tpl = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            headers=headers,
            json={"name": "Лиды", "schema": schema},
        )
        tpl_id = tpl.json()["_id"]

        # 2. Позитивный сценарий: отправка сырых данных и проверка автонормализации движком
        valid_payload = {
            "data": {
                "client_phone": "+375 (29) 111-22-33",
                "appointment_at": "2026-05-25T15:00:00Z",
                "is_vip": True,
            }
        }
        res = await test_client.post(
            f"/instances/{instance_uuid}/templates/{tpl_id}/notes",
            headers=headers,
            json=valid_payload,
        )
        assert res.status_code == 201

        data = res.json()["data"]
        assert (
            data["client_phone"] == "+375291112233"
        )  # Телефон очищен от форматирования
        assert data["is_vip"] is True

        # 3. Негативный сценарий: отправка заведомо невалидных структур данных
        invalid_payload = {
            "data": {
                "client_phone": "not-a-phone-format",
                "appointment_at": "invalid-date",
                "is_vip": "Yes",  # Ожидается строго bool тип, строка вызовет ошибку
            }
        }
        err_res = await test_client.post(
            f"/instances/{instance_uuid}/templates/{tpl_id}/notes",
            headers=headers,
            json=invalid_payload,
        )
        assert err_res.status_code == 422

    @pytest.mark.asyncio
    async def test_migration_and_data_rewrite_on_type_change(
        self, test_client, create_test_environment
    ):
        """
        Проверка изменения метаданных колонки (string -> phone) с автоматической нормализацией данных в БД.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()
        base_url = f"/instances/{instance_uuid}/templates"

        # 1. Создаем шаблон, где поле телефона является обычной строкой
        tpl = await test_client.post(
            base_url,
            headers=headers,
            json={
                "name": "Контакты",
                "schema": {"contact": {"type": "string", "required": True}},
            },
        )
        tpl_id = tpl.json()["_id"]

        # 2. Сохраняем неформатированную "грязную" строку телефона
        await test_client.post(
            f"{base_url}/{tpl_id}/notes",
            headers=headers,
            json={"data": {"contact": "+375 29 999-88-77"}},
        )

        # 3. Выполняем миграцию: меняем тип поля на 'phone'
        migration_payload = {
            "column_name": "contact",
            "field_meta": {"type": "phone", "required": True},
        }
        migration_res = await test_client.patch(
            f"{base_url}/{tpl_id}/columns", headers=headers, json=migration_payload
        )
        assert migration_res.status_code == 200

        # 4. Проверяем физическое обновление и нормализацию данных внутри хранилища
        get_notes = await test_client.get(f"{base_url}/{tpl_id}/notes", headers=headers)
        response_data = get_notes.json()

        assert response_data["total"] == 1
        assert response_data["results"][0]["data"]["contact"] == "+375299998877"

    @pytest.mark.asyncio
    async def test_migration_blocking_on_invalid_data(
        self, test_client, create_test_environment
    ):
        """
        Проверка блокировки изменения типа поля (string -> datetime) при наличии несовместимых данных.
        """
        user_uuid, instance_uuid, headers = await create_test_environment()
        base_url = f"/instances/{instance_uuid}/templates"

        # 1. Создаем базовый шаблон со строковым полем
        tpl = await test_client.post(
            base_url,
            headers=headers,
            json={
                "name": "Логи",
                "schema": {"event_date": {"type": "string", "required": True}},
            },
        )
        tpl_id = tpl.json()["_id"]

        # 2. Заносим текстовые данные, которые невозможно преобразовать в ISO-дату
        await test_client.post(
            f"{base_url}/{tpl_id}/notes",
            headers=headers,
            json={"data": {"event_date": "Вчера после обеда"}},
        )

        # 3. Попытка изменить тип поля на 'datetime' должна быть отклонена валидатором миграций
        migration_payload = {
            "column_name": "event_date",
            "field_meta": {"type": "datetime", "required": True},
        }
        migration_res = await test_client.patch(
            f"{base_url}/{tpl_id}/columns", headers=headers, json=migration_payload
        )

        assert migration_res.status_code == 422
