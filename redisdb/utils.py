# redisdb/utils.py

import redis.asyncio as redis
from config import EMAIL_DB, LOGIN_DB, REDIS_HOST, REDIS_PORT, REGISTRATION_DB, TASK_DB


def generate_key(prefix, sub):
    return f"{prefix}:{sub}"


redis_clients = {}


async def init_redis():
    dbs = {
        "EMAIL_DB": EMAIL_DB,
        "TASK_DB": TASK_DB,
        "REGISTRATION_DB": REGISTRATION_DB,
        "LOGIN_DB": LOGIN_DB,
    }

    for name, db in dbs.items():
        redis_clients[name] = redis.Redis(
            host=REDIS_HOST,
            port=REDIS_PORT,
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
        try:
            client = redis_clients.get(db_name)
        except Exception as e:
            raise e
        return client

    return get_client
