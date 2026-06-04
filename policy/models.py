# policy/models.py

import uuid
from sqlalchemy import Column, String, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from database.db import Base


class StorefrontPolicies(Base):
    __tablename__ = "storefront_policies"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Привязка к конкретному инстансу (интернет-магазину клиента CRM)
    instance_uuid = Column(
        UUID(as_uuid=True),
        ForeignKey("instances.uuid", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # Шаблон MongoDB (например, 'products' или 'orders'), к которому применяется правило
    template_name = Column(String(100), nullable=False)

    defaults = Column(JSONB, nullable=False, default=dict)
    # Жесткие фильтры, которые всегда накладываются на выдачу
    # Например: {"data.in_stock": True, "data.is_public": True}
    read_filters = Column(JSONB, nullable=False, default=dict)

    # Whitelist полей для отдачи на фронтенд (массив строк)
    # Например: ["name", "price", "image", "description"]
    read_mask = Column(JSONB, nullable=False, default=list)

    # Whitelist полей для приема от клиента через POST (массив строк)
    # Например: ["product_id", "qty", "customer_phone"]
    write_mask = Column(JSONB, nullable=False, default=list)

    # Гарантия бизнес-логики: один шаблон внутри инстанса = одна политика
    __table_args__ = (
        UniqueConstraint(
            "instance_uuid", "template_name", name="uq_instance_template_policy"
        ),
    )
