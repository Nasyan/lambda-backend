# users/tests/test_client.py

import pytest
import asyncio
from sqlalchemy import select
from datetime import datetime, timezone, timedelta

from users.models import Users, UserRole, Instances
from redisdb.utils import generate_key
from config import JOIN_PREFIX
from jsonwebtoken.utils import encode_jwt
import uuid


class TestClientStorefrontAuth:

    @pytest.mark.asyncio
    async def test_client_registration_flow_success(
        self, test_client, db_session, redis_email_db, user_factory, monkeypatch
    ):
        """
        Тест полного цикла регистрации CLIENT:
        1. /storefront-auth/register/ -> Создается неактивный CLIENT с привязкой к инстансу.
        2. /storefront-auth/verify/ -> CLIENT активируется, код удаляется из Redis.
        """
        mock_send_called = False

        def mock_send(*args, **kwargs):
            nonlocal mock_send_called
            mock_send_called = True

        monkeypatch.setattr("workers.email_tasks.send_email.send", mock_send)

        # 1. Готовим инстанс магазина в PostgreSQL
        test_instance = Instances(title="My Online Store", active=True)
        db_session.add(test_instance)
        await db_session.commit()
        await db_session.refresh(test_instance)

        # 2. Формируем данные (добавляем instance_id, так как инвайтов нет)
        user_data = user_factory()
        user_data["instance_id"] = str(test_instance.uuid)
        email = user_data["email"]

        # 3. Шаг первый: Публичная отправка формы регистрации на витрине
        response = await test_client.post("/storefront-auth/register/", json=user_data)

        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert mock_send_called is True

        # Проверяем, что в БД создался неактивный юзер с ЖЕСТКОЙ ролью CLIENT
        result = await db_session.execute(
            select(Users)
            .where(Users._email == email)
            .execution_options(populate_existing=True)
        )
        user = result.scalar_one_or_none()
        assert user is not None
        assert user.active is False
        assert user.role == UserRole.CLIENT
        assert user.instance_id == test_instance.uuid

        # 4. Шаг второй: Достаем сгенерированный 6-значный код из Redis
        join_redis_key = generate_key(prefix=JOIN_PREFIX, sub=email)
        stored_code_bytes = await redis_email_db.get(join_redis_key)
        assert stored_code_bytes is not None

        verification_code = (
            stored_code_bytes.decode("utf-8")
            if isinstance(stored_code_bytes, bytes)
            else stored_code_bytes
        )

        # 5. Шаг третий: Клиент вводит код верификации с фронтенда магазина
        verify_payload = {"email": email, "code": verification_code}
        verify_response = await test_client.post(
            "/storefront-auth/verify/", json=verify_payload
        )

        assert verify_response.status_code == 200
        verify_json = verify_response.json()
        assert verify_json["status"] == "success"
        assert verify_json["user"]["instance_id"] == str(test_instance.uuid)

        # 6. Проверяем активацию в PostgreSQL
        await db_session.refresh(user)
        assert user.active is True

        # 7. Проверяем чистоту Redis
        assert not await redis_email_db.exists(join_redis_key)

    @pytest.mark.asyncio
    async def test_client_registration_fails_for_non_existent_instance(
        self, test_client, db_session, user_factory
    ):
        """
        Защита от мусора: Нельзя зарегистрировать клиента к несуществующему инстансу (магазину).
        """
        user_data = user_factory()
        # Генерируем фейковый случайный UUID инстанса
        user_data["instance_id"] = "00000000-0000-0000-0000-000000000000"

        response = await test_client.post("/storefront-auth/register/", json=user_data)

        assert response.status_code == 404

        # Проверяем, что запись в БД не появилась
        result = await db_session.execute(
            select(Users).where(Users._email == user_data["email"])
        )
        assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_client_verify_fails_with_invalid_code(
        self, test_client, db_session, user_factory, monkeypatch
    ):
        """
        Если клиент вводит неверный код, возвращается 400 и аккаунт остается неактивным.
        """
        monkeypatch.setattr(
            "workers.email_tasks.send_email.send", lambda *args, **kwargs: None
        )

        test_instance = Instances(title="Store 2", active=True)
        db_session.add(test_instance)
        await db_session.commit()

        user_data = user_factory()
        user_data["instance_id"] = str(test_instance.uuid)
        email = user_data["email"]

        await test_client.post("/storefront-auth/register/", json=user_data)

        # Шлем неверный код
        verify_payload = {"email": email, "code": "999999"}
        response = await test_client.post(
            "/storefront-auth/verify/", json=verify_payload
        )

        assert response.status_code == 400

        result = await db_session.execute(select(Users).where(Users._email == email))
        assert result.scalar_one_or_none().active is False

    @pytest.mark.asyncio
    async def test_client_resend_code_rate_limit(
        self, test_client, db_session, user_factory, monkeypatch
    ):
        """
        Проверка Rate Limiter на повторную отправку кода для клиентов (429 Too Many Requests).
        """
        monkeypatch.setattr(
            "workers.email_tasks.send_email.send", lambda *args, **kwargs: None
        )

        test_instance = Instances(title="Store 3", active=True)
        db_session.add(test_instance)
        await db_session.commit()

        user_data = user_factory()
        user_data["instance_id"] = str(test_instance.uuid)

        await test_client.post("/storefront-auth/register/", json=user_data)

        # Сразу шлем повторный запрос без ожидания
        response = await test_client.post(
            "/storefront-auth/resend-code/", json={"email": user_data["email"]}
        )

        assert response.status_code == 429


@pytest.mark.asyncio
class TestClientTokenRefreshFlow:

    async def _create_active_client(
        self, db_session, email: str, instance: Instances
    ) -> Users:
        """Хелпер для создания активного покупателя с ролью CLIENT."""
        user = Users(role=UserRole.CLIENT, active=True, instance_id=instance.uuid)
        user.email = email
        user.password = "CustomerPass123!"
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        return user

    async def test_client_login_success_and_cookie_isolation(
        self, test_client, db_session
    ):
        """
        Успешный логин клиента: возвращается JWT, а рефреш прячется в ИЗОЛИРОВАННУЮ куку client_refresh_token.
        """
        test_instance = Instances(title="Isolated Store", active=True)
        db_session.add(test_instance)
        await db_session.commit()

        email = "client_buyer@example.com"
        await self._create_active_client(db_session, email, test_instance)

        login_data = {"username": email, "password": "CustomerPass123!"}
        response = await test_client.post("/storefront-auth/login/", data=login_data)

        assert response.status_code == 200
        assert "access_token" in response.json()

        set_cookie_header = response.headers.get("set-cookie")
        assert set_cookie_header is not None
        # Проверяем, что кука изолирована от CRM-контура
        assert "client_refresh_token=" in set_cookie_header
        assert "HttpOnly" in set_cookie_header

    async def test_crm_user_cannot_login_through_storefront_endpoint(
        self, test_client, db_session
    ):
        """
        🔒 Проверка защиты от Role Escalation / Cross-Pollination.
        Менеджер CRM (USER/CREATOR) не должен иметь возможности авторизоваться через клиентский роутер.
        """
        user = Users(role=UserRole.USER, active=True)
        user.email = "crm_manager@example.com"
        user.password = "ManagerPass123!"
        db_session.add(user)
        await db_session.commit()

        login_data = {
            "username": "crm_manager@example.com",
            "password": "ManagerPass123!",
        }
        response = await test_client.post("/storefront-auth/login/", data=login_data)

        # Сервис должен отбросить пользователя с ролью USER на клиентском входе
        assert response.status_code == 400

    async def test_client_refresh_session_success(self, test_client, db_session):
        """
        Успешный цикл ротации токенов по изолированной клиентской куке.
        """
        test_instance = Instances(title="Refresh Store", active=True)
        db_session.add(test_instance)
        await db_session.commit()

        email = "refresh_buyer@example.com"
        await self._create_active_client(db_session, email, test_instance)

        login_data = {"username": email, "password": "CustomerPass123!"}
        login_res = await test_client.post("/storefront-auth/login/", data=login_data)

        first_access_token = login_res.json()["access_token"]
        client_refresh = test_client.cookies.get("client_refresh_token")
        assert client_refresh is not None

        test_client.headers.clear()
        await asyncio.sleep(1.05)  # Гарантируем изменение exp временной метки

        # Явно задаем куки на инстансе клиента (современный подход HTTPX)
        test_client.cookies = {"client_refresh_token": client_refresh}

        # Обновляем сессию через клиентскую ручку без передачи аргумента cookies
        refresh_res = await test_client.post("/storefront-auth/refresh/")

        assert refresh_res.status_code == 200
        assert refresh_res.json()["access_token"] != first_access_token
        assert "client_refresh_token" in refresh_res.cookies

    async def test_client_refresh_fails_if_client_deactivated(
        self, test_client, db_session
    ):
        """
        Если Креатор забанил клиента в панели CRM, refresh_token клиента мгновенно аннулируется.
        """
        test_instance = Instances(title="Banning Store", active=True)
        db_session.add(test_instance)
        await db_session.commit()

        email = "fraud_buyer@example.com"
        client_user = await self._create_active_client(db_session, email, test_instance)

        login_data = {"username": email, "password": "CustomerPass123!"}
        await test_client.post("/storefront-auth/login/", data=login_data)
        client_refresh = test_client.cookies.get("client_refresh_token")

        # Симулируем бан клиента администратором CRM
        client_user.active = False
        await db_session.commit()

        # Явно задаем куки на инстансе клиента перед запросом
        test_client.cookies = {"client_refresh_token": client_refresh}

        response = await test_client.post("/storefront-auth/refresh/")

        assert response.status_code == 401


class TestClientLifecycle:

    @pytest.mark.asyncio
    async def test_client_full_auth_and_profile_retrieval_success(
        self,
        test_client,
        db_session,
        redis_email_db,
        user_factory,
        test_instance,
        monkeypatch,
    ):
        """
        Сквозной тест жизненного цикла CLIENT:
        1. Регистрация на витрине с привязкой к test_instance.uuid.
        2. Извлечение кода из Redis и подтверждение (активация).
        3. Логин на клиентском эндпоинте -> получение access_token.
        4. Запрос к эндпоинту /storefront-auth/me/ с токеном в заголовке -> проверка профиля.
        """
        # Мокаем отправку почты
        monkeypatch.setattr(
            "workers.email_tasks.send_email.send", lambda *args, **kwargs: None
        )

        # ----------------------------------------------------------------
        # ШАГ 1: Регистрация клиента
        # ----------------------------------------------------------------
        raw_user_data = user_factory()
        email = raw_user_data["email"]
        plain_password = raw_user_data[
            "password"
        ]  # Сохраняем чистый пароль для последующего логина

        register_payload = {
            "email": email,
            "password": plain_password,
            "name": "Иван Покупатель",
            "instance_id": str(test_instance.uuid),
        }

        reg_response = await test_client.post(
            "/storefront-auth/register/", json=register_payload
        )
        assert reg_response.status_code == 200
        assert reg_response.json()["status"] == "success"

        # ----------------------------------------------------------------
        # ШАГ 2: Верификация аккаунта (активация)
        # ----------------------------------------------------------------
        join_redis_key = generate_key(prefix=JOIN_PREFIX, sub=email)
        stored_code_bytes = await redis_email_db.get(join_redis_key)
        assert stored_code_bytes is not None

        verification_code = (
            stored_code_bytes.decode("utf-8")
            if isinstance(stored_code_bytes, bytes)
            else stored_code_bytes
        )

        verify_payload = {"email": email, "code": verification_code}
        verify_response = await test_client.post(
            "/storefront-auth/verify/", json=verify_payload
        )
        assert verify_response.status_code == 200

        # Убеждаемся, что в Postgres флаг active переключился в True
        result = await db_session.execute(select(Users).where(Users._email == email))
        db_user = result.scalar_one_or_none()
        assert db_user is not None
        assert db_user.active is True
        assert db_user.role == UserRole.CLIENT

        # ----------------------------------------------------------------
        # ШАГ 3: Авторизация (Логин на витрине)
        # ----------------------------------------------------------------
        # Передаем данные формы (OAuth2PasswordRequestForm использует x-www-form-urlencoded)
        login_data = {"username": email, "password": plain_password}
        login_response = await test_client.post(
            "/storefront-auth/login/", data=login_data
        )

        assert login_response.status_code == 200
        tokens_json = login_response.json()
        assert "access_token" in tokens_json

        access_token = tokens_json["access_token"]

        # Кэшируем куку рефреша, проверяем, что она изолированная (клиентская)
        assert "client_refresh_token" in test_client.cookies

        # ----------------------------------------------------------------
        # ШАГ 4: Обращение к новому эндпоинту /me/ с JWT токеном
        # ----------------------------------------------------------------
        # Перед отправкой добавляем токен в заголовки авторизации
        headers = {"Authorization": f"Bearer {access_token}"}

        profile_response = await test_client.get(
            "/storefront-auth/me/", headers=headers
        )

        assert profile_response.status_code == 200
        profile_data = profile_response.json()

        # Строгая проверка структуры ответа (согласно нашей Pydantic схеме ClientProfileResponse)
        assert profile_data["email"] == email
        assert profile_data["name"] == "Иван Покупатель"
        assert profile_data["instance_id"] == str(test_instance.uuid)
        assert profile_data["uuid"] == str(db_user.uuid)

        # Важнейшая проверка безопасности: хэш пароля или системные поля не должны утекать!
        assert "password" not in profile_data
        assert "_password" not in profile_data
        assert "role" not in profile_data
        assert "permissions" not in profile_data

    @pytest.mark.asyncio
    async def test_get_client_profile_unauthorized_fails(self, test_client):
        """
        Проверка защиты эндпоинта: запрос без заголовка Authorization
        или с невалидным токеном возвращает 401.
        """
        # ----------------------------------------------------------------
        # Вариант 1: Запрос вообще без заголовков (Токен отсутствует)
        # ----------------------------------------------------------------
        test_client.headers.clear()
        response = await test_client.get("/storefront-auth/me/")

        assert response.status_code == 401

        response_data = response.json()

        response_string = str(response_data).lower()
        assert any(
            word in response_string
            for word in ["token", "credential", "auth", "missing", "unauthorized"]
        )

        # ----------------------------------------------------------------
        # Вариант 2: Запрос с фейковым/неструктурированным токеном (Токен битый)
        # ----------------------------------------------------------------
        headers = {"Authorization": "Bearer absolute_gibberish_token_value"}
        response = await test_client.get("/storefront-auth/me/", headers=headers)

        assert response.status_code == 401

        response_data2 = response.json()
        response_string2 = str(response_data2)

        # Проверяем, что в структуре ответа вернулся либо наш профессиональный error_code,
        # либо текстовое описание того, что токен невалиден
        assert any(
            keyword in response_string2
            for keyword in [
                "INVALID_TOKEN",
                "invalid_token",
                "Invalid or expired",
                "not valid",
            ]
        )


class TestClientRBACIsolation:

    @pytest.fixture(scope="function")
    async def authenticated_client_headers(self, db_session, test_instance):
        """
        Фикстура, которая создает активного клиента в Postgres,
        генерирует для него валидный CLIENT access-токен и возвращает заголовки авторизации.
        """
        # 1. Создаем клиента, привязанного к тестовому инстансу магазина
        client_user = Users(
            role=UserRole.CLIENT, active=True, name="Тестовый Покупатель"
        )
        client_user.email = f"banned_visitor_{uuid.uuid4().hex[:6]}@example.com"
        client_user.password = "SecurePass123!"
        client_user.instance_id = test_instance.uuid

        db_session.add(client_user)
        await db_session.commit()
        await db_session.refresh(client_user)

        # 2. Генерируем access-токен, зашивая в payload роль CLIENT
        payload = {
            "sub": str(client_user.uuid),
            "role": UserRole.CLIENT.value,
            "type": "access",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=15),
        }
        token = encode_jwt(payload=payload)

        return {"Authorization": f"Bearer {token}"}

    @pytest.mark.asyncio
    async def test_client_cannot_access_trigger_evaluation(
        self, test_client, authenticated_client_headers, test_instance
    ):
        """Проверяем, что клиент не может вызвать летучие вычисления или создать триггеры."""
        fake_trigger_uuid = str(uuid.uuid4())
        payload = {"context_data": {"total_score": 100}}

        response = await test_client.post(
            f"/instances/{test_instance.uuid}/triggers/{fake_trigger_uuid}/evaluate",
            json=payload,
            headers=authenticated_client_headers,
        )

        # Ожидаем строгий отлуп 403 от гарда get_current_creator или get_current_admin
        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_client_cannot_modify_storefront_policies(
        self, test_client, authenticated_client_headers, test_instance
    ):
        """Проверяем, что клиент не имеет доступа к настройке масок и фильтров политик витрины."""
        fake_policy_id = str(uuid.uuid4())
        update_payload = {"read_mask": ["email", "phone"], "write_mask": ["phone"]}

        response = await test_client.patch(
            f"/instances/{test_instance.uuid}/storefront-configs/{fake_policy_id}",
            json=update_payload,
            headers=authenticated_client_headers,
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_client_cannot_crud_collections_and_templates(
        self, test_client, authenticated_client_headers, test_instance
    ):
        """Проверяем, что клиент не может создавать No-Code шаблоны (коллекции) в инстансе."""
        template_payload = {
            "name": "Хакерская Таблица",
            "schema": {"secret_field": {"type": "string"}},
        }

        response = await test_client.post(
            f"/instances/{test_instance.uuid}/templates",
            json=template_payload,
            headers=authenticated_client_headers,
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_client_cannot_manipulate_records_directly(
        self, test_client, authenticated_client_headers, test_instance
    ):
        """Проверяем, что клиент не может читать или писать записи напрямую через административный роутер документов."""
        fake_template_uuid = str(uuid.uuid4())

        # Пытаемся засидgroupить документ напрямую в обход публичной витрины
        response = await test_client.post(
            f"/instances/{test_instance.uuid}/templates/{fake_template_uuid}/notes",
            json={"data": {"title": "Контрабанда"}},
            headers=authenticated_client_headers,
        )

        assert response.status_code == 403

    @pytest.mark.asyncio
    async def test_client_cannot_alter_schema_columns(
        self, test_client, authenticated_client_headers, test_instance
    ):
        """Проверяем, что клиент не может изменять архитектуру таблиц (добавлять/менять колонки)."""
        fake_template_uuid = str(uuid.uuid4())
        column_payload = {
            "column_name": "injected_field",
            "field_meta": {"type": "string", "required": False},
        }

        # Попытка инъекции новой колонки в схему данных
        response = await test_client.post(
            f"/instances/{test_instance.uuid}/templates/{fake_template_uuid}/columns",
            json=column_payload,
            headers=authenticated_client_headers,
        )

        assert response.status_code == 403
