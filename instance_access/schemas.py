from pydantic import BaseModel, Field, ConfigDict, model_validator


class TriggersToolSchema(BaseModel):
    enabled: bool = True
    allow_get: bool = True
    allow_post: bool = True
    allow_put: bool = True
    allow_delete: bool = True

    @model_validator(mode="after")
    def enforce_disabled_rules(self) -> "TriggersToolSchema":
        # Если инструмент выключен целиком, гасим все внутренние права
        if not self.enabled:
            self.allow_get = False
            self.allow_post = False
            self.allow_put = False
            self.allow_delete = False
        return self


class TablesToolSchema(BaseModel):
    enabled: bool = True


class AnalyticsToolSchema(BaseModel):
    enabled: bool = True


class StoreToolSchema(BaseModel):
    enabled: bool = True


class InstanceToolsConfigSchema(BaseModel):
    """
    Мастер-схема конфигурации инструментов инстанса.
    """

    model_config = ConfigDict(from_attributes=True)

    triggers: TriggersToolSchema = Field(default_factory=TriggersToolSchema)
    tables: TablesToolSchema = Field(default_factory=TablesToolSchema)
    analytics: AnalyticsToolSchema = Field(default_factory=AnalyticsToolSchema)
    store: StoreToolSchema = Field(default_factory=StoreToolSchema)

    def to_dict(self) -> dict:
        return self.model_dump()
