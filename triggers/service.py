# triggers/service.py

import logging
from typing import Dict, Any
from sqlalchemy import select
from motor.motor_asyncio import AsyncIOMotorDatabase
from pydantic import TypeAdapter, ValidationError
from .models import Trigger, EventType, TriggerType
from .actions import ACTION_MAPPING
from .interpolator import ContextInterpolator
from engine.ast import ASTNode
from engine.evaluator import FormulaEvaluator

# Импортируем наши профессиональные исключения
from exceptions.base import BaseAppException
from triggers.exceptions.service import (
    AutomationActionNotFoundError,
    AutomationConditionEvaluationError,
)

logger = logging.getLogger(__name__)


class AutomationService:

    @classmethod
    async def execute_automation_triggers(
        cls,
        pg_session: Any,
        mongo_db: AsyncIOMotorDatabase,
        instance_uuid: str,
        template_uuid: str,
        event_type: EventType,
        current_record: Dict[str, Any],
    ) -> None:
        """
        Ищет активные триггеры автоматизации в Postgres и последовательно выполняет их.
        """
        if hasattr(pg_session, "__anext__"):
            async for session in pg_session:
                pg_session = session
                break

        stmt = select(Trigger).where(
            Trigger.instance_uuid == instance_uuid,
            Trigger.target_template_uuid == template_uuid,
            Trigger.trigger_type == TriggerType.AUTOMATION,
            Trigger.event_type == event_type,
        )

        if hasattr(pg_session, "execute"):
            result = await pg_session.execute(stmt)
            triggers = result.scalars().all()
        else:
            triggers = pg_session.scalars(stmt).all()

        for trigger in triggers:
            try:
                # 2. Проверяем CONDITION через эвалюатор
                is_valid = await cls._evaluate_condition(
                    trigger.name, trigger.ast, current_record
                )
            except AutomationConditionEvaluationError as e:
                logger.error(
                    f"[AUTOMATION CRITICAL] Условие триггера '{trigger.name}' аварийно сломано: {e.message}"
                )
                continue  # Изолируем битый триггер

            logger.info(
                f"[DEBUG] Trigger '{trigger.name}' condition evaluation: {is_valid}"
            )

            if not is_valid:
                logger.info(
                    f"[AUTOMATION] Условие триггера '{trigger.name}' не выполнено. Пропуск."
                )
                continue

            # 3. Находим соответствующий экшен в Реестре
            action_func = ACTION_MAPPING.get(trigger.action_name)
            if not action_func:
                raise AutomationActionNotFoundError(
                    action_name=trigger.action_name,
                    trigger_name=trigger.name,
                    instance_uuid=instance_uuid,
                )

            # 4. Интерполируем параметры
            raw_params = trigger.action_params or {}
            interpolated_params = ContextInterpolator.interpolate(
                raw_params, current_record
            )

            # 5. Выполняем экшен
            try:
                await action_func(
                    instance_uuid=instance_uuid,
                    targets=[current_record],
                    params=interpolated_params,
                    db=mongo_db,
                    pg_session=pg_session,
                )

                # 🔥 ФИКС: Фиксируем транзакцию для текущего успешного триггера.
                # Теперь созданные в диспетчере 'history' и 'inbox' гарантированно сохранятся в БД.
                if hasattr(pg_session, "commit"):
                    await pg_session.commit()

                logger.info(
                    f"[AUTOMATION] Триггер '{trigger.name}' успешно отработал экшен '{trigger.action_name}'."
                )
            except BaseAppException:
                # В случае преднамеренного бизнес-исключения делаем rollback текущей транзакции перед пробросом
                if hasattr(pg_session, "rollback"):
                    await pg_session.rollback()
                raise
            except Exception as e:
                if hasattr(pg_session, "rollback"):
                    await pg_session.rollback()
                logger.error(
                    f"[AUTOMATION ERROR] Непредвиденная системная ошибка в '{trigger.name}': {e}",
                    exc_info=True,
                )
                raise

    @classmethod
    async def _evaluate_condition(
        cls, trigger_name: str, ast_condition: Dict[str, Any], record: Dict[str, Any]
    ) -> bool:
        """
        Полноценный мост между сырым JSON из БД и движком вычисления формул.
        """
        if not ast_condition or ast_condition == {}:
            return True  # Если условие пустое — выполняем безусловно

        try:
            # Валидируем сырой словарь в полиморфное Pydantic-дерево
            ast_node = TypeAdapter(ASTNode).validate_python(ast_condition)

            # Прокидываем в эвалюатор. Текущая запись становится контекстом.
            result = await FormulaEvaluator.evaluate(node=ast_node, context=record)

            return bool(result)

        except ValidationError as e:
            raise AutomationConditionEvaluationError(
                trigger_name=trigger_name,
                reason="Структура AST-дерева в базе данных повреждена или не валидна.",
                details={"errors": e.errors()},
            )
        except Exception as e:
            raise AutomationConditionEvaluationError(
                trigger_name=trigger_name,
                reason=f"Внутренний сбой эвалюатора формул: {str(e)}",
            )

    @classmethod
    async def process_cron_triggers(
        cls, pg_session: Any, mongo_db: AsyncIOMotorDatabase
    ) -> None:
        """
        Сканирует Postgres на наличие временных (CRON) триггеров автоматизации,
        выбирает целевые записи из Mongo и выполняет условия.
        """
        stmt = select(Trigger).where(
            Trigger.trigger_type == TriggerType.AUTOMATION,
            Trigger.event_type == EventType.CRON,
        )

        if hasattr(pg_session, "execute"):
            result = await pg_session.execute(stmt)
            cron_triggers = result.scalars().all()
        else:
            cron_triggers = pg_session.scalars(stmt).all()

        if not cron_triggers:
            logger.debug("[CRON] Активных временных триггеров в БД не обнаружено.")
            return

        for trigger in cron_triggers:
            if not trigger.target_template_uuid:
                continue

            template_uuid_str = str(trigger.target_template_uuid)
            instance_uuid_str = str(trigger.instance_uuid)

            # Оптимизация: Запрашиваем только нужные поля, если это возможно,
            # но пока оставляем find для полной совместимости с контекстом
            cursor = mongo_db["records"].find(
                {"template_uuid": template_uuid_str, "instance_uuid": instance_uuid_str}
            )

            async for record in cursor:
                # 🔥 ТОЧКА ИЗОЛЯЦИИ: Создаем SAVEPOINT или управляем commit/rollback поштучно
                try:
                    is_valid = await cls._evaluate_condition(
                        trigger.name, trigger.ast, record
                    )

                    if not is_valid:
                        continue

                    action_func = ACTION_MAPPING.get(trigger.action_name)
                    if not action_func:
                        logger.error(f"[CRON] Экшен '{trigger.action_name}' не найден.")
                        continue

                    raw_params = trigger.action_params or {}
                    interpolated_params = ContextInterpolator.interpolate(
                        raw_params, record
                    )

                    # Выполняем экшен для конкретной записи
                    await action_func(
                        instance_uuid=instance_uuid_str,
                        targets=[record],
                        params=interpolated_params,
                        db=mongo_db,
                        pg_session=pg_session,
                    )

                    # 🔥 ФИКС: Коммитим Postgres транзакцию строго для ТЕКУЩЕЙ успешной записи
                    if hasattr(pg_session, "commit"):
                        await pg_session.commit()

                    logger.info(
                        f"[CRON SUCCESS] Триггер '{trigger.name}' успешно обработал запись {record.get('_id')}"
                    )

                except Exception as e:
                    # Если упала конкретная запись — откатываем только её операции в PG
                    if hasattr(pg_session, "rollback"):
                        await pg_session.rollback()

                    logger.error(
                        f"[CRON RECORD ERROR] Ошибка обработки записи {record.get('_id')} "
                        f"в триггере '{trigger.name}': {str(e)}",
                        exc_info=True,
                    )
                    # Проглатываем ошибку (continue), переходим к следующему документу таблицы!
                    continue
