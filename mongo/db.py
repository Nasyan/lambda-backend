# mongo/db.py

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from config import MONGO_URL, MONGO_DB_NAME


class MongoDB:
    def __init__(self):
        self.client: AsyncIOMotorClient = None
        self.db = None

    def connect(self):
        try:
            self.client = AsyncIOMotorClient(
                MONGO_URL,
                maxPoolSize=100,  # 👈 Потолок подключений для одного воркера FastAPI
                minPoolSize=10,  # 👈 Держать готовыми минимум 10 подключений
                waitQueueTimeoutMS=2000,  # Ждать свободное подключение максимум 2 секунды
            )
            self.db = self.client[MONGO_DB_NAME]
            print(f"Connected to MongoDB {MONGO_URL.split("@")[-1]}")
        except Exception as e:
            print("Failed to create MongoDB client")
            raise e

    def close(self):
        if self.client:
            self.client.close()
            print("MongoDB connection closed")


mongo_manager = MongoDB()


async def get_mongo_db() -> AsyncIOMotorDatabase:
    return mongo_manager.db
