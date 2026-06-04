# triggers/actions.py

import uuid
import logging
from typing import List, Dict, Any, Callable, Awaitable
from pydantic import BaseModel, Field, ValidationError, UUID4
from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy import select

from notifications.dispatcher import NotificationDispatcher
from notifications.models import NotificationTemplate

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
    def _resolve_recipients(
        config: Dict[str, Any], target: Dict[str, Any]
    ) -> List[str]:
        """
        Внутренний метод для парсинга recipients_config (из поля JSONB шаблона).
        Вычисляет финальный список строк (UUID сотрудников, email или tg_name)
        на основе переданного Mongo-документа (target).
        """
        config_type = config.get("type", "static")

        if config_type == "static":
            # Вариант 1: Жестко заданный список UUID сотрудников в шаблоне
            return config.get("uuids", [])

        elif config_type == "ast_tree":
            # Вариант 2: Динамическое вычисление через ваше AST-дерево.
            # Сюда залетает структура вашего AST-дерева.
            ast_root = config.get("tree", {})  # noqa! F841

            # Пример интеграции с вашим AST-вычислителем (если он вынесен в отдельный модуль):
            # return DynamicASTEvaluator.evaluate(ast_root, context=target)

            # Простой пример-заглушка: если в конфиге лежит маска пути до поля в Mongo-документе
            field_path = config.get(
                "field_path"
            )  # например: "{{data.responsible_manager_uuid}}"
            if field_path:
                resolved = ContextInterpolator.interpolate(field_path, target)
                if isinstance(resolved, list):
                    return [str(uid) for uid in resolved]
                if resolved:
                    return [str(resolved)]

        return []

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
            "created_by": "system_automation",
        }

        try:
            await db["records"].insert_one(record_document)
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

        try:
            result = await db["records"].update_many(full_filter, interpolated_update)
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
                "created_by": "system_automation_upsert",
            },
            # $inc атомарно прибавит 1 к версии.
            # Если документа не было, Mongo сама создаст поле version и запишет туда 1.
            "$inc": {"version": 1},
        }

        try:
            # Делаем один единственный вызов в БД
            result = await db["records"].update_one(
                query_filter, update_operations, upsert=True
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
            recipients = ActionRegistry._resolve_recipients(
                template.recipients_config, target
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
    "mongo_update": ActionRegistry.mongo_update,
    "mongo_upsert": ActionRegistry.mongo_upsert,
    "send_telegram_broadcast": ActionRegistry.send_telegram_broadcast,
    "create_crm_notification": ActionRegistry.create_crm_notification,
}
