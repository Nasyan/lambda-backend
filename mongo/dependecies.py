# mongo/dependecies.py

from fastapi import Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from mongo.record import RecordRepository
from mongo.template import TemplateRepository
from mongo.db import get_mongo_db


def get_record_repository(
    db: AsyncIOMotorDatabase = Depends(get_mongo_db),
) -> RecordRepository:
    return RecordRepository(db)


def get_template_repository(
    db: AsyncIOMotorDatabase = Depends(get_mongo_db),
) -> TemplateRepository:
    return TemplateRepository(db)
