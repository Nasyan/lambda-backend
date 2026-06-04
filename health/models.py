# health/models.py

from sqlalchemy import Column, String, Integer

from database.db import Base


class TestTable(Base):
    __tablename__ = "test_table"

    id = Column(Integer, primary_key=True)
    name = Column(String)
