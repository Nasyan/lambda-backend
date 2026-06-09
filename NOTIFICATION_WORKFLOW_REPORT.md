# Notification Workflow Report

## Контекст

Ветка: `feature/notification-workflow`.

Задача реализована через существующий trigger-engine v2 pipeline:
`EventReceptor` / `EvaluationScope` / `ASTEvaluator` / `BatchDataLoader` /
`ActionDispatcher`. Отдельный `ContextBoundResolver` в кодовой базе не найден;
ближайший фактический resolver runtime для Mongo-запросов - связка
`ASTEvaluator(batch_loader=BatchDataLoader(...))`, а legacy resolver есть как
`RecordResolverSession` в `engine/context.py`.

## Шаг 1. Валидация переменных NotificationTemplate

Файлы:
- `notifications/schemas.py`
- `notifications/service.py`
- `core/services/template_integrity.py`
- `triggers/interpolator.py`
- `notifications/tests/test_api.py`

Что сделано:
- В `TemplateCreate` / `TemplateUpdate` добавлены поля `source_template_uuid` и
  `entity_mappings`.
- `NotificationTemplateService.create_template()` теперь валидирует `{{...}}`
  до создания SQLAlchemy-модели и до `commit`.
- `TemplateIntegrityService.validate_notification_template()` теперь проверяет:
  - `{{name}}` и `{{data.name}}` через `source_template_uuid`;
  - `{{client.name}}` через `entity_mappings={"client": "<template_uuid>"}`;
  - отсутствие binding для шаблона с переменными как HTTP 400
    `SCHEMA_VALIDATION_ERROR`.
- `ContextInterpolator` теперь для `{{name}}` делает fallback в `document["data"]`,
  чтобы runtime соответствовал новой валидации.
- Добавлен тест на успешное сохранение валидной переменной и 400 без записи в
  Postgres для несуществующего поля.

Дизайн-решение:
- Binding не сохраняется в `notification_templates`, потому что в текущей модели
  Postgres нет соответствующих колонок и задача не требовала миграцию. Поля
  используются как save-time validation context.

## Шаг 2. Resolve recipients через существующий AST pipeline

Файлы:
- `triggers/actions.py`

Что сделано:
- `ActionRegistry._resolve_recipients()` переведён в async.
- `static` формат остался совместимым: `uuids`, `user_uuids`,
  `employee_uuids`, `emails`, `recipients`, `values`.
- Добавлена поддержка выбора сотрудников:
  - `{"type": "users", "user_uuids": [...]}`
  - `{"type": "employees", "employee_uuids": [...]}`
  - `{"type": "all_employees"}` / `{"type": "all_users"}` /
    `{"type": "users", "selection": "all"}`
- Добавлена поддержка требуемого AST-формата:
  `{"type":"ast_tree","tree":{...},"contact_field":"work_email"}`.
- AST вычисляется через `ASTEvaluator` и `BatchDataLoader`, затем из результата
  извлекается строго поле `data.<contact_field>`; для `_id` и `uuid` разрешено
  чтение top-level системного поля.
- Список получателей нормализуется в плоский deduplicated список строк.

## Шаг 3. NotificationDispatcher CRM/email

Файлы:
- `notifications/dispatcher.py`

Что сделано:
- Канал `crm` создаёт `NotificationHistory` и `NotificationInbox`.
- Канал `email` на каждого recipient ставит Dramatiq-задачу через существующий
  actor `workers.email_tasks.send_email.send(...)`.
- SMTP credentials берутся из существующего `config.SENDER_EMAIL` и
  `config.EMAIL_PASSWORD`, без hardcode.

Дизайн-решение:
- Email не отправляется напрямую. Dispatcher только enqueue-ит Dramatiq actor.

## Шаг 4. Cron / ON_TIME workflow processing

Файлы:
- `triggers/service.py`
- `triggers/models.py`
- `triggers/schemas.py`

Что сделано:
- `process_cron_triggers()` выбирает `EventType.CRON` и `EventType.ON_TIME`.
- В enum `EventType` добавлен `ON_TIME`; `TriggerCreate` требует
  `cron_expression` и для `CRON`, и для `ON_TIME`.
- Старый per-record `_evaluate_condition()` path в cron loop заменён на общий
  `_run_trigger_pipeline()`, где condition и payload вычисляются через
  `ASTEvaluator`.
- Для cron trigger загружается source schema из Mongo `TemplateRepository`, и
  `EvaluationScope` получает `source_schema`.
- `BatchDataLoader` создаётся на cron trigger и переиспользуется в batch loop.
- Skipped records больше не логируются как successful processing.
- Для reactive/manual event pipeline добавлен `pg_session.commit()` после
  успешной обработки события, чтобы CRM notification records не терялись после
  `flush()` в `NotificationDispatcher`.

Что не менялось:
- `workers/crm_tasks.py` и Redis-lock не тронуты.

## Шаг 5. Business E2E tests

Файлы:
- `playground/tests/test_notification_workflows.py`
- `notifications/tests/test_api.py`

Добавлены сценарии:
- Reactive `ON_RECORD_UPDATE`: клиент меняет статус `new -> won`, trigger
  уведомляет выбранного сотрудника в CRM inbox.
- Cron "День рождения": cron 6:00 находит клиента с `birth_date == today`;
  один trigger ставит email Dramatiq-задачу клиенту через `ast_tree`
  recipients_config, второй trigger создаёт CRM inbox ответственному сотруднику.
- Cron "Забытый клиент": условие `diff_days(now, last_touch_at) > 3`, bulk
  CRM-уведомление активным сотрудникам через `all_employees`.

Тесты снабжены комментариями "что происходит" и используют публичные API для
создания CRM templates, records, notification templates и triggers.

## Проверки

Пройдено:
- `python -m py_compile core/services/template_integrity.py notifications/schemas.py notifications/service.py notifications/dispatcher.py triggers/actions.py triggers/interpolator.py triggers/service.py notifications/tests/test_api.py playground/tests/test_notification_workflows.py`
- `python -m py_compile triggers/models.py triggers/schemas.py triggers/service.py playground/tests/test_notification_workflows.py`
- `git diff --check`

Не удалось выполнить:
- `pytest playground/tests/test_notification_workflows.py notifications/tests/test_api.py::TestNotification::test_notification_template_validates_variables_against_crm_schema -q`
  - причина: `ModuleNotFoundError: No module named 'aioboto3'` при загрузке
    корневого `conftest.py`.
- Standalone import-level helper check
  - причина: текущий interpreter `Python 3.9.23`, `SQLAlchemy 1.4.15`;
    проектный код импортирует `sqlalchemy.ext.asyncio.async_sessionmaker`,
    которого нет в этой версии SQLAlchemy.

Окружение проверки:
- `python --version`: `Python 3.9.23`
- `SQLAlchemy`: `1.4.15`
- `aioboto3`: не установлен

## Коммиты

Коммиты по шагам были запрошены, но заблокированы sandbox-правами:

```text
fatal: Unable to create '/home/aiagent/assistant/git/lambda-backend/.git/index.lock': Read-only file system
```

Поэтому изменения оставлены в рабочем дереве без commit и без push.
Незнакомый untracked `.entire/` не трогался.
