# core/tests/test_basic_fields.py

import pytest
from uuid import uuid4
from users.models import AppTools, UserRole


@pytest.mark.asyncio
async def test_record_lifecycle(test_client, setup_catalog_template):
    """
    Тест проверяет полный цикл жизни записи в No-code таблице:
    создание записи, чтение списка с пагинацией и PATCH-обновление с контролем версий.
    """
    ctx = setup_catalog_template
    url, headers = ctx["base_url"], ctx["headers"]

    create_resp = await test_client.post(
        url, json={"data": {"title": "Ноутбук", "price": 1000}}, headers=headers
    )
    assert create_resp.status_code == 201
    record = create_resp.json()
    record_uuid = record["_id"]
    assert record["data"]["title"] == "Ноутбук"

    get_resp = await test_client.get(url, headers=headers)
    assert get_resp.status_code == 200
    page = get_resp.json()
    assert page["total"] == 1 and page["results"][0]["_id"] == record_uuid

    patch_resp = await test_client.patch(
        f"{url}/{record_uuid}",
        json={"data": {"title": "Ноутбук Pro", "price": 1500}},
        headers=headers,
    )
    assert patch_resp.status_code == 200
    updated_record = patch_resp.json()
    assert updated_record["data"]["title"] == "Ноутбук Pro"
    assert updated_record["version"] == 2


@pytest.mark.asyncio
async def test_create_record_validation_error(test_client, crm_template_factory):
    """
    Проверка валидации: отправка некорректного типа данных (string вместо number)
    должна приводить к ошибке 422 Unprocessable Entity.
    """
    custom_schema = {"count": {"type": "number", "required": True}}
    ctx = await crm_template_factory(schema=custom_schema)

    response = await test_client.post(
        ctx["base_url"],
        json={"data": {"count": "это не число"}},
        headers=ctx["headers"],
    )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_record_missing_required(test_client, crm_template_factory):
    """
    Проверка отсутствия обязательного поля: отправка пустого payload
    при "required": True должна приводить к ошибке 422.
    """
    ctx = await crm_template_factory(
        schema={"name": {"type": "string", "required": True}}
    )
    response = await test_client.post(
        ctx["base_url"], json={"data": {}}, headers=ctx["headers"]
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_update_record_not_found(test_client, crm_template_factory):
    """
    Проверка 404 при обновлении несуществующей записи.
    """
    ctx = await crm_template_factory(
        schema={"name": {"type": "string", "required": False}}
    )

    fake_record_uuid = str(uuid4())
    response = await test_client.patch(
        f"{ctx['base_url']}/{fake_record_uuid}",
        json={"data": {"name": "Test"}},
        headers=ctx["headers"],
    )

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_create_record_with_workflow_access_in_db(
    test_client, crm_template_factory, employee_factory
):
    """
    Проверка создания записи через Workflow эндпоинт пользователем,
    имеющим соответствующее разрешение в базе данных.
    """
    ctx = await crm_template_factory(
        schema={"title": {"type": "string", "required": True}}
    )

    employee_headers = await employee_factory(
        instance_uuid=ctx["instance_uuid"], tool_name=AppTools.WORKFLOW.value
    )

    workflow_url = ctx["base_url"].replace("/notes", "/workflow")
    response = await test_client.post(
        workflow_url,
        json={"data": {"title": "Юзер из базы прошел!"}},
        headers=employee_headers,
    )

    assert response.status_code == 201
    assert response.json()["data"]["title"] == "Юзер из базы прошел!"


@pytest.mark.asyncio
async def test_create_record_forbidden_without_workflow_access_in_db(
    test_client, crm_template_factory, employee_factory
):
    """
    Проверка авторизации: если у пользователя в allowed_tools есть только 'notes',
    то запрос на эндпоинт workflow должен возвращать 403 Forbidden.
    """
    # 1. Создаем изолированный шаблон схемы от лица Менеджера
    ctx = await crm_template_factory(
        schema={"title": {"type": "string", "required": True}}
    )

    # 2. Создаем сотрудника с доступом ТОЛЬКО к notes (workflow отсутствует)
    bad_employee_headers = await employee_factory(
        instance_uuid=ctx["instance_uuid"], tool_name=AppTools.NOTES.value
    )

    # 3. Выполняем запрос к workflow эндпоинту от лица бесправного сотрудника
    workflow_url = ctx["base_url"].replace("/notes", "/workflow")
    response = await test_client.post(
        workflow_url,
        json={"data": {"title": "Я хочу взломать workflow"}},
        headers=bad_employee_headers,
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_full_workflow_with_creator_and_user_roles(
    test_client, crm_environment_factory
):
    """
    Сквозной тест совместной работы: Создатель генерирует шаблон,
    а обычный Сотрудник с ролью USER и пермишеном workflow успешно создает запись.
    """
    # 1. Разворачиваем инстанс и создателя через фабрику среды
    env = await crm_environment_factory()
    instance_uuid = env["instance_uuid"]

    # 2. Создаем сотрудника (USER) с доступом к workflow через встроенный хелпер
    employee_headers = await env["add_employee"](
        role=UserRole.USER, allowed_tools=[AppTools.WORKFLOW.value]
    )

    # 3. Создаем шаблон от лица Владельца пространства
    t_resp = await test_client.post(
        f"/instances/{instance_uuid}/templates",
        json={
            "name": "Задачи Отдела",
            "schema": {"title": {"type": "string", "required": True}},
        },
        headers=env["creator_headers"],
    )
    assert t_resp.status_code == 201
    template_uuid = t_resp.json()["_id"]

    # 4. Вносим запись в этот шаблон от лица Сотрудника через workflow-эндпоинт
    response = await test_client.post(
        f"/instances/{instance_uuid}/templates/{template_uuid}/workflow",
        json={"data": {"title": "Рабочий отчет сотрудника"}},
        headers=employee_headers,
    )

    assert response.status_code == 201
    assert response.json()["data"]["title"] == "Рабочий отчет сотрудника"


class TestSelectFieldIntegration:

    @pytest.mark.annotations
    @pytest.mark.asyncio
    async def test_select_field_lifecycle(self, test_client, crm_template_factory):
        """
        Тестирование жизненного цикла SelectField: успешное создание шаблона с опциями,
        сохранение валидного значения и отклонение некорректного варианта.
        """
        # 1. Создаем шаблон с полем типа select через фабрику
        options = ["Low", "Medium", "High"]
        select_schema = {
            "priority": {"type": "select", "required": True, "options": options}
        }

        ctx = await crm_template_factory(schema=select_schema)
        url, headers = ctx["base_url"], ctx["headers"]

        # 2. Позитивный тест: отправляем значение из списка options
        res = await test_client.post(
            url, json={"data": {"priority": "Medium"}}, headers=headers
        )
        assert res.status_code == 201
        assert res.json()["data"]["priority"] == "Medium"

        # 3. Негативный тест: отправляем значение, отсутствующее в options
        res = await test_client.post(
            url, json={"data": {"priority": "Ultra-High"}}, headers=headers
        )
        assert res.status_code == 422

    @pytest.mark.asyncio
    async def test_invalid_select_meta_fails(self, test_client, crm_template_factory):
        """
        Проверяем, что нельзя создать шаблон с некорректными мета-данными для select (400 Bad Request).
        """
        # Получаем авторизованные заголовки создателя и uuid инстанса из базовой фабрики шаблонов
        ctx = await crm_template_factory()
        url = f"/instances/{ctx['instance_uuid']}/templates"
        headers = ctx["headers"]

        # 1. Попытка создать шаблон без списка options
        bad_meta_payload = {
            "name": "Bad Template",
            "schema": {"status": {"type": "select", "required": True}},
        }
        response = await test_client.post(url, json=bad_meta_payload, headers=headers)
        assert response.status_code == 400

        # 2. Попытка создать шаблон с пустым списком options
        bad_empty_options_payload = {
            "name": "Empty Options Template",
            "schema": {"status": {"type": "select", "required": True, "options": []}},
        }
        response_empty = await test_client.post(
            url, json=bad_empty_options_payload, headers=headers
        )
        assert response_empty.status_code == 400

    @pytest.mark.asyncio
    async def test_update_select_field_type_migration(
        self, test_client, crm_template_factory
    ):
        """
        Тестирование изменения структуры и типа поля в шаблоне через эндпоинт /columns.
        """
        # 1. Создаем начальный шаблон с полем типа select
        initial_schema = {"status": {"type": "select", "options": ["A", "B"]}}
        ctx = await crm_template_factory(schema=initial_schema)

        columns_url = f"/instances/{ctx['instance_uuid']}/templates/{ctx['template_uuid']}/columns"
        headers = ctx["headers"]

        # 2. Позитивный тест: изменение типа поля 'status' с select на string
        update_payload = {
            "column_name": "status",
            "field_meta": {"type": "string", "required": True},
        }
        resp = await test_client.patch(
            columns_url, json=update_payload, headers=headers
        )
        assert resp.status_code == 200

        # 3. Негативный тест: попытка установить select с дублирующимися опциями
        bad_update_payload = {
            "column_name": "status",
            "field_meta": {"type": "select", "options": ["A", "A"]},
        }
        resp_bad = await test_client.patch(
            columns_url, json=bad_update_payload, headers=headers
        )
        assert resp_bad.status_code == 400

    @pytest.mark.asyncio
    async def test_data_migration_select_to_string(
        self, test_client, crm_template_factory
    ):
        """
        Тестирование миграции данных: изменение типа поля из select в string
        позволяет сохранять новые значения вне старых options.
        """
        # 1. Создаем шаблон с полем типа select через фабрику
        initial_schema = {
            "status": {"type": "select", "options": ["Draft", "Published"]}
        }
        ctx = await crm_template_factory(schema=initial_schema)

        instance_uuid = ctx["instance_uuid"]
        template_uuid = ctx["template_uuid"]
        headers = ctx["headers"]
        notes_url = ctx["base_url"]
        columns_url = f"/instances/{instance_uuid}/templates/{template_uuid}/columns"

        # 2. Вносим запись в рамках исходной схемы select
        await test_client.post(
            notes_url, json={"data": {"status": "Draft"}}, headers=headers
        )

        # 3. Миграция: меняем тип поля 'status' с select на string
        migration_payload = {
            "column_name": "status",
            "field_meta": {"type": "string", "required": True},
        }
        migration_resp = await test_client.patch(
            columns_url, json=migration_payload, headers=headers
        )
        assert migration_resp.status_code == 200

        # 4. Проверяем добавление значения, отсутствовавшего в старом options
        new_value = "Archived"
        resp_new = await test_client.post(
            notes_url, json={"data": {"status": new_value}}, headers=headers
        )

        assert resp_new.status_code == 201
        assert resp_new.json()["data"]["status"] == new_value
