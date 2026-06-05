# core/services/record.py

from uuid import UUID
from typing import Dict, Any, Optional
from sqlalchemy.orm import Session
from motor.motor_asyncio import AsyncIOMotorDatabase

from mongo.record import RecordRepository
from mongo.template import TemplateRepository
from mongo.tools.exceptions import TemplateNotFoundError, RecordNotFoundError

# Импортируем наши новые, чистые и информативные доменные ошибки
from core.exceptions.record import (
    TemplateNotFoundDomainError,
    RecordNotFoundDomainError,
    RecordValidationError,
)

from core.validators.record import (
    RecordDataValidator,
    RecordUniqueConstraintChecker,
)
from core.services.resolver_factory import RecordResolverFactory

from triggers.service import AutomationService
from triggers.models import EventType
from triggers.exceptions.action import SystemContractViolation
from engine.service import FormulaService

import logging

logger = logging.getLogger(__name__)


class RecordService:
    """Оркестратор работы с записями (task3, ГЗ-1 Блок B).

    Говорит ЧТО делать: формулы -> валидация -> уникальность -> глупый I/O
    репозитория -> автоматизации. Сборка резолверов — RecordResolverFactory,
    проверки — core/validators/record.py.
    """

    def __init__(
        self,
        record_repo: RecordRepository,
        template_repo: TemplateRepository,
        pg_session: Session,
        mongo_db: AsyncIOMotorDatabase,
    ):
        self.record_repo = record_repo
        self.template_repo = template_repo
        self.pg_session = pg_session
        self.mongo_db = mongo_db

        self.resolver_factory = RecordResolverFactory(record_repo)
        self.data_validator = RecordDataValidator(record_repo)
        self.unique_checker = RecordUniqueConstraintChecker(record_repo)

    async def create_new_record(
        self,
        instance_uuid: UUID,
        template_uuid: UUID,
        user_uuid: UUID,
        data: Dict[str, Any],
        s3_service: Any = None,
    ) -> Dict[str, Any]:

        str_instance = str(instance_uuid)
        str_template = str(template_uuid)

        try:
            template = await self.template_repo.get_template_by_uuid(
                str_instance,
                str_template,
            )
        except TemplateNotFoundError:
            raise TemplateNotFoundDomainError(
                template_uuid=str_template, instance_uuid=str_instance
            )

        session_resolver, aggregation_resolver = self.resolver_factory.create(
            str_instance
        )

        computed_data = await FormulaService.process_record_formulas(
            template_schema=template["schema"],
            record_data=data,
            record_resolver=session_resolver,
            aggregation_resolver=aggregation_resolver,
        )

        try:
            await self.data_validator.validate(
                data=computed_data,
                schema=template["schema"],
                instance_uuid=str_instance,
                s3_service=s3_service,
            )
            await self.unique_checker.check(
                instance_uuid=str_instance,
                template_uuid=str_template,
                data=computed_data,
                schema=template["schema"],
            )
            inserted_record = await self.record_repo.create_record(
                instance_uuid=str_instance,
                template_uuid=str_template,
                data=computed_data,
                user_uuid=str(user_uuid),
            )
        except Exception as e:
            raise RecordValidationError(
                action="создании", error=e, template_uuid=str_template
            )

        try:
            await AutomationService.execute_automation_triggers(
                pg_session=self.pg_session,
                mongo_db=self.mongo_db,
                instance_uuid=str_instance,
                template_uuid=str_template,
                event_type=EventType.ON_RECORD_CREATE,
                current_record=inserted_record,
            )
        except SystemContractViolation:
            raise
        except Exception as automation_error:
            # Трейсбек триггеров не должен прерывать успешное сохранение записи,
            # поэтому здесь мы оставляем безопасный изолированный logger.error
            logger.error(
                f"Критический сбой автоматизации: {automation_error}",
                exc_info=True,
            )

        return inserted_record

    async def get_records_list(
        self,
        instance_uuid: UUID,
        template_uuid: UUID,
        filters: Dict[str, Any],
        sort_by: Optional[str],
        descending: bool,
        limit: int,
        offset: int,
    ) -> Dict[
        str, Any
    ]:  # Возвращаем словарь, который совпадет с PaginatedRecordsResponse

        # Запрашиваем у репозитория кортеж (список_записей, общее_количество)
        results, total_count = await self.record_repo.get_records(
            instance_uuid=str(instance_uuid),
            template_uuid=str(template_uuid),
            filters=filters,
            sort_by=sort_by,
            sort_descending=descending,
            limit=limit,
            offset=offset,
        )

        return {
            "total": total_count,
            "limit": limit,
            "offset": offset,
            "results": results,
        }

    async def update_existing_record(
        self,
        instance_uuid: UUID,
        template_uuid: UUID,
        record_uuid: UUID,
        user_uuid: UUID,
        new_data: Dict[str, Any],
        s3_service: Any = None,
    ) -> Dict[str, Any]:

        str_instance = str(instance_uuid)
        str_template = str(template_uuid)
        str_record = str(record_uuid)

        # 1. Получаем шаблон
        try:
            template = await self.template_repo.get_template_by_uuid(
                str_instance,
                str_template,
            )
        except TemplateNotFoundError:
            raise TemplateNotFoundDomainError(
                template_uuid=str_template, instance_uuid=str_instance
            )

        # 2. Получаем текущую запись для мерджа старых и новых данных
        try:
            existing_record = await self.record_repo.get_record_by_uuid(
                str_instance,
                str_record,
            )
        except RecordNotFoundError:
            raise RecordNotFoundDomainError(
                record_uuid=str_record, instance_uuid=str_instance
            )

        # Мерджим старые данные с новыми
        existing_data = existing_record.get("data", {})
        merged_data = {
            **existing_data,
            **new_data,
        }

        # Создаем резолверы
        session_resolver, aggregation_resolver = self.resolver_factory.create(
            str_instance
        )

        # Пересчитываем формулы при обновлении
        computed_data = await FormulaService.process_record_formulas(
            template_schema=template["schema"],
            record_data=merged_data,
            record_resolver=session_resolver,
            aggregation_resolver=aggregation_resolver,
        )

        # Валидируем и обновляем запись уже вычисленными данными
        try:
            await self.data_validator.validate(
                data=computed_data,
                schema=template["schema"],
                instance_uuid=str_instance,
                s3_service=s3_service,
            )
            await self.unique_checker.check(
                instance_uuid=str_instance,
                template_uuid=str_template,
                data=computed_data,
                schema=template["schema"],
                exclude_record_uuid=str_record,
            )
            updated_record = await self.record_repo.update_record_data(
                instance_uuid=str_instance,
                record_uuid=str_record,
                new_data=computed_data,
                user_uuid=str(user_uuid),
            )
        except RecordNotFoundError:
            raise RecordNotFoundDomainError(
                record_uuid=str_record, instance_uuid=str_instance
            )
        except Exception as e:
            raise RecordValidationError(
                action="обновлении",
                error=e,
                template_uuid=str_template,
                record_uuid=str_record,
            )

        # 🚀 ПЕРЕХВАТЧИК АВТОМАТИЗАЦИЙ
        try:
            await AutomationService.execute_automation_triggers(
                pg_session=self.pg_session,
                mongo_db=self.mongo_db,
                instance_uuid=str_instance,
                template_uuid=str_template,
                event_type=EventType.ON_RECORD_UPDATE,
                current_record=updated_record,
            )
        except SystemContractViolation:
            raise
        except Exception as automation_error:
            logger.error(
                f"Критический сбой автоматизации обновления: {automation_error}",
                exc_info=True,
            )

        return updated_record
