import redis.asyncio as redis
import config  # Импортируем модуль целиком, а не переменные из него


def generate_key(prefix, sub):
    return f"{prefix}:{sub}"


redis_clients = {}


async def init_redis():
    dbs = {
        "EMAIL_DB": config.EMAIL_DB,
        "TASK_DB": config.TASK_DB,
        "REGISTRATION_DB": config.REGISTRATION_DB,
        "LOGIN_DB": config.LOGIN_DB,
    }

    for name, db in dbs.items():
        redis_clients[name] = redis.Redis(
            host=config.REDIS_HOST,
            port=config.REDIS_PORT,  # Теперь подмена порта в контесте сработает!
            db=db,
            encoding="utf-8",
            decode_responses=True,
        )
        await redis_clients[name].ping()


async def close_redis():
    for client in redis_clients.values():
        await client.close()


def get_redis_db(db_name: str):
    async def get_client():
        return redis_clients.get(db_name)

    return get_client
