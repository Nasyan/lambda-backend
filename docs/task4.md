Этап 1: Инфраструктурный фундамент (Подготовка базы)
Сначала необходимо настроить окружение, логику удаления данных и глобальную обработку ошибок, чтобы последующий код сразу писался по правильным стандартам.
1. Унификация конфигурации (config.py) и стадий разработки
Задача: Четко разделить стадии локальной разработки в config.py и унифицировать использование путей.
Логика (три стадии):
DEV_LOCAL: FastAPI и Dramatiq запущены локально (poetry/virtualenv), а базы данных (Postgres, Mongo, Redis) крутятся в Docker Compose. Это для создания бэка.
DEV_CONTAINER_BACK: Вся бэкенд-часть (FastAPI, Dramatiq, БД) упакована и работает внутри Docker Compose, но фронтенд разрабатывается локально (подключается снаружи).
DEV_FULL_COMPOSE: Полное окружение, включая фронтенд, бэкенд и базы, работает в единой сети Docker Compose.
Директории: config.py. Источник истины здеьк который задает статус - это .env вот он:
APP_MODE="dev"


# --- Postgres ---
POSTGRES_DB_HOST="postgres"
POSTGRES_DB_PORT="5432"
POSTGRES_DB_USER="postgres_user"
POSTGRES_DB_PASSWORD="123"
POSTGRES_DB_NAME="db"


POSTGRES_TEST_DB_HOST="test-postgres"
POSTGRES_TEST_DB_PORT="5433"
POSTGRES_TEST_DB_NAME="test_db"


# Email
SENDER_EMAIL="codywantmoney@gmail.com"
EMAIL_PASSWORD="pgejwvsjfjrefhdl"


# --- Redis ---
REDIS_HOST="redis"
REDIS_PORT="6379"           # внутренний/основной
REDIS_EXTERNAL_PORT="6380"  # внешний для локала
REDIS_TEST_PORT="6381"


EMAIL_DB="0"
TASK_DB="1"
REGISTRATION_DB="2"
LOGIN_DB="3"


JOIN_PREFIX="100"
RESET_PREFIX="200"
SET_ACTIVITY="300"
INVITE_PREFIX="400"
USER_INVITE_PREFIX="500"


# --- MongoDB ---
MONGO_HOST="mongodb"
MONGO_PORT="27017"
MONGO_EXTERNAL_PORT="27017"
MONGO_TEST_PORT="27018"
MONGO_DB_NAME="lambda_db"
MONDO_DB_PASSWORD='123'
MONGO_TEST_NAME="test_mogno"


# --- MinIO (S3) ---
MINIO_HOST="minio"
MINIO_PORT="9000"
MINIO_CONSOLE_PORT="9001"
MINIO_EXTERNAL_PORT="9000"
MINIO_TEST_PORT="9002"
MINIO_ROOT_USER="minio_admin"
MINIO_ROOT_PASSWORD="super_minio_password_123"
MINIO_DEFAULT_BUCKET="lambda-media"


# --- Admin / Auth ---
ADMIN_USERNAME="admin"
ADMIN_PASSWORD="superpassword"


# Next JS. "docker-dev" for docker-compose.prod.yml. "dev" for docker-compose.yml
NEXT_PUBLIC_STAGE="docker-dev"
NEXT_PUBLIC_API_HOST_DEV="127.0.0.1"
NEXT_PUBLIC_API_PORT_DEV="8000"
NEXT_PUBLIC_API_HOST_PROD="lambda.com"
2. Переработка глобальной системы исключений (Exceptions)
Задача: Уйти от ручной проброски статус-кодов в роутерах. Каждое кастомное исключение должно само «знать» свой HTTP-статус.
Логика: Сейчас exceptions идут в exceptions/handler, где список преобразует их в HTTP-статусы. Нужно писать значение статуса прямо внутри классов exceptions, а в handler просто парсить этот атрибут.
Директории: exceptions/ и по всему проекту.
3. Перевод системы на Soft Delete (is_deleted)
Задача: Заменить физическое удаление на мягкое (is_deleted = True). Касается только history, records и templates.
Логика: Данные в кастомных таблицах templates нужно удалять аккуратно для возможности восстановления. Добавить эндпоинт восстановления удаленного template со всеми его records. Добавить эндпоинт списка удаленных templates. Аналогично для records.
Директории: template, records.
Важно: Написать множество тестов. Обратить особое внимание, чтобы ни один существующий тест по проекту не упал.
4. Логирование запросов в MongoDB
Задача: Реализовать логирование запросов Mongo для понимания их количества и структуры (особенно важно для триггеров).
Логика: Внедрить логирование на уровне Mongo-репозитория или через декораторы. В логах должно быть видно: какой запрос ушел, сколько документов затронуто, время выполнения. Интегрировать с существующей директорией logs/.
Этап 2: Транспортный уровень (Фоновые задачи)
Настройка "рельс" для асинхронных задач перед переходом к бизнес-логике.
5. Полная ревизия работы Dramatiq
Задача: Проверить работоспособность всех воркеров Dramatiq локально и в контейнерах, настроить стабильное переподключение к Redis.
6. Асинхронная рассылка Email через Dramatiq
Задача: Проверить реализацию task для Dramatiq — send_email.
Логика: Задача принимает receiver_email, subject, body, подключается к SMTP и отправляет письмо. Полная изоляция от бизнес-логики CRM.
Директории: workers и scheduler.
Этап 3: Ядро Workflow-движка (Уведомления и Триггеры)
Контекст: Необходимо довести до ума систему уведомлений в CRM. Система отправляет динамические шаблоны писем (с переменными вроде {{ client.name }}) на основе ручных действий, реактивных триггеров или расписания (Cron).
Ключевой архитектурный нюанс: В проекте уже реализован мощный 4-этапный движок триггеров:
EVENT (EventReceptor ловит ON_CREATE, ON_UPDATE, ON_TIME).
CONDITION (ContextBoundResolver + ASTEvaluator вычисляют True/False или транслируют AST в поисковый запрос к MongoDB).
PAYLOAD AST (BatchDataLoader собирает данные, IterationLoopEngine управляет итерациями).
ACTION (Выполнение целевого действия).
Задача агента: НЕ изобретать AST-парсеры с нуля. Переиспользовать этот пайплайн для экшенов уведомлений, строго разделив вычисления и транспорт.
7. Валидация переменных в NotificationTemplate (на этапе создания)
Задача: Обновить сервис создания/редактирования NotificationTemplate. При сохранении шаблона бэкенд должен распарсить title и body на предмет переменных.
Логика: Запросить из MongoDB схему динамической таблицы (CRM Template). Если переменной нет в схеме — отдавать HTTP 400 Validation Error (используя новую систему исключений из Шага 2) и не сохранять шаблон в Postgres.
8. Динамический поиск получателей через AST (ActionRegistry._resolve_recipients)
Задача: Реализовать логику парсинга получателей, заменяя текущую заглушку и используя существующие ContextBoundResolver и ASTEvaluator.
Логика: Конфиг recipients_config в шаблоне поддерживает формат {"type": "ast_tree", "tree": {...}, "contact_field": "work_email"} (плюс возможность выбрать конкретных юзеров). Метод скармливает tree вычислителю, делает запрос к MongoDB, извлекает значения из поля contact_field и возвращает плоский список строк (адресов/ID).
9. Интеграция NotificationDispatcher с Dramatiq и CRM-инбоксом
Задача: Доработать NotificationDispatcher.dispatch, который вызывается внутри create_crm_notification.
Логика: Принимает title, body, список recipients и массив channels (например, ["crm", "email"]).
Если "crm" — пишет в Postgres таблицы NotificationHistory и NotificationInbox (для колокольчика).
Если "email" — проходит по списку recipients и ставит задачи в Dramatiq: send_email.send(...).
10. Пакетная обработка Cron-триггеров (ON_TIME)
Задача: Дописать AutomationService.process_cron_triggers() в workers/crm_tasks.py, интегрировав Cron с EventReceptor.
Логика: Найти триггеры с event_type = EventType.CRON (или ON_TIME). ContextBoundResolver транслирует condition_ast в запрос (например, "ДР == сегодня"). BatchDataLoader достает записи из Mongo. Экшен create_crm_notification рендерит шаблоны под каждую запись и отдает в NotificationDispatcher.
Важно: Оставь существующий механизм блокировок (Redis lock) в cron-воркере без изменений. Прописать полные тесты.
Этап 4: Оптимизация, Аналитика и Данные (Экспорт/Импорт)
Тяжелые операции с данными и кэширование для снижения нагрузки.
11. Кэширование данных
Задача: Внедрить кэширование (Redis) для часто запрашиваемых и редко изменяемых данных (схемы инстансов, настройки шаблонов). Добавить отдельную db внутри Redis. Прописать тесты на важных местах с кэшом.
12. Выгрузка Records и Аналитики
Задача: Создать эндпоинты для выгрузки данных из Mongo с учетом фильтров. Отдельно реализовать скачивание отчетов аналитики в формате CSV и других форматах.
Важно: Написать тесты на выгрузку CSV с объекта аналитики или records с применением фильтров.
13. Полный Экспорт / Импорт всей схемы инстанса
Задача: Инструмент для резервного копирования или миграции.
Логика: Эндпоинт для выгрузки одного JSON, содержащего мета-структуру тенанта из Postgres (схемы таблиц, триггеры, шаблоны уведомлений, политики, аналитики). Эндпоинт для внедрения такого JSON и создания структуры одним кликом. Обязательна валидация через SchemaIntegrityValidator в engine. Добавить тесты полной выгрузки и загрузки.
Этап 5: Тестирование бизнес-сценариев (E2E тесты)
Финальный этап проверки работы всей цепочки.
14. Доработка тестов History и логики триггеров
Задача: Написать тесты на историю уведомлений и покрыть созданный функционал комплексными бизнес-сценариями (по аналогии с playground/tests/test_business_notification.py или test_business_scenarios.py):
Тест "Смена статуса" (ON_UPDATE): Клиент перешел из статус 1 в статус 2. Триггер замечает это и отправляет уведомление по шаблону выбранным сотрудникам.
Тест "День рождения" (CRON в 6 утра): Триггер проверяет AST-дерево ("дата рождения == сегодня"). Уведомление уходит на почту клиента (если выбрано) и летит в колокольчик CRM ("crm") прикрепленному сотруднику.
Тест "Забытый клиент" (CRON): Проверка условия: клиенты с полем последнее_касание > 3 дней назад. Идет массовая рассылка (Bulk Notification) определенной группе сотрудников.



