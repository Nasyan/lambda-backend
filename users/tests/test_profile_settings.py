# users/tests/test_profile_settings.py

import pytest
from uuid import uuid4
from sqlalchemy.future import select

from users.models import (
    UserPermissions,
    Users,
    UserSettings,
    UserRole,
    UserLanguage,
    Instances,
)


@pytest.mark.asyncio
class TestUserProfileAndSettings:

    async def test_get_my_context_success(self, auth_client, db_session, user_factory):
        """
        Успешное получение контекста профиля (dashboard).
        Проверяем, что эндпоинт отдает профиль и список коллег.
        """
        client, current_user = auth_client

        # Создаем инстанс и привязываем юзера, чтобы проверить блок "team"
        test_instance = Instances(uuid=uuid4(), title="Context Studio", active=True)
        db_session.add(test_instance)

        current_user.instance_id = test_instance.uuid
        await db_session.commit()

        # Создаем коллегу в том же инстансе
        colleague_data = user_factory()
        colleague = Users(
            uuid=uuid4(),
            name=colleague_data["name"],
            email=colleague_data["email"],
            instance_id=test_instance.uuid,
            role=UserRole.USER,
            active=True,
        )
        colleague.password = "Pass123!"
        db_session.add(colleague)
        await db_session.commit()

        response = await client.get("/users/me/context")

        assert response.status_code == 200
        data = response.json()

        assert "profile" in data
        assert data["profile"]["email"] == current_user.email
        assert "team" in data
        # В команде должен быть текущий юзер + коллега
        assert len(data["team"]) == 2

    async def test_set_god_mode_success(self, auth_client, db_session):
        """
        Успешное включение God Mode Креатором (Админом).
        Параметр передается как query (?enabled=true).
        """
        client, creator_user = auth_client

        # Настраиваем права для прохождения get_current_creator
        test_instance = Instances(uuid=uuid4(), title="Admin Space", active=True)
        db_session.add(test_instance)
        creator_user.role = UserRole.CREATOR
        creator_user.instance_id = test_instance.uuid
        await db_session.commit()

        response = await client.post("/users/me/settings/god-mode?enabled=true")

        assert response.status_code == 200
        assert response.json()["god_mode"] is True

        # Проверяем БД
        stmt = select(UserSettings).where(UserSettings.user_uuid == creator_user.uuid)
        res = await db_session.execute(stmt)
        settings = res.scalar_one()

        assert settings.god_mode is True

    async def test_set_god_mode_forbidden_for_regular_user(
        self, auth_client, db_session
    ):
        """
        Ошибка 403, если обычный юзер пытается включить God Mode.
        """
        client, regular_user = auth_client

        regular_user.role = UserRole.USER
        await db_session.commit()

        response = await client.post("/users/me/settings/god-mode?enabled=true")

        # Так как get_current_creator требует роли CREATOR, должна быть ошибка доступа
        assert response.status_code == 403

    async def test_set_language_success(self, auth_client, db_session):
        """
        Успешная смена языка (доступно любому пользователю).
        """
        client, current_user = auth_client

        response = await client.post("/users/me/settings/language?lang=en")

        assert response.status_code == 200
        assert response.json()["language"] == "en"

        # Проверяем БД
        stmt = select(UserSettings).where(UserSettings.user_uuid == current_user.uuid)
        res = await db_session.execute(stmt)
        settings = res.scalar_one()

        assert settings.language == UserLanguage.EN

    async def test_update_ui_kit_success(self, auth_client, db_session):
        """
        Успешное обновление UI-кита (валидный JSON).
        """
        client, current_user = auth_client
        item_uuid = str(uuid4())

        payload = {
            "favorites": [
                {
                    "uuid": item_uuid,
                    "type": "template",
                    "subtype": "notes",
                    "position": {"x": 1, "y": 2},
                }
            ]
        }

        response = await client.put("/users/me/ui-kit", json=payload)

        assert response.status_code == 200
        assert response.json()["status"] == "success"

        # Проверяем БД
        stmt = select(UserSettings).where(UserSettings.user_uuid == current_user.uuid)
        res = await db_session.execute(stmt)
        settings = res.scalar_one()

        assert len(settings.ui_kits["favorites"]) == 1
        assert settings.ui_kits["favorites"][0]["uuid"] == item_uuid

    async def test_update_ui_kit_validation_error(self, auth_client):
        """
        Ошибка 422 от Pydantic при несовпадении типа и подтипа
        (например, type=analytics, а subtype=notes - запрещено матрицей).
        """
        client, _ = auth_client
        item_uuid = str(uuid4())

        payload = {
            "favorites": [
                {
                    "uuid": item_uuid,
                    "type": "analytics",
                    "subtype": "notes",  # Невалидная комбинация
                    "position": {"x": 0, "y": 0},
                }
            ]
        }

        response = await client.put("/users/me/ui-kit", json=payload)

        # Должна отработать наша матрица валидации из @model_validator
        assert response.status_code == 422
        assert "Невалидный subtype" in response.text

    async def test_remove_ui_kit_item_success(self, auth_client, db_session):
        """
        Успешное удаление элемента из UI-кита по UUID.
        """
        client, current_user = auth_client
        item_uuid = str(uuid4())

        # 1. Предварительно создаем настройки с одним элементом в избранном
        settings = UserSettings(
            user_uuid=current_user.uuid,
            ui_kits={
                "favorites": [
                    {
                        "uuid": item_uuid,
                        "type": "template",
                        "subtype": "tables",
                        "position": {"x": 0, "y": 0},
                    }
                ]
            },
        )
        db_session.add(settings)
        await db_session.commit()

        # 2. Удаляем этот элемент
        response = await client.delete(f"/users/me/ui-kit/item/{item_uuid}")

        assert response.status_code == 200
        assert response.json()["status"] == "success"

        # 3. Проверяем БД: список избранного должен стать пустым
        await db_session.refresh(settings, attribute_names=["ui_kits"])

        assert len(settings.ui_kits["favorites"]) == 0

    async def test_get_creator_context_success(
        self, auth_client, db_session, user_factory
    ):
        """
        Успешное получение контекста Креатора.
        Проверяем, что в списке команды отдаются права ('allowed_tools') каждого юзера.
        """
        client, creator_user = auth_client

        # 1. Настраиваем инстанс и роль Креатора
        test_instance = Instances(
            uuid=uuid4(), title="Creator Dashboard Studio", active=True
        )
        db_session.add(test_instance)

        creator_user.role = UserRole.CREATOR
        creator_user.instance_id = test_instance.uuid
        await db_session.commit()

        # 2. Создаем обычного сотрудника в этом же инстансе
        emp_data = user_factory()
        emp_uuid = uuid4()
        employee = Users(
            uuid=emp_uuid,
            name=emp_data["name"],
            email=emp_data["email"],
            instance_id=test_instance.uuid,
            role=UserRole.USER,
            active=True,
        )
        employee.password = "EmployeePass123!"
        db_session.add(employee)

        # Навешиваем сотруднику специфичные права
        emp_perms = UserPermissions(
            user_uuid=emp_uuid, allowed_tools=["notes", "tables"]
        )
        db_session.add(emp_perms)
        await db_session.commit()

        # 3. Делаем запрос к новому эндпоинту
        response = await client.get("/users/me/creator/context")

        assert response.status_code == 200
        data = response.json()

        # Проверяем структуру ответа
        assert "profile" in data
        assert data["profile"]["role"] == "CREATOR"
        assert "team" in data

        # Ищем нашего сотрудника в массиве team и проверяем его права
        emp_json = next(
            (member for member in data["team"] if member["uuid"] == str(emp_uuid)), None
        )
        assert emp_json is not None
        assert "allowed_tools" in emp_json
        assert sorted(emp_json["allowed_tools"]) == sorted(["notes", "tables"])

    async def test_get_creator_context_forbidden_for_regular_user(
        self, auth_client, db_session
    ):
        """
        Ошибка 403 при попытке обычного пользователя постучаться в контекст Креатора.
        """
        client, regular_user = auth_client

        # Гарантируем, что у пользователя обычная роль USER
        regular_user.role = UserRole.USER
        await db_session.commit()

        response = await client.get("/users/me/creator/context")

        # Зависимость get_current_creator должна заблокировать этот запрос
        assert response.status_code == 403

    async def test_get_ui_kit_empty_by_default(self, auth_client):
        """
        Успешное получение UI Kit, если настроек еще нет в базе (должен вернуться пустой favorites).
        """
        client, _ = auth_client

        response = await client.get("/users/me/ui-kit")

        assert response.status_code == 200
        data = response.json()
        assert "favorites" in data
        assert data["favorites"] == []

    async def test_get_ui_kit_with_data(self, auth_client, db_session):
        """
        Успешное получение заполненного UI Kit из базы данных.
        """
        client, current_user = auth_client
        item_uuid = str(uuid4())

        settings = UserSettings(
            user_uuid=current_user.uuid,
            ui_kits={
                "favorites": [
                    {
                        "uuid": item_uuid,
                        "type": "template",
                        "subtype": "workflow",
                        "position": {"x": 5, "y": 10},
                    }
                ]
            },
        )
        db_session.add(settings)
        await db_session.commit()

        response = await client.get("/users/me/ui-kit")

        assert response.status_code == 200
        data = response.json()
        assert len(data["favorites"]) == 1
        assert data["favorites"][0]["uuid"] == item_uuid
        assert data["favorites"][0]["subtype"] == "workflow"

    async def test_add_ui_kit_item_success(self, auth_client, db_session):
        """
        Успешное добавление (CREATE) одного нового элемента на доску.
        """
        client, current_user = auth_client
        item_uuid = str(uuid4())

        payload = {
            "uuid": item_uuid,
            "type": "template",
            "subtype": "tables",
            "position": {"x": 2, "y": 3},
        }

        response = await client.post("/users/me/ui-kit/item", json=payload)

        assert response.status_code == 200
        assert response.json()["status"] == "success"

        # Проверяем изменения в базе данных
        stmt = select(UserSettings).where(UserSettings.user_uuid == current_user.uuid)
        res = await db_session.execute(stmt)
        settings = res.scalar_one()

        assert len(settings.ui_kits["favorites"]) == 1
        assert settings.ui_kits["favorites"][0]["uuid"] == item_uuid
        assert settings.ui_kits["favorites"][0]["position"]["x"] == 2

    async def test_update_item_position_success(self, auth_client, db_session):
        """
        Успешное изменение позиции (PATCH) виджета при Drag & Drop.
        """
        client, current_user = auth_client
        item_uuid = str(uuid4())

        # Инициализируем настройки с элементом в позиции (0, 0)
        settings = UserSettings(
            user_uuid=current_user.uuid,
            ui_kits={
                "favorites": [
                    {
                        "uuid": item_uuid,
                        "type": "analytics",
                        "subtype": "none",
                        "position": {"x": 0, "y": 0},
                    }
                ]
            },
        )
        db_session.add(settings)
        await db_session.commit()

        # Двигаем элемент на позицию (4, 8)
        new_position_payload = {"x": 4, "y": 8}
        response = await client.patch(
            f"/users/me/ui-kit/item/{item_uuid}/position", json=new_position_payload
        )

        assert response.status_code == 200
        assert response.json()["status"] == "success"

        # Проверяем, что в базе координаты перезаписались
        await db_session.refresh(settings, attribute_names=["ui_kits"])
        assert settings.ui_kits["favorites"][0]["position"]["x"] == 4
        assert settings.ui_kits["favorites"][0]["position"]["y"] == 8

    async def test_update_item_position_not_found(self, auth_client):
        """
        Ошибка 404 при попытке изменить координаты несуществующего элемента.
        """
        client, _ = auth_client
        fake_uuid = str(uuid4())

        response = await client.patch(
            f"/users/me/ui-kit/item/{fake_uuid}/position", json={"x": 1, "y": 1}
        )

        assert response.status_code == 404
        assert "не найден в UI Kit" in response.json()["detail"]

    async def test_clear_ui_kit_success(self, auth_client, db_session):
        """
        Успешная очистка (DELETE ALL) всей сетки виджетов.
        """
        client, current_user = auth_client

        # Наполняем базу несколькими элементами
        settings = UserSettings(
            user_uuid=current_user.uuid,
            ui_kits={
                "favorites": [
                    {
                        "uuid": str(uuid4()),
                        "type": "analytics",
                        "subtype": "none",
                        "position": {"x": 0, "y": 0},
                    },
                    {
                        "uuid": str(uuid4()),
                        "type": "template",
                        "subtype": "notes",
                        "position": {"x": 1, "y": 1},
                    },
                ]
            },
        )
        db_session.add(settings)
        await db_session.commit()

        # Вызываем очистку
        response = await client.delete("/users/me/ui-kit")

        assert response.status_code == 200
        assert response.json()["status"] == "success"

        # Проверяем, что массив favorites стал абсолютно пустым
        await db_session.refresh(settings, attribute_names=["ui_kits"])
        assert settings.ui_kits["favorites"] == []
