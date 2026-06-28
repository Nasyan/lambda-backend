import uvicorn
from fastapi import FastAPI
from contextlib import asynccontextmanager
from fastapi.middleware.cors import CORSMiddleware

from health.views import router as health_router
from users.views.creator_api import router as creator_router
from users.views.admin_api import router as admin_router
from users.views.user_api import router as auth_router
from users.views.profile_api import router as profile_router
from core.views.template import router as instance_router
from core.views.history import router as history_router
from minio.views import router as storage_router
from triggers.views import router as trigger_router
from analytics.views import router as analytics_router
from store.views import router as store_router
from policy.views import router as policy_router
from users.views.client_api import router as client_router
from notifications.views import router as notification_router
from instance_schema.views import router as instance_schema_router
from core.views.record import create_records_router
from users.models import AppTools
from redisdb.utils import init_redis, close_redis
from mongo.db import mongo_manager
from database.db import SessionLocal
from users.utils import init_admin
from minio.db import init_s3_storage
from exceptions.handlers import register_exception_handlers
from logs.config import setup_logging
from logs.middleware import StructuredLoggingMiddleware
from config import MODE

setup_logging(MODE)


@asynccontextmanager
async def lifespan(app: FastAPI):
    mongo_manager.connect()
    await init_redis()
    await init_s3_storage()
    async with SessionLocal() as session:
        try:
            await init_admin(session)
        except Exception as e:
            print(f"Error during admin initialization: {e}")

    yield
    await close_redis()
    mongo_manager.close()


app = FastAPI(lifespan=lifespan)

app.add_middleware(StructuredLoggingMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["Content-Type", "Authorization", "Range", "Accept-Ranges"],
)

app.include_router(health_router)
app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(creator_router)
app.include_router(instance_router)
app.include_router(history_router)
app.include_router(storage_router)
app.include_router(trigger_router)
app.include_router(analytics_router)
app.include_router(store_router)
app.include_router(policy_router)
app.include_router(client_router)
app.include_router(notification_router)
app.include_router(instance_schema_router)
app.include_router(profile_router)

register_exception_handlers(app)

target_tools = [AppTools.WORKFLOW, AppTools.NOTES, AppTools.TABLES]
for tool in target_tools:
    generated_router = create_records_router(tool)
    app.include_router(generated_router)


if __name__ == "__main__":
    try:
        host = "localhost"
        port = 8000
        print(f"SWAGGER - http://{host}:{port}/docs")
        uvicorn.run("main:app", host=host, port=port, reload=False)
    except Exception as e:
        print(e)
