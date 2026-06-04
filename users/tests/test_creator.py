# users/tests/test_creator.py

import pytest
from uuid import uuid4
from users.models import UserPermissions, Users, UserRole, Instances
from redisdb.utils import generate_key
from config import USER_INVITE_PREFIX
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.future import select
from sqlalchemy.orm import joinedload


@pytest.mark.asyncio
class TestCreatorInviteManagement:

    async def test_invite_user_success(self, auth_client, db_session, redis_email_db):
        """
        Успешный инвайт сотрудника Креатором.
        Проверяет код ответа и фактическое наличие записи инстанса в Redis.
        """
        client, creator_user = auth_client

        # 1. Создаем тестовый инстанс в БД и жестко привязываем его к юзеру
        test_instance = Instances(uuid=uuid4(), title="Test Studio", active=True)
        db_session.add(test_instance)

        creator_user.role = UserRole.CREATOR
        creator_user.instance_id = test_instance.uuid

        # Сливаем изменения в базу, чтобы зависимость внутри эндпоинта их увидела
        await db_session.commit()

        target_email = "employee_test@example.com"
        payload = {"email": target_email}

        # 2. Делаем запрос
        response = await client.post("/creator/invite-user/", json=payload)

        assert response.status_code == 200
        assert response.json()["status"] == "success"

        # 3. Проверяем Redis через правильную фикстуру redis_email_db
        redis_key = generate_key(prefix=USER_INVITE_PREFIX, sub=target_email)
        redis_value_bytes = await redis_email_db.get(redis_key)

        assert redis_value_bytes is not None

        redis_value = (
            redis_value_bytes.decode("utf-8")
            if isinstance(redis_value_bytes, bytes)
            else redis_value_bytes
        )

        assert redis_value == str(creator_user.instance_id)

    async def test_invite_user_already_registered(
        self, auth_client, db_session, user_factory
    ):
        """
        Ошибка 400, если Креатор пытается пригласить email,
        который уже существует в PostgreSQL.
        """
        client, creator_user = auth_client

        # Создаем инстанс для Креатора и сохраняем его в БД
        test_instance = Instances(uuid=uuid4(), title="Test Studio", active=True)
        db_session.add(test_instance)

        creator_user.role = UserRole.CREATOR
        creator_user.instance_id = test_instance.uuid
        await db_session.commit()

        # Создаем в базе данных "уже зарегистрированного" пользователя через фабрику
        existing_user_data = user_factory()
        already_registered_email = existing_user_data["email"]

        new_user = Users(
            name=existing_user_data["name"],
            email=already_registered_email,
            instance_id=creator_user.instance_id,
            active=True,
        )
        new_user.password = "SomePassword123!"
        db_session.add(new_user)
        await db_session.commit()

        # Пробуем выслать инвайт на этот же email
        payload = {"email": already_registered_email}
        response = await client.post("/creator/invite-user/", json=payload)

        assert response.status_code == 400

    async def test_invite_user_creator_without_instance(self, auth_client, db_session):
        """
        Ошибка 400, если у самого Креатора в профиле почему-то отсутствует инстанс.
        """
        client, creator_user = auth_client

        # 🔥 ГАРАНТИРУЕМ, что пользователь является Креатором,
        # иначе новая зависимость get_current_creator отсечет его по 403 ошибке!
        creator_user.role = UserRole.CREATOR

        # Гарантируем, что инстанса нет в базе данных для этого юзера
        creator_user.instance_id = None

        await db_session.commit()
        await db_session.refresh(creator_user)  # Обновляем состояние объекта

        payload = {"email": "random_guy@example.com"}
        response = await client.post("/creator/invite-user/", json=payload)

        assert response.status_code == 400

    async def test_promote_to_creator_success(
        self, auth_client, db_session, user_factory
    ):
        """
        Успешное повышение обычного USER до роли CREATOR.
        Проверяет изменение роли в БД и выдачу прав 'all'.
        """
        client, creator_user = auth_client

        # 1. Создаем тестовое окружение (инстанс и привязываем Креатора)
        test_instance = Instances(
            uuid=uuid4(), title="Main Production Studio", active=True
        )
        db_session.add(test_instance)

        creator_user.role = UserRole.CREATOR
        creator_user.instance_id = test_instance.uuid
        await db_session.commit()

        # 2. Создаем обычного пользователя (Сотрудника) внутри этого же инстанса
        emp_data = user_factory()
        employee = Users(
            uuid=uuid4(),
            name=emp_data["name"],
            email=emp_data["email"],
            instance_id=test_instance.uuid,
            role=UserRole.USER,
            active=True,
        )
        employee.password = "EmployeePass123!"
        db_session.add(employee)
        await db_session.commit()

        # 3. Делаем запрос на повышение
        payload = {"user_uuid": str(employee.uuid)}
        response = await client.post("/creator/promote-to-creator/", json=payload)

        # Проверяем ответ эндпоинта
        assert response.status_code == 200
        assert response.json()["status"] == "success"

        # Создаем временную чистую сессию, чтобы прочитать свежий коммит из эндпоинта
        check_engine = create_async_engine(db_session.bind.url, echo=False)
        CheckSession = async_sessionmaker(check_engine, expire_on_commit=False)

        async with CheckSession() as check_session:
            stmt = (
                select(Users)
                .where(Users.uuid == employee.uuid)
                .options(joinedload(Users.permissions))
            )
            res = await check_session.execute(stmt)
            updated_user = res.scalar_one_or_none()

            assert updated_user is not None
            assert updated_user.role == UserRole.CREATOR

            assert updated_user.permissions is not None
            assert updated_user.permissions.allowed_tools == ["all"]

        await check_engine.dispose()

    async def test_promote_to_creator_foreign_instance_forbidden(
        self, auth_client, db_session, user_factory
    ):
        """
        Ошибка 403, если Креатор пытается повысить пользователя,
        который принадлежит чужому инстансу.
        """
        client, creator_user = auth_client

        # 1. Инстанс А для нашего Креатора
        instance_a = Instances(uuid=uuid4(), title="Studio A", active=True)
        db_session.add(instance_a)
        creator_user.role = UserRole.CREATOR
        creator_user.instance_id = instance_a.uuid

        # 2. Инстанс Б (чужой)
        instance_b = Instances(uuid=uuid4(), title="Studio B", active=True)
        db_session.add(instance_b)
        await db_session.commit()

        # 3. Создаем пользователя в инстансе Б
        emp_data = user_factory()
        foreign_employee = Users(
            uuid=uuid4(),
            name=emp_data["name"],
            email=emp_data["email"],
            instance_id=instance_b.uuid,  # Чужой инстанс!
            role=UserRole.USER,
            active=True,
        )
        foreign_employee.password = "ForeignPass123!"
        db_session.add(foreign_employee)
        await db_session.commit()

        # 4. Пытаемся повысить чужого юзера
        payload = {"user_uuid": str(foreign_employee.uuid)}
        response = await client.post("/creator/promote-to-creator/", json=payload)

        assert response.status_code == 403

    async def test_promote_to_creator_already_creator(
        self, auth_client, db_session, user_factory
    ):
        """
        Ошибка 400, если целевой пользователь уже является Креатором.
        """
        client, creator_user = auth_client

        test_instance = Instances(uuid=uuid4(), title="Shared Studio", active=True)
        db_session.add(test_instance)

        creator_user.role = UserRole.CREATOR
        creator_user.instance_id = test_instance.uuid
        await db_session.commit()

        # Создаем пользователя, который изначально уже CREATOR в этом же инстансе
        another_creator_data = user_factory()
        another_creator = Users(
            uuid=uuid4(),
            name=another_creator_data["name"],
            email=another_creator_data["email"],
            instance_id=test_instance.uuid,
            role=UserRole.CREATOR,  # Уже креатор!
            active=True,
        )
        another_creator.password = "CreatorPass123!"
        db_session.add(another_creator)
        await db_session.commit()

        # Пытаемся повторно повысить
        payload = {"user_uuid": str(another_creator.uuid)}
        response = await client.post("/creator/promote-to-creator/", json=payload)

        assert response.status_code == 400

    async def test_demote_to_user_success(self, auth_client, db_session, user_factory):
        """
        Успешное понижение пользователя со статуса CREATOR до USER.
        Проверяет смену роли и очистку массива прав.
        """
        client, creator_user = auth_client

        # 1. Привязываем текущего креатора к инстансу
        test_instance = Instances(
            uuid=uuid4(), title="Demote Testing Space", active=True
        )
        db_session.add(test_instance)

        creator_user.role = UserRole.CREATOR
        creator_user.instance_id = test_instance.uuid
        await db_session.commit()

        # 2. Создаем ВТОРОГО креатора в этом же инстансе, которого будем понижать
        target_data = user_factory()
        target_user = Users(
            uuid=uuid4(),
            name=target_data["name"],
            email=target_data["email"],
            instance_id=test_instance.uuid,
            role=UserRole.CREATOR,  # Он изначально Креатор
            active=True,
        )
        target_user.password = "SuperPass123!"
        db_session.add(target_user)

        # Даем ему права "all"
        target_perms = UserPermissions(
            user_uuid=target_user.uuid, allowed_tools=["all"]
        )
        db_session.add(target_perms)
        await db_session.commit()

        # 3. Делаем запрос на понижение
        payload = {"user_uuid": str(target_user.uuid)}
        response = await client.post("/creator/demote-to-user/", json=payload)

        assert response.status_code == 200
        assert response.json()["status"] == "success"

        # 4. Проверяем базу через чистую транзакцию
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from sqlalchemy.future import select
        from sqlalchemy.orm import joinedload

        check_engine = create_async_engine(db_session.bind.url, echo=False)
        CheckSession = async_sessionmaker(check_engine, expire_on_commit=False)

        async with CheckSession() as check_session:
            stmt = (
                select(Users)
                .where(Users.uuid == target_user.uuid)
                .options(joinedload(Users.permissions))
            )
            res = await check_session.execute(stmt)
            updated_user = res.scalar_one_or_none()

            assert updated_user is not None
            assert updated_user.role == UserRole.USER  # Стал обычным юзером
            assert updated_user.permissions is not None
            assert updated_user.permissions.allowed_tools == []  # Права очистились!

        await check_engine.dispose()

    async def test_demote_to_user_already_regular_user(
        self, auth_client, db_session, user_factory
    ):
        """
        Ошибка 400 при попытке понизить того, кто и так является USER.
        """
        client, creator_user = auth_client

        test_instance = Instances(
            uuid=uuid4(), title="Demote Testing Space", active=True
        )
        db_session.add(test_instance)
        creator_user.role = UserRole.CREATOR
        creator_user.instance_id = test_instance.uuid
        await db_session.commit()

        # Создаем обычного USER
        emp_data = user_factory()
        regular_user = Users(
            uuid=uuid4(),
            name=emp_data["name"],
            email=emp_data["email"],
            instance_id=test_instance.uuid,
            role=UserRole.USER,  # Он уже USER
            active=True,
        )
        regular_user.password = "Pass123!"
        db_session.add(regular_user)
        await db_session.commit()

        # Пытаемся понизить
        payload = {"user_uuid": str(regular_user.uuid)}
        response = await client.post("/creator/demote-to-user/", json=payload)

        assert response.status_code == 400

    async def test_demote_myself_bad_request(self, auth_client, db_session):
        """
        Ошибка 400 при попытке Креатора понизить самого себя.
        """
        client, creator_user = auth_client

        test_instance = Instances(
            uuid=uuid4(), title="Demote Testing Space", active=True
        )
        db_session.add(test_instance)
        creator_user.role = UserRole.CREATOR
        creator_user.instance_id = test_instance.uuid
        await db_session.commit()

        # Креатор передает в payload свой собственный UUID
        payload = {"user_uuid": str(creator_user.uuid)}
        response = await client.post("/creator/demote-to-user/", json=payload)

        assert response.status_code == 400

    async def test_update_permissions_success(
        self, auth_client, db_session, user_factory
    ):
        """
        Успешное обновление прав для USER Креатором (выдача notes и workflow).
        """
        client, creator_user = auth_client

        # Настраиваем Креатора и инстанс
        test_instance = Instances(uuid=uuid4(), title="Permission Lab", active=True)
        db_session.add(test_instance)
        creator_user.role = UserRole.CREATOR
        creator_user.instance_id = test_instance.uuid
        await db_session.commit()

        # Создаем обычного USER
        emp_data = user_factory()
        employee = Users(
            uuid=uuid4(),
            name=emp_data["name"],
            email=emp_data["email"],
            instance_id=test_instance.uuid,
            role=UserRole.USER,
            active=True,
        )
        employee.password = "EmpPass123!"
        db_session.add(employee)
        await db_session.commit()

        # Отправляем запрос на выдачу прав для 'notes' и 'workflow'
        payload = {
            "user_uuid": str(employee.uuid),
            "allowed_tools": ["notes", "workflow"],
        }
        response = await client.post("/creator/update-permissions/", json=payload)

        assert response.status_code == 200
        assert response.json()["status"] == "success"
        assert set(response.json()["allowed_tools"]) == {"notes", "workflow"}

        # Проверяем в базе через изолированную чистую сессию
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from sqlalchemy.future import select
        from sqlalchemy.orm import joinedload

        check_engine = create_async_engine(db_session.bind.url, echo=False)
        CheckSession = async_sessionmaker(check_engine, expire_on_commit=False)

        async with CheckSession() as check_session:
            stmt = (
                select(Users)
                .where(Users.uuid == employee.uuid)
                .options(joinedload(Users.permissions))
            )
            res = await check_session.execute(stmt)
            updated_user = res.scalar_one_or_none()

            assert updated_user is not None
            assert updated_user.permissions is not None
            # Сортируем списки для надежного сравнения массивов
            assert sorted(updated_user.permissions.allowed_tools) == sorted(
                ["notes", "workflow"]
            )

        await check_engine.dispose()

    async def test_update_permissions_foreign_instance_forbidden(
        self, auth_client, db_session, user_factory
    ):
        """
        Ошибка 403, если Креатор пытается отредактировать права пользователя из чужого инстанса.
        """
        client, creator_user = auth_client

        # Инстанс Креатора
        instance_mine = Instances(uuid=uuid4(), title="My Territory", active=True)
        db_session.add(instance_mine)
        creator_user.role = UserRole.CREATOR
        creator_user.instance_id = instance_mine.uuid

        # Чужой инстанс
        instance_foreign = Instances(
            uuid=uuid4(), title="Foreign Territory", active=True
        )
        db_session.add(instance_foreign)
        await db_session.commit()

        # Чужой пользователь
        emp_data = user_factory()
        foreign_user = Users(
            uuid=uuid4(),
            name=emp_data["name"],
            email=emp_data["email"],
            instance_id=instance_foreign.uuid,  # Чужая привязка
            role=UserRole.USER,
            active=True,
        )
        foreign_user.password = "PassXYZ123!"
        db_session.add(foreign_user)
        await db_session.commit()

        # Пытаемся поменять ему права
        payload = {"user_uuid": str(foreign_user.uuid), "allowed_tools": ["tables"]}
        response = await client.post("/creator/update-permissions/", json=payload)

        assert response.status_code == 403

    async def test_deactivate_user_success(self, auth_client, db_session, user_factory):
        """
        Успешная деактивация (бан) обычного USER Креатором.
        """
        client, creator_user = auth_client

        # Настраиваем инстанс и Креатора
        test_instance = Instances(uuid=uuid4(), title="Security Lab", active=True)
        db_session.add(test_instance)
        creator_user.role = UserRole.CREATOR
        creator_user.instance_id = test_instance.uuid
        await db_session.commit()

        # Создаем активного пользователя (жертву) в том же инстансе
        emp_data = user_factory()
        employee = Users(
            uuid=uuid4(),
            name=emp_data["name"],
            email=emp_data["email"],
            instance_id=test_instance.uuid,
            role=UserRole.USER,
            active=True,  # Он активен
        )
        employee.password = "ActivePass123!"
        db_session.add(employee)
        await db_session.commit()

        # Отправляем запрос на бан
        payload = {"user_uuid": str(employee.uuid)}
        response = await client.post("/creator/deactivate-user/", json=payload)

        assert response.status_code == 200
        assert response.json()["status"] == "success"

        # Проверяем изменения в базе данных через чистую транзакцию
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from sqlalchemy.future import select

        check_engine = create_async_engine(db_session.bind.url, echo=False)
        CheckSession = async_sessionmaker(check_engine, expire_on_commit=False)

        async with CheckSession() as check_session:
            stmt = select(Users).where(Users.uuid == employee.uuid)
            res = await check_session.execute(stmt)
            updated_user = res.scalar_one_or_none()

            assert updated_user is not None
            assert updated_user.active is False  # Пользователь успешно забанен!

        await check_engine.dispose()

    async def test_deactivate_myself_bad_request(self, auth_client, db_session):
        """
        Ошибка 400 при попытке Креатора забанить самого себя.
        """
        client, creator_user = auth_client

        test_instance = Instances(uuid=uuid4(), title="Security Lab", active=True)
        db_session.add(test_instance)
        creator_user.role = UserRole.CREATOR
        creator_user.instance_id = test_instance.uuid
        await db_session.commit()

        # Передаем свой же UUID
        payload = {"user_uuid": str(creator_user.uuid)}
        response = await client.post("/creator/deactivate-user/", json=payload)

        assert response.status_code == 400

    async def test_deactivate_user_foreign_instance_forbidden(
        self, auth_client, db_session, user_factory
    ):
        """
        Ошибка 403 при попытке забанить пользователя из чужого инстанса.
        """
        client, creator_user = auth_client

        # Наш инстанс
        instance_mine = Instances(uuid=uuid4(), title="My Zone", active=True)
        db_session.add(instance_mine)
        creator_user.role = UserRole.CREATOR
        creator_user.instance_id = instance_mine.uuid

        # Чужой инстанс
        instance_foreign = Instances(uuid=uuid4(), title="Alien Zone", active=True)
        db_session.add(instance_foreign)
        await db_session.commit()

        # Чужой активный юзер
        emp_data = user_factory()
        foreign_user = Users(
            uuid=uuid4(),
            name=emp_data["name"],
            email=emp_data["email"],
            instance_id=instance_foreign.uuid,
            role=UserRole.USER,
            active=True,
        )
        foreign_user.password = "Pass123!"
        db_session.add(foreign_user)
        await db_session.commit()

        # Пытаемся забанить чужого юзера
        payload = {"user_uuid": str(foreign_user.uuid)}
        response = await client.post("/creator/deactivate-user/", json=payload)

        assert response.status_code == 403

    async def test_deactivate_user_by_regular_user_forbidden(
        self, auth_client, db_session, user_factory
    ):
        """
        Ошибка 403, если обычный USER пытается деактивировать другого пользователя.
        Проверяет, что у роли USER нет прав на деактивацию.
        """
        client, regular_user = auth_client

        # 1. Настраиваем окружение (создаем общий инстанс)
        test_instance = Instances(uuid=uuid4(), title="Regular Workspace", active=True)
        db_session.add(test_instance)

        # Текущий авторизованный пользователь является обычным USER
        regular_user.role = UserRole.USER
        regular_user.instance_id = test_instance.uuid
        await db_session.commit()

        # 2. Создаем жертву (другого сотрудника в этом же инстансе)
        target_data = user_factory()
        target_employee = Users(
            uuid=uuid4(),
            name=target_data["name"],
            email=target_data["email"],
            instance_id=test_instance.uuid,
            role=UserRole.USER,
            active=True,
        )
        target_employee.password = "TargetPass123!"
        db_session.add(target_employee)
        await db_session.commit()

        # 3. Обычный пользователь пытается отправить запрос на деактивацию
        payload = {"user_uuid": str(target_employee.uuid)}
        response = await client.post("/creator/deactivate-user/", json=payload)

        # 4. Проверяем, что система жестко отбила запрос по правам доступа
        assert response.status_code == 403

        # 5. Дополнительно проверяем базу данных через чистую транзакцию,
        # чтобы убедиться, что статус пользователя действительно НЕ изменился.
        from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
        from sqlalchemy.future import select

        check_engine = create_async_engine(db_session.bind.url, echo=False)
        CheckSession = async_sessionmaker(check_engine, expire_on_commit=False)

        async with CheckSession() as check_session:
            stmt = select(Users).where(Users.uuid == target_employee.uuid)
            res = await check_session.execute(stmt)
            db_user = res.scalar_one_or_none()

            assert db_user is not None
            assert db_user.active is True  # Он остался активным, бан не прошел

        await check_engine.dispose()
