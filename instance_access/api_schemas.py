from typing import Optional

from pydantic import BaseModel


class TriggersPatchRequest(BaseModel):
    enabled: Optional[bool] = None
    allow_get: Optional[bool] = None
    allow_post: Optional[bool] = None
    allow_put: Optional[bool] = None
    allow_delete: Optional[bool] = None
    allow_cron: Optional[bool] = None
