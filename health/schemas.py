# health/schemas.py

from typing import List

from pydantic import BaseModel


class PulseSchema(BaseModel):
    status: bool = True
    description: str = None


class ExistsTables(BaseModel):
    tables: List[str]
