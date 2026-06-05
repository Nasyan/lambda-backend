# Архитектурный аудит task3 (ГЗ-1): View → Service → Repository

Дата: 2026-06-06. Ветка `feature/task3-arch-hardening-tests`.
Правила: Views — только HTTP/права/DTO; Services — оркестраторы без SQL/Mongo;
Repositories — глупый I/O; Validators/Factories — отдельные классы.

## Этап 1 — обязательные рефакторинги

| Находка | Действие |
|---|---|
| `SchemaIntegrityValidator` — God Object (чистые проверки + PG-каскады в одном классе) | Разбит: `engine/schema_rules.py::NoCodeSchemaValidator` (in-memory: циклы формул, used-fields, storefront-маски, definition) + `core/services/template_integrity.py::TemplateIntegrityService` (PG/Mongo каскады, notification/trigger AST). `engine/integrity.py` удалён, все вызовы обновлены |
| `TemplateRepository` валидировал схему (`validate_schema_definition`) и запускал миграцию записей в `update_column_meta` | Репозиторий — глупый I/O; валидация в `TemplateService` (контракт ошибок API сохранён), миграция — `core/services/schema_migration.py::SchemaMigrationService` |
| `inject_trigger_to_schema`/`remove_trigger_from_schema` в репозитории шаблонов | Вынесены в `mongo/trigger_metadata.py::TriggerMetadataRepository` |
| `RecordRepository.validate_record_data`/`check_unique_constraints` | Вырезаны → `core/validators/record.py::RecordDataValidator` + `RecordUniqueConstraintChecker`; репозиторию добавлены глупые примитивы `has_field_value_duplicate`, `set_record_data_field`. `validate_dict_keys` оставлен в репо осознанно: это NoSQL-injection защита I/O-слоя, не бизнес-валидация |
| `RecordService._create_resolvers` (сборка контекста в сервисе) | `core/services/resolver_factory.py::RecordResolverFactory` |
| `RecordRepository.validate_existing_records_against_field` (миграция данных в репо) | `SchemaMigrationService.validate_existing_records_against_field`, оркестрируется из `TemplateService.update_column_meta` |

## Этап 2 — discovery-аудит модулей

| Модуль | Находка | Действие |
|---|---|---|
| `triggers/views.py` | **Худший случай**: полный CRUD SQL + валидация + sync Mongo-метаданных прямо в роутере | Создан `triggers/repository.py::TriggerRepository` (глупый PG I/O) + `triggers/admin_service.py::TriggerAdminService` (оркестрация create/update/delete/list + schema-sync + транзакции); роутер — тонкий |
| `policy/service.py` | SQL в сервисе; `_get_template_schema_by_name` — O(N) обход всех шаблонов | `policy/repository.py::PolicyRepository`; точечный `TemplateService.find_by_name` (O(1) запрос) |
| `store/service.py` | `HTTPException` в сервисном слое (4×) — сервис знал о HTTP; O(N) резолв имени шаблона; SQL в сервисе | Доменные ошибки `store/exceptions.py` (`StorefrontTemplateNotFoundError` 404, `StorefrontEmptyWritePayloadError` 400, замаплены в `exceptions/handlers.py`); `find_by_name`; policy-запрос через `PolicyRepository` |
| `notifications/service.py` | SQL (select/update/delete) в сервисе | `notifications/repository.py::NotificationTemplateRepository` + `NotificationInboxRepository` |
| `analytics/widget.py` | SQL в сервисе (3 одинаковых select) | `analytics/repository.py::AnalyticsWidgetRepository` |
| `users/services/auth_service.py` | Толстый сервис: генерация кода + отправка email (побочное действие) внутри register/resend | `users/services/verification_notifier.py::RegistrationVerificationNotifier` (generate_code + send_code); AuthService — чистый оркестратор |
| `analytics/builder.py` | Чистый компилятор AST→Mongo pipeline, без I/O и веб-зависимостей | Без изменений (соответствует правилам) |
| `minio/service.py` | Инфраструктурный адаптер S3 с доменными ошибками | Без изменений |
| `users/views/*` | SQL/маппинга в роутерах нет | Без изменений |
| `mongo/history.py`, `mongo/analytics.py` | Глупый I/O | Без изменений |

## Принятые пограничные решения (не дефекты)

- `store/dependecies.py`, `health/exceptions.py`, `jsonwebtoken/utils.py` —
  `HTTPException`/`select` в **web-boundary** слое (FastAPI Depends / health-check):
  это часть HTTP-контура, а не бизнес-сервисов. Вынос user-lookup'ов из
  jsonwebtoken в UserRepository — кандидат на следующую итерацию, не блокер.
- `get_records` фильтры в `RecordRepository` проходят `validate_dict_keys` —
  injection-защита уровня I/O (см. выше).
- Транзакции (`commit`/`rollback`) остаются в сервисах-оркестраторах —
  репозитории не коммитят (unit-of-work на стороне сервиса).
