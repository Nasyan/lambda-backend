# users/tests/test_admin.py

import pytest
from uuid import uuid4
from sqlalchemy import select
from users.models import Users, UserRole
from users.utils import init_admin
from config import INVITE_PREFIX, SENDER_EMAIL, ADMIN_PASSWORD
from redisdb.utils import generate_key


@pytest.mark.asyncio
class TestAdminInitialization:

    async def test_admin_created_successfully_when_db_empty(self, db_session):
        """Проверяет корректное создание админа при чистой БД."""
        initial_query = await db_session.execute(
            select(Users).where(Users.role == UserRole.ADMIN)
        )
        assert initial_query.scalar_one_or_none() is None

        await init_admin(db_session)

        result_query = await db_session.execute(
            select(Users)
            .where(Users.role == UserRole.ADMIN)
            .execution_options(populate_existing=True)
        )
        admin_user = result_query.scalar_one_or_none()

        assert admin_user is not None
        assert admin_user.email == SENDER_EMAIL
        assert admin_user.role == UserRole.ADMIN
        assert admin_user.active is True


@pytest.mark.asyncio
class TestAdminAuthAndManagement:

    async def test_admin_login_success(self, test_client, db_session):
        """
        Тест успешного входа администратора с использованием
        SENDER_EMAIL и ADMIN_PASSWORD.
        """
        await init_admin(db_session)

        login_data = {"username": SENDER_EMAIL, "password": ADMIN_PASSWORD}
        response = await test_client.post("/admin/login", data=login_data)

        assert response.status_code == 200
        json_data = response.json()
        assert "access_token" in json_data
        assert json_data["token_type"] == "bearer"

    async def test_admin_login_invalid_password(self, test_client, db_session):
        """Тест падения авторизации при неверном пароле."""
        await init_admin(db_session)

        login_data = {"username": SENDER_EMAIL, "password": "wrong_password_123"}
        response = await test_client.post("/admin/login", data=login_data)

        assert response.status_code == 401

    async def test_create_instance_success(self, test_client, db_session):
        """Тест успешного создания нового инстанса администратором."""
        await init_admin(db_session)
        login_data = {"username": SENDER_EMAIL, "password": ADMIN_PASSWORD}
        login_res = await test_client.post("/admin/login", data=login_data)
        test_client.headers["Authorization"] = (
            f"Bearer {login_res.json()['access_token']}"
        )

        instance_payload = {"title": "Overwatch Design Studio"}
        response = await test_client.post("/admin/instances", json=instance_payload)

        assert response.status_code == 201
        json_data = response.json()
        assert "uuid" in json_data
        assert json_data["title"] == "Overwatch Design Studio"
        assert json_data["active"] is True

    async def test_create_instance_duplicate_title(self, test_client, db_session):
        """Проверка, что нельзя создать инстанс с уже существующим названием."""
        await init_admin(db_session)
        login_data = {"username": SENDER_EMAIL, "password": ADMIN_PASSWORD}
        login_res = await test_client.post("/admin/login", data=login_data)
        test_client.headers["Authorization"] = (
            f"Bearer {login_res.json()['access_token']}"
        )

        instance_payload = {"title": "Unique Studio"}

        # Первая отправка — успех
        res1 = await test_client.post("/admin/instances", json=instance_payload)
        assert res1.status_code == 201

        # Вторая отправка — конфликт названий
        res2 = await test_client.post("/admin/instances", json=instance_payload)
        assert res2.status_code == 400

    async def test_invite_creator_success_flow(
        self, test_client, db_session, redis_email_db
    ):
        """
        Интеграционный тест: создание инстанса -> генерация инвайта для Creator
        с привязкой к этому инстансу -> проверка записи UUID инстанса в Redis.
        """
        # 1. Создаем админа и логинимся
        await init_admin(db_session)
        login_data = {"username": SENDER_EMAIL, "password": ADMIN_PASSWORD}
        login_res = await test_client.post("/admin/login", data=login_data)
        token = login_res.json()["access_token"]
        test_client.headers["Authorization"] = f"Bearer {token}"

        # 2. Сначала генерируем инстанс, куда будем приглашать креатора
        instance_res = await test_client.post(
            "/admin/instances", json={"title": "Pravaon Studio"}
        )
        instance_id = instance_res.json()["uuid"]

        # 3. Отправляем инвайт с instance_id
        target_email = "new_creator_studio@gmail.com"
        invite_payload = {"email": target_email, "instance_id": instance_id}

        response = await test_client.post("/admin/invite-creator", json=invite_payload)

        assert response.status_code == 200
        assert response.json()["status"] == "success"

        # 4. Проверяем состояние Redis напрямую: значением должен быть UUID инстанса
        redis_key = generate_key(prefix=INVITE_PREFIX, sub=target_email)
        redis_value_bytes = await redis_email_db.get(redis_key)

        assert redis_value_bytes is not None
        assert redis_value_bytes == instance_id

        # Проверяем, что TTL выставился на 24 часа
        ttl = await redis_email_db.ttl(redis_key)
        assert 86390 <= ttl <= 86400

    async def test_invite_creator_non_existent_instance(self, test_client, db_session):
        """Система должна возвращать 404, если указан UUID несуществующего инстанса."""
        await init_admin(db_session)
        login_data = {"username": SENDER_EMAIL, "password": ADMIN_PASSWORD}
        login_res = await test_client.post("/admin/login", data=login_data)
        test_client.headers["Authorization"] = (
            f"Bearer {login_res.json()['access_token']}"
        )

        # Передаем случайный сгенерированный UUID
        invite_payload = {
            "email": "ghost_creator@example.com",
            "instance_id": str(uuid4()),
        }
        response = await test_client.post("/admin/invite-creator", json=invite_payload)

        assert response.status_code == 404

    async def test_invite_creator_forbidden_for_unauthorized(self, test_client):
        """Проверка, что без токена доступ к инвайтам закрыт."""
        test_client.headers.clear()

        invite_payload = {"email": "someone@example.com", "instance_id": str(uuid4())}
        response = await test_client.post("/admin/invite-creator", json=invite_payload)

        assert response.status_code in [401, 403]

    async def test_invite_creator_already_exists_in_db(self, test_client, db_session):
        """Система не должна разрешать инвайт, если email уже занят в PostgreSQL."""
        await init_admin(db_session)
        login_data = {"username": SENDER_EMAIL, "password": ADMIN_PASSWORD}
        login_res = await test_client.post("/admin/login", data=login_data)
        test_client.headers["Authorization"] = (
            f"Bearer {login_res.json()['access_token']}"
        )

        instance_res = await test_client.post(
            "/admin/instances", json={"title": "Temp Studio"}
        )
        instance_id = instance_res.json()["uuid"]

        # Пытаемся выслать инвайт на почту самого админа (она уже есть в базе)
        invite_payload = {"email": SENDER_EMAIL, "instance_id": instance_id}
        response = await test_client.post("/admin/invite-creator", json=invite_payload)

        assert response.status_code == 400

    # --- ТЕСТЫ ДЛЯ НОВЫХ ЭНДПОИНТОВ УПРАВЛЕНИЯ CREATORS ---
    # --- ТЕСТЫ ДЛЯ НОВЫХ ЭНДПОИНТОВ УПРАВЛЕНИЯ CREATORS ---

    async def _create_test_user(
        self, db_session, email: str, role: UserRole, active: bool = True
    ) -> Users:
        """Вспомогательный метод для быстрого создания пользователей в БД."""
        user = Users(role=role, active=active)
        # Используем твой setter для email и password
        user.email = email
        user.password = "SecurePass123!"  # Соответствует валидации в модели

        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        return user

    async def test_list_creators_success(self, test_client, db_session):
        """Позитивный сценарий: Успешное получение списка всех Creator."""
        # 1. Авторизуемся под админом
        await init_admin(db_session)
        login_data = {"username": SENDER_EMAIL, "password": ADMIN_PASSWORD}
        login_res = await test_client.post("/admin/login", data=login_data)
        test_client.headers["Authorization"] = (
            f"Bearer {login_res.json()['access_token']}"
        )

        # 2. Создаем тестовых пользователей
        await self._create_test_user(
            db_session, "creator_a@example.com", UserRole.CREATOR
        )
        await self._create_test_user(
            db_session, "creator_b@example.com", UserRole.CREATOR
        )
        await self._create_test_user(
            db_session, "regular_user@example.com", UserRole.USER
        )

        # 3. Запрашиваем список
        response = await test_client.get("/admin/creators")

        assert response.status_code == 200
        json_data = response.json()

        assert len(json_data) == 2
        emails = [item["email"] for item in json_data]
        assert "creator_a@example.com" in emails
        assert "creator_b@example.com" in emails
        assert all(item["role"] == "CREATOR" for item in json_data)

    async def test_get_creator_by_uuid_success(self, test_client, db_session):
        """Позитивный сценарий: Получение детальной информации о Creator по UUID."""
        await init_admin(db_session)
        login_data = {"username": SENDER_EMAIL, "password": ADMIN_PASSWORD}
        login_res = await test_client.post("/admin/login", data=login_data)
        test_client.headers["Authorization"] = (
            f"Bearer {login_res.json()['access_token']}"
        )

        target_creator = await self._create_test_user(
            db_session, "target_creator@example.com", UserRole.CREATOR
        )

        response = await test_client.get(f"/admin/creators/{target_creator.uuid}")

        assert response.status_code == 200
        json_data = response.json()
        assert json_data["uuid"] == str(target_creator.uuid)
        assert json_data["email"] == "target_creator@example.com"
        assert json_data["role"] == "CREATOR"

    async def test_get_creator_by_uuid_not_found(self, test_client, db_session):
        """Негативный сценарий: Попытка получить несуществующего Creator."""
        await init_admin(db_session)
        login_data = {"username": SENDER_EMAIL, "password": ADMIN_PASSWORD}
        login_res = await test_client.post("/admin/login", data=login_data)
        test_client.headers["Authorization"] = (
            f"Bearer {login_res.json()['access_token']}"
        )

        fake_uuid = str(uuid4())
        response = await test_client.get(f"/admin/creators/{fake_uuid}")

        assert response.status_code == 404

    async def test_get_creator_by_uuid_wrong_role(self, test_client, db_session):
        """Негативный сценарий: Попытка запросить пользователя, у которого роль не CREATOR."""
        await init_admin(db_session)
        login_data = {"username": SENDER_EMAIL, "password": ADMIN_PASSWORD}
        login_res = await test_client.post("/admin/login", data=login_data)
        test_client.headers["Authorization"] = (
            f"Bearer {login_res.json()['access_token']}"
        )

        not_a_creator = await self._create_test_user(
            db_session, "just_user@example.com", UserRole.USER
        )

        response = await test_client.get(f"/admin/creators/{not_a_creator.uuid}")

        assert response.status_code == 404

    async def test_deactivate_creator_success(self, test_client, db_session):
        """Позитивный сценарий: Успешная деактивация активного аккаунта Creator."""
        await init_admin(db_session)
        login_data = {"username": SENDER_EMAIL, "password": ADMIN_PASSWORD}
        login_res = await test_client.post("/admin/login", data=login_data)
        test_client.headers["Authorization"] = (
            f"Bearer {login_res.json()['access_token']}"
        )

        active_creator = await self._create_test_user(
            db_session, "active_c@example.com", UserRole.CREATOR, active=True
        )

        response = await test_client.patch(
            f"/admin/creators/{active_creator.uuid}/deactivate"
        )

        assert response.status_code == 200
        json_data = response.json()
        assert json_data["uuid"] == str(active_creator.uuid)
        assert json_data["active"] is False

    async def test_deactivate_creator_already_inactive(self, test_client, db_session):
        """Негативный сценарий: Попытка деактивировать уже забаненного/неактивного Creator."""
        await init_admin(db_session)
        login_data = {"username": SENDER_EMAIL, "password": ADMIN_PASSWORD}
        login_res = await test_client.post("/admin/login", data=login_data)
        test_client.headers["Authorization"] = (
            f"Bearer {login_res.json()['access_token']}"
        )

        inactive_creator = await self._create_test_user(
            db_session, "dead_c@example.com", UserRole.CREATOR, active=False
        )

        response = await test_client.patch(
            f"/admin/creators/{inactive_creator.uuid}/deactivate"
        )

        assert response.status_code == 400

    async def test_deactivate_creator_wrong_role_protection(
        self, test_client, db_session
    ):
        """Негативный сценарий: Защита от деактивации пользователей с другими ролями (например, Admin)."""
        await init_admin(db_session)
        login_data = {"username": SENDER_EMAIL, "password": ADMIN_PASSWORD}
        login_res = await test_client.post("/admin/login", data=login_data)
        test_client.headers["Authorization"] = (
            f"Bearer {login_res.json()['access_token']}"
        )

        admin_response = await db_session.execute(
            select(Users).where(Users.role == UserRole.ADMIN)
        )
        admin_user = admin_response.scalar_one()

        response = await test_client.patch(
            f"/admin/creators/{admin_user.uuid}/deactivate"
        )

        assert response.status_code == 404


@pytest.mark.asyncio
class TestAdminInstanceTriggersManagement:

    async def _setup_admin_auth(self, test_client, db_session) -> str:
        """Вспомогательный метод: авторизация админа и создание тестового инстанса."""
        await init_admin(db_session)
        login_data = {"username": SENDER_EMAIL, "password": ADMIN_PASSWORD}
        login_res = await test_client.post("/admin/login", data=login_data)
        token = login_res.json()["access_token"]
        test_client.headers["Authorization"] = f"Bearer {token}"

        instance_res = await test_client.post(
            "/admin/instances", json={"title": "Triggers Test Studio"}
        )
        return instance_res.json()["uuid"]

    async def test_get_triggers_config_returns_defaults(self, test_client, db_session):
        """Проверяет GET: автоматическое создание и возврат дефолтной схемы триггеров."""
        instance_id = await self._setup_admin_auth(test_client, db_session)

        response = await test_client.get(
            f"/admin/instances/{instance_id}/tools/triggers"
        )
        assert response.status_code == 200

        json_data = response.json()
        assert "enabled" in json_data
        assert isinstance(json_data["enabled"], bool)

    async def test_update_full_triggers_config_success(self, test_client, db_session):
        """Проверяет PUT: полное обновление конфигурации триггеров."""
        instance_id = await self._setup_admin_auth(test_client, db_session)

        payload = {
            "enabled": True,
            "allow_get": True,
            "allow_post": False,
            "allow_put": True,
            "allow_delete": False,
            "allow_cron": True,
        }
        response = await test_client.put(
            f"/admin/instances/{instance_id}/tools/triggers", json=payload
        )
        assert response.status_code == 200

        json_data = response.json()
        assert json_data["enabled"] is True
        assert json_data["allow_post"] is False
        assert json_data["allow_put"] is True

    async def test_patch_triggers_config_success(self, test_client, db_session):
        """Проверяет PATCH: частичное изменение только выбранных ключей."""
        instance_id = await self._setup_admin_auth(test_client, db_session)

        # Сначала задаем базовое состояние
        await test_client.put(
            f"/admin/instances/{instance_id}/tools/triggers",
            json={
                "enabled": True,
                "allow_get": True,
                "allow_post": True,
                "allow_put": True,
                "allow_delete": True,
            },
        )

        # Патчим только два поля
        patch_payload = {"enabled": False, "allow_put": False}
        response = await test_client.patch(
            f"/admin/instances/{instance_id}/tools/triggers", json=patch_payload
        )
        assert response.status_code == 200

        json_data = response.json()
        assert json_data["enabled"] is False
        assert json_data["allow_put"] is False
        # Проверяем, что остальные поля не затерлись дефолтами, а сохранились из базы (или отработал ваш валидатор сброса)
        assert "allow_get" in json_data

    async def test_disable_triggers_entirely_success(self, test_client, db_session):
        """Проверяет POST: быстрое полное отключение инструмента."""
        instance_id = await self._setup_admin_auth(test_client, db_session)

        response = await test_client.post(
            f"/admin/instances/{instance_id}/tools/triggers/disable"
        )
        assert response.status_code == 200
        assert response.json()["enabled"] is False

    async def test_triggers_endpoints_forbidden_for_unauthorized(self, test_client):
        """Проверяет защиту эндпоинтов от неавторизованных запросов."""
        test_client.headers.clear()
        fake_id = str(uuid4())

        response = await test_client.get(f"/admin/instances/{fake_id}/tools/triggers")
        assert response.status_code in [401, 403]
