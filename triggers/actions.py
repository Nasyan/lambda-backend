# triggers/actions.py

import uuid
import logging
from typing import List, Dict, Any, Callable, Awaitable
from pydantic import BaseModel, Field, ValidationError, UUID4
from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy import select

from notifications.dispatcher import NotificationDispatcher
from notifications.models import NotificationTemplate
from engine.ast import parse_ast
from engine.batch_loader import BatchDataLoader
from engine.evaluator import ASTEvaluator, EvaluationScope
from engine.utils import resolve_dot_notation
from mongo.tools.utils import with_active_filter
from users.models import Users, UserRole
from logs.mongo import (
    execute_logged_mongo_call,
    summarize_mongo_document,
)

# Импортируем доменные исключения автоматизации
from triggers.exceptions.action import (
    AutomationValidationError,
    AutomationExecutionError,
)
from triggers.interpolator import ContextInterpolator

logger = logging.getLogger(__name__)


class TestActionSchema(BaseModel):
    required_text: str = Field(..., description="Тестовый текст, который обязателен")
    send_attempts: int = Field(default=1, description="Количество симулируемых попыток")


class SendNotificationActionSchema(BaseModel):
    """Новая строгая схема параметров экшена. Исключает хардкод текста и каналов."""

    notification_template_uuid: UUID4 = Field(
        ..., description="UUID шаблона уведомления из Postgres"
    )


class ActionRegistry:
    """
    Реестр системных действий (Actions) для автоматизаций в CRM.
    """

    @staticmethod
    async def _resolve_recipients(
        config: Dict[str, Any],
        target: Dict[str, Any],
        db: AsyncIOMotorDatabase,
        instance_uuid: str,
        pg_session: Any = None,
    ) -> List[str]:
        """
        Внутренний метод для парсинга recipients_config (из поля JSONB шаблона).
        Вычисляет финальный список строк (UUID сотрудников, email или tg_name)
        на основе переданного Mongo-документа (target).
        """
        if not config:
            return []

        config_type = config.get("type", "static")

        if config_type == "static":
            return ActionRegistry._dedupe_recipients(
                ActionRegistry._configured_recipient_values(config)
            )

        if config_type in {
            "users",
            "employees",
            "selected_users",
            "specific_users",
            "all_users",
            "all_employees",
        }:
            return await ActionRegistry._resolve_user_recipients(
                config=config,
                instance_uuid=instance_uuid,
                pg_session=pg_session,
            )

        if config_type == "ast_tree":
            ast_root = config.get("tree")
            contact_field = config.get("contact_field")
            if not ast_root or not contact_field:
                raise AutomationValidationError(
                    action_name="create_crm_notification",
                    instance_uuid=instance_uuid,
                    reason=(
                        "recipients_config типа 'ast_tree' требует поля "
                        "'tree' и 'contact_field'."
                    ),
                )

            data_loader = BatchDataLoader(mongo_db=db, instance_uuid=instance_uuid)
            evaluator = ASTEvaluator(batch_loader=data_loader)
            scope = EvaluationScope(
                document=target or {},
                instance_uuid=str(instance_uuid),
            )
            resolved = await evaluator.evaluate(parse_ast(ast_root), scope)
            return ActionRegistry._dedupe_recipients(
                ActionRegistry._extract_contact_values(resolved, contact_field)
            )

        field_path = config.get("field_path")
        if field_path:
            resolved = ContextInterpolator.interpolate(field_path, target)
            return ActionRegistry._dedupe_recipients(
                ActionRegistry._flatten_recipient_values(resolved)
            )

        return []

    @staticmethod
    def _configured_recipient_values(config: Dict[str, Any]) -> List[Any]:
        values: List[Any] = []
        for key in (
            "uuids",
            "user_uuids",
            "employee_uuids",
            "emails",
            "recipients",
            "values",
        ):
            values.extend(ActionRegistry._flatten_recipient_values(config.get(key)))
        return values

    @staticmethod
    async def _resolve_user_recipients(
        config: Dict[str, Any],
        instance_uuid: str,
        pg_session: Any = None,
    ) -> List[str]:
        configured = ActionRegistry._configured_recipient_values(config)
        if configured:
            return ActionRegistry._dedupe_recipients(configured)

        selection = config.get("selection")
        if config.get("type") not in {
            "all_users",
            "all_employees",
        } and selection not in {
            "all",
            "all_active",
            "all_users",
            "all_employees",
        }:
            return []

        if not pg_session:
            raise AutomationValidationError(
                action_name="create_crm_notification",
                instance_uuid=instance_uuid,
                reason="Для выбора всех сотрудников необходим pg_session.",
            )

        stmt = select(Users.uuid).where(
            Users.instance_id == uuid.UUID(str(instance_uuid)),
            Users.active.is_(True),
            Users.role != UserRole.CLIENT,
        )
        result = await pg_session.execute(stmt)
        return [str(user_uuid) for user_uuid in result.scalars().all()]

    @staticmethod
    def _extract_contact_values(resolved: Any, contact_field: str) -> List[Any]:
        documents = resolved if isinstance(resolved, list) else [resolved]
        values: List[Any] = []
        for document in documents:
            if not isinstance(document, dict):
                continue
            value = ActionRegistry._extract_contact_value(document, contact_field)
            values.extend(ActionRegistry._flatten_recipient_values(value))
        return values

    @staticmethod
    def _extract_contact_value(document: Dict[str, Any], contact_field: str) -> Any:
        if contact_field.startswith("data."):
            return resolve_dot_notation(document, contact_field, default=None)
        if contact_field in {"_id", "uuid"}:
            direct_value = resolve_dot_notation(document, contact_field, default=None)
            if direct_value is not None:
                return direct_value
        return resolve_dot_notation(document, f"data.{contact_field}", default=None)

    @staticmethod
    def _flatten_recipient_values(value: Any) -> List[Any]:
        if value is None:
            return []
        if isinstance(value, list):
            flattened: List[Any] = []
            for item in value:
                flattened.extend(ActionRegistry._flatten_recipient_values(item))
            return flattened
        return [value]

    @staticmethod
    def _dedupe_recipients(values: List[Any]) -> List[str]:
        recipients: List[str] = []
        seen = set()
        for value in values:
            recipient = str(value).strip()
            if not recipient or recipient in seen:
                continue
            seen.add(recipient)
            recipients.append(recipient)
        return recipients

    @staticmethod
    async def run_test_action(
        instance_uuid: str,
        targets: List[Dict[str, Any]],
        params: Dict[str, Any],
        db: AsyncIOMotorDatabase,
        pg_session: Any = None,
    ) -> Dict[str, Any]:
        try:
            validated_params = TestActionSchema(**params)
        except ValidationError as e:
            raise AutomationValidationError(
                action_name="test_action",
                instance_uuid=instance_uuid,
                reason="Неверная конфигурация параметров схемы тестового экшена.",
                details=e.errors(),
            )

        executed_count = 0
        logs = []

        for target in targets:
            record_data = target.get("data", {})
            record_identifier = (
                record_data.get("phone") or target.get("_id") or "Unknown Record"
            )

            log_msg = (
                f"[TEST ACTION] Успешно обработана запись {record_identifier}. "
                f"Текст: '{validated_params.required_text}'. Попыток: {validated_params.send_attempts}"
            )
            logger.info(log_msg)
            logs.append(log_msg)
            executed_count += 1

        return {"status": "success", "executed_records": executed_count, "logs": logs}

    @staticmethod
    async def mongo_insert(
        instance_uuid: str,
        targets: List[Dict[str, Any]],
        params: Dict[str, Any],
        db: AsyncIOMotorDatabase,
        pg_session: Any = None,
    ) -> Dict[str, Any]:
        target_template_uuid = params.get("target_template_uuid")
        raw_payload = params.get("payload", {})

        if not target_template_uuid:
            raise AutomationValidationError(
                action_name="mongo_insert",
                instance_uuid=instance_uuid,
                reason="Параметр 'target_template_uuid' является строго обязательным.",
            )

        context = targets[0] if targets else {}
        payload_data = ContextInterpolator.interpolate(raw_payload, context)

        # _id keying fix (audit CRITICAL #2): раньше писали только "uuid" без "_id",
        # Mongo назначала случайный ObjectId, а резолвер/связи ищут запись по _id ->
        # связь к авто-созданной записи не находилась и схлопывалась в 0. Делаем
        # _id == uuid, чтобы авто-запись была разрешима так же, как обычные записи.
        new_record_id = str(uuid.uuid4())
        record_document = {
            "_id": new_record_id,
            "uuid": new_record_id,
            "instance_uuid": str(instance_uuid),
            "template_uuid": str(target_template_uuid),
            "data": payload_data,
            "is_deleted": False,
            "created_by": "system_automation",
        }

        try:
            records_collection = db["records"]
            await execute_logged_mongo_call(
                records_collection,
                "insert_one",
                summarize_mongo_document(record_document),
                lambda: records_collection.insert_one(record_document),
                lambda _: 1,
            )
        except Exception as e:
            raise AutomationExecutionError(
                action_name="mongo_insert",
                instance_uuid=instance_uuid,
                reason=f"Сбой записи в базу данных Mongo: {str(e)}",
            )

        logger.info(f"[AUTOMATION] Создана запись в шаблоне {target_template_uuid}")
        return {"status": "success", "inserted_uuid": record_document["uuid"]}

    @staticmethod
    async def mongo_update(
        instance_uuid: str,
        targets: List[Dict[str, Any]],
        params: Dict[str, Any],
        db: AsyncIOMotorDatabase,
        pg_session: Any = None,
    ) -> Dict[str, Any]:
        target_template_uuid = params.get("target_template_uuid")
        mongo_filter = params.get("filter", {})
        update_op = params.get("update_op", {})

        if not target_template_uuid:
            raise AutomationValidationError(
                action_name="mongo_update",
                instance_uuid=instance_uuid,
                reason="Параметр 'target_template_uuid' является строго обязательным.",
            )

        context = targets[0] if targets else {}
        interpolated_filter = ContextInterpolator.interpolate(mongo_filter, context)
        interpolated_update = ContextInterpolator.interpolate(update_op, context)

        full_filter = {
            "instance_uuid": str(instance_uuid),
            "template_uuid": str(target_template_uuid),
        }

        for k, v in interpolated_filter.items():
            if k.startswith("$"):
                full_filter[k] = v
            else:
                full_filter[f"data.{k}"] = v
        full_filter = with_active_filter(full_filter)

        try:
            records_collection = db["records"]
            result = await execute_logged_mongo_call(
                records_collection,
                "update_many",
                full_filter,
                lambda: records_collection.update_many(
                    full_filter, interpolated_update
                ),
                lambda item: item.modified_count,
                update=interpolated_update,
            )
        except Exception as e:
            raise AutomationExecutionError(
                action_name="mongo_update",
                instance_uuid=instance_uuid,
                reason=f"Сбой массового обновления документов в Mongo: {str(e)}",
            )

        logger.info(
            f"[AUTOMATION] Обновлено записей: {result.modified_count} в шаблоне {target_template_uuid}"
        )
        return {"status": "success", "modified_count": result.modified_count}

    @staticmethod
    async def mongo_upsert(
        instance_uuid: str,
        targets: List[Dict[str, Any]],
        params: Dict[str, Any],
        db: AsyncIOMotorDatabase,
        pg_session: Any = None,
    ) -> Dict[str, Any]:
        target_template_uuid = params.get("target_template_uuid")
        search_fields = params.get("search_fields", [])
        raw_payload = params.get("payload", {})

        if not target_template_uuid or not search_fields:
            raise AutomationValidationError(
                action_name="mongo_upsert",
                instance_uuid=instance_uuid,
                reason="Параметры 'target_template_uuid' и 'search_fields' обязательны для операции upsert.",
            )

        context = targets[0] if targets else {}
        payload_data = ContextInterpolator.interpolate(raw_payload, context)

        query_filter = {
            "instance_uuid": str(instance_uuid),
            "template_uuid": str(target_template_uuid),
        }

        for field in search_fields:
            if field in payload_data:
                query_filter[f"data.{field}"] = payload_data[field]
        query_filter = with_active_filter(query_filter)

        generated_uuid = str(uuid.uuid4())

        # 🔥 Формируем единый атомарный запрос
        update_operations = {
            # $set обновит эти поля всегда (и при создании, и при апдейте)
            "$set": {
                "data": payload_data,
                "updated_by": "system_automation_upsert",
            },
            # $setOnInsert запишет эти поля ТОЛЬКО если документа не было
            "$setOnInsert": {
                # _id keying fix (audit CRITICAL #2): задаём _id == uuid, иначе при
                # вставке Mongo назначит ObjectId и связи по _id не разрешатся.
                "_id": generated_uuid,
                "uuid": generated_uuid,
                "instance_uuid": str(instance_uuid),
                "template_uuid": str(target_template_uuid),
                "is_deleted": False,
                "created_by": "system_automation_upsert",
            },
            # $inc атомарно прибавит 1 к версии.
            # Если документа не было, Mongo сама создаст поле version и запишет туда 1.
            "$inc": {"version": 1},
        }

        try:
            # Делаем один единственный вызов в БД
            records_collection = db["records"]
            result = await execute_logged_mongo_call(
                records_collection,
                "update_one",
                query_filter,
                lambda: records_collection.update_one(
                    query_filter, update_operations, upsert=True
                ),
                lambda item: item.modified_count + (1 if item.upserted_id else 0),
                update=update_operations,
            )
        except Exception as e:
            raise AutomationExecutionError(
                action_name="mongo_upsert",
                instance_uuid=instance_uuid,
                reason=f"Ошибка транзакции upsert в Mongo: {str(e)}",
            )

        # upserted_id присутствует в ответе Mongo только если был создан новый документ
        if result.upserted_id:
            logger.info(
                f"[AUTOMATION] Upsert: Создана новая запись в шаблоне {target_template_uuid}"
            )
            return {"status": "created", "uuid": generated_uuid}
        else:
            logger.info(
                f"[AUTOMATION] Upsert: Обновлена существующая запись в шаблоне {target_template_uuid}"
            )
            return {"status": "updated"}

    @staticmethod
    async def send_telegram_broadcast(
        instance_uuid: str,
        targets: List[Dict[str, Any]],
        params: Dict[str, Any],
        db: AsyncIOMotorDatabase,
        pg_session: Any = None,
    ) -> Dict[str, Any]:
        logger.info(f"[FUTURE TELEGRAM] Логика рассылки для {len(targets)} адресатов.")
        return {"status": "success", "detail": "Telegram stub successfully processed"}

    @staticmethod
    async def create_crm_notification(
        instance_uuid: str,
        targets: List[Dict[str, Any]],
        params: Dict[str, Any],
        db: AsyncIOMotorDatabase,
        pg_session: Any = None,
    ) -> Dict[str, Any]:
        """
        Полностью переписанный No-Code экшен.
        Берет шаблон из Postgres, динамически вычисляет получателей (в т.ч. через AST),
        рендерит текст сообщения и отправляет пачкой в Диспетчер.
        """
        if not pg_session:
            raise AutomationExecutionError(
                action_name="create_crm_notification",
                instance_uuid=instance_uuid,
                reason="Передача уведомления невозможна: отсутствует необходимый pg_session.",
            )

        # 1. Валидация параметров через новую Pydantic-схему (ожидаем только template_uuid)
        try:
            validated_params = SendNotificationActionSchema(**params)
        except ValidationError as e:
            raise AutomationValidationError(
                action_name="create_crm_notification",
                instance_uuid=instance_uuid,
                reason="Неверная конфигурация параметров экшена уведомлений.",
                details=e.errors(),
            )

        # 2. Извлекаем шаблон из PostgreSQL с проверкой Multi-tenancy изолированности
        stmt = select(NotificationTemplate).where(
            NotificationTemplate.uuid == validated_params.notification_template_uuid,
            NotificationTemplate.instance_uuid == uuid.UUID(instance_uuid),
        )
        result = await pg_session.execute(stmt)
        template = result.scalar_one_or_none()

        if not template:
            raise AutomationExecutionError(
                action_name="create_crm_notification",
                instance_uuid=instance_uuid,
                reason=f"Шаблон уведомления {validated_params.notification_template_uuid} не найден в данном контуре.",
            )

        total_sent_recipients = 0

        # 3. Проходим циклом по документам из MongoDB, которые вызвали триггер
        for target in targets:
            # Вычисляем получателей (парсинг дерева AST или получение статического списка)
            recipients = await ActionRegistry._resolve_recipients(
                config=template.recipients_config,
                target=target,
                db=db,
                instance_uuid=str(instance_uuid),
                pg_session=pg_session,
            )
            if not recipients:
                logger.warning(
                    f"[AUTOMATION] Набор получателей пуст для Mongo-документа {target.get('_id')}"
                )
                continue

            # Интерполируем title и body данными из текущего Mongo-документа
            compiled_title = ContextInterpolator.interpolate(template.title, target)
            compiled_body = ContextInterpolator.interpolate(template.body, target)

            # 4. Отправляем в диспетчер (он запишет историю, создаст инбоксы/колокольчики и пнет воркеры)
            await NotificationDispatcher.dispatch(
                pg_session=pg_session,
                instance_uuid=str(instance_uuid),
                template_uuid=str(template.uuid),
                title=compiled_title,
                body=compiled_body,
                channels=template.channels,
                recipients=recipients,
            )
            total_sent_recipients += len(recipients)
            # Транзакция НЕ коммитится здесь, это зона ответственности process_cron_triggers

        return {"status": "success", "total_recipients_notified": total_sent_recipients}


ACTION_MAPPING: Dict[
    str,
    Callable[
        [str, List[Dict[str, Any]], Dict[str, Any], AsyncIOMotorDatabase, Any],
        Awaitable[Any],
    ],
] = {
    "test_action": ActionRegistry.run_test_action,
    "mongo_insert": ActionRegistry.mongo_insert,
    "INSERT_RECORD": ActionRegistry.mongo_insert,
    "mongo_update": ActionRegistry.mongo_update,
    "UPDATE_RECORD": ActionRegistry.mongo_update,
    "mongo_upsert": ActionRegistry.mongo_upsert,
    "UPSERT_RECORD": ActionRegistry.mongo_upsert,
    "send_telegram_broadcast": ActionRegistry.send_telegram_broadcast,
    "SEND_BULK_NOTIFICATION": ActionRegistry.send_telegram_broadcast,
    "create_crm_notification": ActionRegistry.create_crm_notification,
    "SEND_NOTIFICATION": ActionRegistry.create_crm_notification,
}
