# workers/crm_tasks.py

import asyncio
import dramatiq
import logging
from redis import Redis
from motor.motor_asyncio import AsyncIOMotorClient

from config import MONGO_URL, MONGO_DB_NAME, REDIS_HOST, REDIS_PORT, TASK_DB
from database.db import SessionLocal
from triggers.service import AutomationService
from logs.decorators import trace_action

logger = logging.getLogger(__name__)

# Подключаем Redis для менеджмента блокировок
redis_client = Redis(host=REDIS_HOST, port=REDIS_PORT, db=TASK_DB)


@trace_action(name="Task::Cron_Triggers_Run")
async def run_time_triggers():
    logger.info("[CRON] Асинхронная проверка временных триггеров началась...")

    mongo_client = AsyncIOMotorClient(MONGO_URL)
    mongo_db = mongo_client[MONGO_DB_NAME]

    # Сессия Postgres теперь открывается в режиме context manager без коммита "всего пакета"
    # Каждый шаг внутри сервиса должен отвечать за свои транзакции самостоятельно
    async with SessionLocal() as pg_session:
        try:
            await AutomationService.process_cron_triggers(pg_session, mongo_db)
            logger.info(
                "[CRON] Асинхронная проверка временных триггеров успешно завершена."
            )
        except Exception as e:
            logger.error(
                f"[CRON CRITICAL ERROR] Ошибка выполнения пакета триггеров: {str(e)}"
            )
            # Не пробрасываем raise e наружу, чтобы Dramatiq не уходил в бесконечный ретрай
            # для CRON-задачи, которая всё равно перезапустится через 10 секунд.
        finally:
            await mongo_client.close()


@dramatiq.actor(max_retries=0)  # 🔥 ЗАПРЕЩАЕМ РЕТРАИ ДЛЯ ПЕРИОДИЧЕСКОГО СКАНИРОВАНИЯ
def check_time_based_alerts():
    """Синхронный мост Dramatiq с защитой от перекрытия (Distributed Lock)"""
    lock_key = "lock:cron_triggers_run"
    # Пытаемся захватить лок на 5 минут (300 секунд)
    # acquire_lock = redis_client.set(lock_key, "true", ex=300, nx=True)

    if not redis_client.set(lock_key, "true", ex=300, nx=True):
        logger.warning(
            "[DRAMATIQ CRON] Прошлая задача еще выполняется. Пропуск итерации."
        )
        return

    try:
        asyncio.run(run_time_triggers())
    except Exception as e:
        logger.error(f"[DRAMATIQ CRON] Ошибка выполнения актора: {str(e)}")
    finally:
        # Обязательно освобождаем лок
        redis_client.delete(lock_key)
