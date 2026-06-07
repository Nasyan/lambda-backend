# mongo/db.py

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from config import MONGO_URL, MONGO_DB_NAME


class MongoDB:
    def __init__(self):
        self.client: AsyncIOMotorClient = None
        self.db = None

    def connect(self):
        try:
            self.client = AsyncIOMotorClient(MONGO_URL)
            self.db = self.client[MONGO_DB_NAME]
        except Exception as e:
            raise e

    def close(self):
        if self.client:
            self.client.close()


mongo_manager = MongoDB()


async def get_mongo_db() -> AsyncIOMotorDatabase:
    return mongo_manager.db
