# triggers/tests/conftest.py

import pytest_asyncio
from uuid import uuid4
from database.db import get_db
from main import app
from users.models import UserPermissions, Users, Instances, UserRole
from jsonwebtoken.utils import encode_jwt
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
import config as cfg


@pytest_asyncio.fixture
def crm_template_factory(test_client, create_test_environment):
    """
    Фабрика для генерации изолированного окружения и создания шаблонов с динамической схемой.
    Поддерживает как классический формат "schema", так и плоский формат "schema_definition".
    """

    async def _create_template(
        name="Динамический шаблон", schema=None, flat_schema=None
    ):
        user_uuid, instance_uuid, headers = await create_test_environment()

        payload = {"name": name}
        if flat_schema is not None:
            payload["schema_definition"] = flat_schema
        else:
            payload["schema"] = (
                schema
                if schema is not None
                else {
                    "title": {"type": "string", "required": True},
                    "price": {"type": "number", "required": False},
                }
            )

        response = await test_client.post(
            f"/instances/{instance_uuid}/templates",
            json=payload,
            headers=headers,
        )
        assert response.status_code == 201
        tpl_data = response.json()
        template_uuid = (
            tpl_data.get("_id") or tpl_data.get("id") or tpl_data.get("uuid")
        )

        return {
            "instance_uuid": instance_uuid,
            "template_uuid": template_uuid,
            "headers": headers,
            "base_url": f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
        }

    return _create_template


@pytest_asyncio.fixture
async def setup_catalog_template(test_client, create_test_environment):
    """
    Разворачивает изолированное окружение инстанса и создает базовый
    шаблон "Товары" со строковым полем 'title' и числовым 'price'.
    """
    user_uuid, instance_uuid, headers = await create_test_environment()

    schema = {
        "title": {"type": "string", "required": True},
        "price": {"type": "number", "required": False},
    }

    response = await test_client.post(
        f"/instances/{instance_uuid}/templates",
        json={"name": "Товары", "schema": schema},
        headers=headers,
    )
    assert response.status_code == 201
    template_uuid = response.json()["_id"]

    return {
        "instance_uuid": instance_uuid,
        "template_uuid": template_uuid,
        "headers": headers,
        "base_url": f"/instances/{instance_uuid}/templates/{template_uuid}/notes",
    }


@pytest_asyncio.fixture
def employee_factory(db_session):
    """
    Фабрика для создания пользователя в БД Postgres с привязкой к инстансу
    и выдачей прав на конкретный инструмент (AppTool).
    """

    async def _create_employee(instance_uuid, tool_name: str):
        employee_uuid = uuid4()

        db_session.add(
            Users(
                uuid=employee_uuid,
                email=f"employee_{uuid4().hex[:6]}@test.com",
                hash_password="mock_password_hash_for_tests",
                role=UserRole.USER,
                active=True,
                instance_id=instance_uuid,
            )
        )
        db_session.add(
            UserPermissions(
                user_uuid=employee_uuid,
                allowed_tools=[tool_name],
            )
        )
        await db_session.commit()

        token = encode_jwt(payload={"sub": str(employee_uuid)})
        return {"Authorization": f"Bearer {token}"}

    return _create_employee


@pytest_asyncio.fixture
def crm_environment_factory(db_session):
    """
    Фабрика для генерации изолированного бизнес-пространства с Владельцем (CREATOR).
    Возвращает UUID инстанса, заголовки владельца и хелпер для добавления сотрудников.
    """

    async def _setup_env():
        instance_uuid = uuid4()
        creator_uuid = uuid4()

        db_session.add(
            Instances(
                uuid=instance_uuid,
                title=f"Бизнес Пространство {uuid4().hex[:4]}",
                active=True,
            )
        )

        db_session.add(
            Users(
                uuid=creator_uuid,
                name="Иван Владелец",
                email=f"creator_{uuid4().hex[:6]}@test.com",
                hash_password="mock_password_hash_for_tests",
                role=UserRole.CREATOR,
                active=True,
                instance_id=instance_uuid,
            )
        )
        await db_session.commit()

        creator_token = encode_jwt(payload={"sub": str(creator_uuid)})
        creator_headers = {"Authorization": f"Bearer {creator_token}"}

        async def add_employee(role: UserRole, allowed_tools: list):
            emp_uuid = uuid4()
            db_session.add(
                Users(
                    uuid=emp_uuid,
                    name="Сотрудник",
                    email=f"worker_{uuid4().hex[:6]}@test.com",
                    hash_password="mock_password_hash_for_tests",
                    role=role,
                    active=True,
                    instance_id=instance_uuid,
                )
            )
            db_session.add(
                UserPermissions(user_uuid=emp_uuid, allowed_tools=allowed_tools)
            )
            await db_session.commit()

            token = encode_jwt(payload={"sub": str(emp_uuid)})
            return {"Authorization": f"Bearer {token}"}

        return {
            "instance_uuid": instance_uuid,
            "creator_headers": creator_headers,
            "add_employee": add_employee,
        }

    return _setup_env
