# mongo/tests/conftest.py

# Локальный override mongo_db удалён (task3, ГЗ-3): корневая фикстура mongo_db
# отдаёт чистую тестовую базу напрямую из session-scoped клиента, не поднимая
# FastAPI-приложение, Postgres и Redis — чистые Mongo-тесты стали легче.
