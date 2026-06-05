# Тестовая инфраструктура (task3, ГЗ-3)

## Архитектура фикстур (корневой conftest.py)

| Слой | Фикстуры | Scope | Что делают |
|---|---|---|---|
| Подключения | `postgres_engine`, `mongo_client`, `redis_pool` | session | Создаются один раз за прогон; схема PG — один `create_all` |
| Изоляция | `pg_session_factory`, `db_session` | function | Одно соединение + внешняя транзакция; `commit()` теста/приложения = SAVEPOINT; в конце ROLLBACK — таблицы не пересоздаются |
| Изоляция | `mongo_db` | function | `delete_many({})` по коллекциям (индексы сохраняются), база — `<MONGO_DB_NAME>_<xdist-worker>` |
| Изоляция | `redis_clean` | function | flushdb тестовых БД (анти-коллизии rate-limit) |
| Клиенты | `test_client` / `async_client` | function | Точечные overrides только get_db + get_mongo_db; S3 — отдельно `minio_client` |
| Конкурентность | `concurrent_test_client` + `create_committed_environment` | function | Реальные пуловые сессии (gather работает по-настоящему); изоляция — TRUNCATE после теста |

## Правила

- **Чистая логика не поднимает инфраструктуру**: юниты AST/валидаторов
  (engine/tests, юнит-классы в playground) не запрашивают `test_client` —
  ни Postgres, ни Redis, ни Mongo не стартуют. Чистые Mongo-тесты берут
  только `mongo_db` (без приложения и PG).
- **`db_session` видит то же, что и API**: одна транзакция на тест, поэтому
  паттерн «создал юзера через db_session → залогинился через API» работает
  без реальных коммитов.
- **Гонки** — только через `concurrent_test_client`: транзакционный клиент
  сериализует запросы одним соединением (это цена скорости), пуловый — нет.
- Требование: **pytest-asyncio >= 0.24** (loop_scope; все async-тесты
  автоматически переводятся в session-петлю хуком в conftest).

## Локальные фабрики (playground/tests/conftest.py)

`record_factory`, `trigger_factory`, `loyalty_crm_env` (+ исторические
`setup_crm_environment*`) — собирают JSON-пейлоады вне тестов; сами тесты
читаются как бизнес-сценарии.

## Бизнес-сценарии (playground/tests/)

- `test_trigger_engine_v2.py` — 3 базовых кейса master-спеки (task2).
- `test_engine_hardening.py` — dirty-кейсы ГЗ-2: двойной PATCH, частичный
  отказ батча, стресс DataLoader 10k ID, юнит-семантика $old/$new.
- `test_business_scenarios.py` — программа лояльности с threshold-crossing
  ($old/$new в каскадах через pre-images), двухзвенный каскад авто-дозаказа,
  422-защита от циклов A→B→A, контракты системных экшенов, изоляция
  тенантов, LIVE_EVAL с contains+ne, конкурентное списание остатков.

Ассерты во всех интеграционных сценариях проверяют финальное состояние
MongoDB (записи/остатки/награды) и PostgreSQL (метаданные триггеров),
а не только HTTP-ответы.
