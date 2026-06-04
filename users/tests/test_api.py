# users/tests/test_api.py

import pytest
from sqlalchemy import select
from users.models import Users, UserRole, Instances
from redisdb.utils import generate_key
from config import INVITE_PREFIX, JOIN_PREFIX
from datetime import datetime, timezone, timedelta
from jsonwebtoken.utils import encode_jwt


class TestUsersAuth:

    @pytest.mark.asyncio
    async def test_creator_registration_flow_success(
        self, test_client, db_session, redis_email_db, user_factory, monkeypatch
    ):
        """
        Тест полного цикла регистрации CREATOR:
        1. /register/ -> Создается неактивный юзер, генерируется код в Redis.
        2. /verify-registration/ -> Юзер активируется, данные удаляются из Redis.
        """
        # Мокаем dramatiq-таску отправки писем, чтобы тест не падал на брокере
        mock_send_called = False

        def mock_send(*args, **kwargs):
            nonlocal mock_send_called
            mock_send_called = True

        monkeypatch.setattr("workers.email_tasks.send_email.send", mock_send)

        # 1. Готовим инстанс в PostgreSQL
        test_instance = Instances(title="Test Tech Studio", active=True)
        db_session.add(test_instance)
        await db_session.commit()
        await db_session.refresh(test_instance)

        # 2. Готовим данные пользователя
        user_data = user_factory()
        email = user_data["email"]

        # Имитируем админский инвайт
        invite_redis_key = generate_key(prefix=INVITE_PREFIX, sub=email)
        await redis_email_db.setex(invite_redis_key, 3600, str(test_instance.uuid))

        # 3. Шаг первый: отправка формы регистрации
        response = await test_client.post("/auth/register/", json=user_data)

        assert response.status_code == 201
        json_resp = response.json()
        assert json_resp["status"] == "success"
        assert "Please check your email" in json_resp["message"]
        assert (
            mock_send_called is True
        )  # Проверяем, что таска отправки добавилась в очередь

        # Проверяем, что пользователь создан в БД, но еще НЕ активен
        result = await db_session.execute(
            select(Users)
            .where(Users._email == email)
            .execution_options(populate_existing=True)
        )
        user = result.scalar_one_or_none()
        assert user is not None
        assert user.active is False

        # 4. Шаг второй: достаем сгенерированный код верификации из Redis
        join_redis_key = generate_key(prefix=JOIN_PREFIX, sub=email)
        stored_data_bytes = await redis_email_db.get(join_redis_key)
        assert stored_data_bytes is not None

        stored_data = (
            stored_data_bytes.decode("utf-8")
            if isinstance(stored_data_bytes, bytes)
            else stored_data_bytes
        )
        verification_code, saved_invite_key = stored_data.split(":", 1)

        # 5. Шаг третий: отправляем код на эндпоинт подтверждения
        verify_payload = {"email": email, "code": verification_code}
        verify_response = await test_client.post(
            "/auth/verify-registration/", json=verify_payload
        )

        assert verify_response.status_code == 200
        verify_json = verify_response.json()
        assert verify_json["status"] == "success"
        assert verify_json["user"]["role"] == UserRole.CREATOR.value
        assert verify_json["user"]["instance_id"] == str(test_instance.uuid)

        # 6. Проверяем финальный статус в PostgreSQL (теперь active должен быть True)
        await db_session.refresh(user)
        assert user.active is True

        # 7. Проверяем, что Redis полностью чист (и код, и оригинальный инвайт удалены)
        assert not await redis_email_db.exists(join_redis_key)
        assert not await redis_email_db.exists(invite_redis_key)

    @pytest.mark.asyncio
    async def test_registration_fails_without_invite(
        self, test_client, db_session, user_factory
    ):
        """
        Если инвайта в Redis нет, система должна отклонить регистрацию.
        """
        user_data = user_factory()

        response = await test_client.post("/auth/register/", json=user_data)

        assert response.status_code == 403

        # Проверяем, что в базу никто не записался
        result = await db_session.execute(
            select(Users).where(Users._email == user_data["email"])
        )
        assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_creator_registration_fails_with_corrupted_redis_data(
        self, test_client, redis_email_db, user_factory
    ):
        """
        Если данные инвайта в Redis не являются валидным UUID, система должна выбросить 500 ошибку.
        """
        user_data = user_factory()
        email = user_data["email"]

        redis_key = generate_key(prefix=INVITE_PREFIX, sub=email)
        await redis_email_db.setex(redis_key, 3600, "corrupted_non_uuid_string")

        response = await test_client.post("/auth/register/", json=user_data)

        assert response.status_code == 500

    @pytest.mark.asyncio
    async def test_verify_registration_fails_with_invalid_code(
        self, test_client, db_session, redis_email_db, user_factory, monkeypatch
    ):
        """
        Если пользователь вводит неверный 6-значный код, система возвращает 400 Bad Request
        и пользователь остается неактивным.
        """
        monkeypatch.setattr(
            "workers.email_tasks.send_email.send", lambda *args, **kwargs: None
        )

        test_instance = Instances(title="Test Studio 2", active=True)
        db_session.add(test_instance)
        await db_session.commit()

        user_data = user_factory()
        email = user_data["email"]

        invite_redis_key = generate_key(prefix=INVITE_PREFIX, sub=email)
        await redis_email_db.setex(invite_redis_key, 3600, str(test_instance.uuid))

        # Регистрируем аккаунт
        await test_client.post("/auth/register/", json=user_data)

        # Пытаемся отправить заведомо неверный код верификации
        verify_payload = {"email": email, "code": "000000"}  # Неверный код
        response = await test_client.post(
            "/auth/verify-registration/", json=verify_payload
        )

        assert response.status_code == 400

        # Проверяем, что пользователь в базе остался неактивным
        result = await db_session.execute(
            select(Users)
            .where(Users._email == email)
            .execution_options(populate_existing=True)
        )
        user = result.scalar_one_or_none()
        assert user.active is False

    @pytest.mark.asyncio
    async def test_resend_verification_code_success(
        self, test_client, db_session, redis_email_db, user_factory, monkeypatch
    ):
        """
        Тест успешного повторного запроса кода:
        1. Регистрируем пользователя (код создается в Redis).
        2. Имитируем, что прошло время (в тесте просто сбиваем TTL старого ключа, чтобы пройти rate-limit).
        3. Вызываем /resend-code/ -> проверяем статус 200 и генерацию нового кода.
        """
        mock_send_count = 0

        def mock_send(*args, **kwargs):
            nonlocal mock_send_count
            mock_send_count += 1

        monkeypatch.setattr("workers.email_tasks.send_email.send", mock_send)

        # Создаем инстанс и инвайт
        test_instance = Instances(title="Resend Test Studio", active=True)
        db_session.add(test_instance)
        await db_session.commit()

        user_data = user_factory()
        email = user_data["email"]

        invite_redis_key = generate_key(prefix=INVITE_PREFIX, sub=email)
        await redis_email_db.setex(invite_redis_key, 3600, str(test_instance.uuid))

        # Шаг 1: Первая регистрация (первая отправка письма)
        await test_client.post("/auth/register/", json=user_data)
        assert mock_send_count == 1

        # Извлекаем первый сгенерированный код
        join_redis_key = generate_key(prefix=JOIN_PREFIX, sub=email)
        first_data = await redis_email_db.get(join_redis_key)

        # Шаг 2: Обходим rate-limit. Имитируем, что код лежал в Redis долго (ставим TTL меньше 840)
        await redis_email_db.expire(join_redis_key, 800)

        # Шаг 3: Делаем запрос на повторную отправку
        resend_payload = {"email": email}
        response = await test_client.post("/auth/resend-code/", json=resend_payload)

        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert mock_send_count == 2  # Письмо должно уйти второй раз

        # Шаг 4: Проверяем, что код в Redis обновился
        second_data = await redis_email_db.get(join_redis_key)

        assert second_data != first_data  # Новый код должен отличаться от старого

    @pytest.mark.asyncio
    async def test_resend_verification_code_rate_limit(
        self, test_client, db_session, redis_email_db, user_factory, monkeypatch
    ):
        """
        Проверка защиты от спама: если повторный запрос отправлен слишком быстро
        (TTL ключа в Redis все еще > 840), эндпоинт должен вернуть 429 Too Many Requests.
        """
        monkeypatch.setattr(
            "workers.email_tasks.send_email.send", lambda *args, **kwargs: None
        )

        test_instance = Instances(title="Rate Limit Studio", active=True)
        db_session.add(test_instance)
        await db_session.commit()

        user_data = user_factory()
        email = user_data["email"]

        invite_redis_key = generate_key(prefix=INVITE_PREFIX, sub=email)
        await redis_email_db.setex(invite_redis_key, 3600, str(test_instance.uuid))

        # Регистрируем первый раз
        await test_client.post("/auth/register/", json=user_data)

        # Сразу же (без изменения TTL) шлем повторный запрос
        resend_payload = {"email": email}
        response = await test_client.post("/auth/resend-code/", json=resend_payload)

        # Ожидаем отлуп по лимиту запросов
        assert response.status_code == 429

    @pytest.mark.asyncio
    async def test_resend_verification_code_user_not_found(self, test_client):
        """
        Если аккаунта с такой почтой нет в базе данных, код генерироваться не должен.
        Возвращаем 404 Not Found.
        """
        resend_payload = {"email": "ghost_user_999@example.com"}
        response = await test_client.post("/auth/resend-code/", json=resend_payload)

        assert response.status_code == 404


@pytest.mark.asyncio
class TestTokenRefreshFlow:

    async def _create_active_user(
        self, db_session, email: str, role: UserRole
    ) -> Users:
        """Вспомогательный метод для создания активного пользователя."""
        user = Users(role=role, active=True)
        user.email = email
        user.password = "SecurePass123!"
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        return user

    async def test_login_returns_access_token_and_sets_refresh_cookie(
        self, test_client, db_session
    ):
        """Проверяет успешный логин и наличие флагов у куки."""
        email = "refresh_tester@example.com"
        await self._create_active_user(db_session, email, UserRole.USER)

        login_data = {"username": email, "password": "SecurePass123!"}
        response = await test_client.post("/auth/login/", data=login_data)

        assert response.status_code == 200
        assert "access_token" in response.json()

        # Проверяем заголовки напрямую, чтобы не зависеть от багов самого клиента
        set_cookie_header = response.headers.get("set-cookie")
        assert set_cookie_header is not None
        assert "refresh_token=" in set_cookie_header
        assert "HttpOnly" in set_cookie_header

    async def test_refresh_tokens_success_flow(self, test_client, db_session):
        """Успешный цикл обновления токенов с явной передачей куки."""
        import asyncio

        email = "cycle_tester@example.com"
        await self._create_active_user(db_session, email, UserRole.USER)

        # 1. Логинимся
        login_data = {"username": email, "password": "SecurePass123!"}
        login_res = await test_client.post("/auth/login/", data=login_data)
        assert login_res.status_code == 200

        first_access_token = login_res.json()["access_token"]
        refresh_token = test_client.cookies.get("refresh_token")
        assert refresh_token is not None

        # Очищаем заголовки авторизации
        test_client.headers.clear()

        # 🔥 Ждем чуть больше 1 секунды, чтобы exp у нового токена гарантированно изменился
        await asyncio.sleep(1.05)

        # 2. Делаем запрос на обновление
        refresh_res = await test_client.post(
            "/auth/refresh/", cookies={"refresh_token": refresh_token}
        )

        assert refresh_res.status_code == 200

        refresh_json = refresh_res.json()

        # Теперь этот ассерт выполнится успешно, так как exp изменился на 1 секунду!
        assert refresh_json["access_token"] != first_access_token
        assert (
            "refresh_token" in refresh_res.cookies
            or "set-cookie" in refresh_res.headers
        )

    async def test_refresh_fails_with_missing_cookie(self, test_client):
        """Попытка обновить токен без куки."""
        test_client.cookies.clear()

        response = await test_client.post("/auth/refresh/", cookies={})

        assert response.status_code == 401

    async def test_refresh_fails_with_access_token_in_cookie_protection(
        self, test_client, db_session
    ):
        """Защита от подмены: access_token вместо refresh_token в куках."""
        email = "hack_tester@example.com"
        user = await self._create_active_user(db_session, email, UserRole.USER)

        bad_payload = {
            "sub": str(user.uuid),
            "type": "access",
            "exp": datetime.now(timezone.utc) + timedelta(minutes=30),
        }
        fake_cookie_token = encode_jwt(payload=bad_payload)

        response = await test_client.post(
            "/auth/refresh/", cookies={"refresh_token": fake_cookie_token}
        )

        assert response.status_code == 401

    async def test_refresh_fails_if_user_deactivated_in_meantime(
        self, test_client, db_session
    ):
        """Если пользователя деактивировали, refresh_token больше не работает."""
        email = "banned_tester@example.com"
        user = await self._create_active_user(db_session, email, UserRole.USER)

        login_data = {"username": email, "password": "SecurePass123!"}
        await test_client.post("/auth/login/", data=login_data)
        refresh_token = test_client.cookies.get("refresh_token")

        # Баним пользователя в БД
        user.active = False
        await db_session.commit()

        response = await test_client.post(
            "/auth/refresh/", cookies={"refresh_token": refresh_token}
        )

        assert response.status_code == 401
