# workers/__init__.py

import dramatiq
from dramatiq.brokers.redis import RedisBroker
from config import TASK_DB, REDIS_PORT, REDIS_HOST

# Настраиваем брокер глобально
url = f"redis://{REDIS_HOST}:{REDIS_PORT}/{TASK_DB}"
broker = RedisBroker(url=url)
dramatiq.set_broker(broker)

# Импортируем все таски, чтобы Dramatiq воркер "увидел" их при запуске пакета
from workers.email_tasks import send_email  # noqa
from workers.crm_tasks import check_time_based_alerts  # noqa
