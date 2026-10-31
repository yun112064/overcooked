
import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI

from app.api.auth import router as auth_router
from app.api.errors import register_error_handlers
from app.api.health import router as health_router
from app.api.households import router as household_router
from app.api.ingredients import router as ingredient_router
from app.api.jobs import router as job_router
from app.api.meal_requests import router as meal_request_router
from app.api.meal_results import router as meal_result_router
from app.api.meal_plans import router as meal_plan_router
from app.api.internal_knowledge import router as internal_knowledge_router
from app.api.members import router as member_router
from app.api.recognitions import router as recognition_router
from app.core.config import settings
from app.core.log_cleanup import cleanup


# 每 24 小時執行一次
CLEANUP_INTERVAL = 24 * 60 * 60


async def scheduled_log_cleanup():
    while True:
        try:
            apply = (
                os.getenv("LOG_CLEANUP_APPLY", "false").lower()
                == "true"
            )

            # 放到執行緒，避免阻塞 API
            await asyncio.to_thread(cleanup, apply=apply)

        except SystemExit as exc:
            logging.getLogger("uvicorn.error").error(
                "Log cleanup stopped: %s", exc
            )
        except Exception:
            logging.getLogger("uvicorn.error").exception(
                "Log cleanup failed"
            )

        await asyncio.sleep(CLEANUP_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 正式部署由單一排程程序（app.jobs.scheduler）清理；僅在明確設定時於 API 內執行
    if not settings.log_cleanup_in_api:
        yield
        return
    task = asyncio.create_task(scheduled_log_cleanup())

    try:
        yield
    finally:
        task.cancel()

        with suppress(asyncio.CancelledError):
            await task


app = FastAPI(
    title="Family Meal Planner API",
    version="0.1.0",
    lifespan=lifespan,
)

register_error_handlers(app)

app.include_router(health_router)
app.include_router(auth_router)
app.include_router(household_router)
app.include_router(member_router)
app.include_router(ingredient_router)
app.include_router(meal_request_router)
app.include_router(job_router)
app.include_router(recognition_router)
app.include_router(meal_result_router)
app.include_router(meal_plan_router)
app.include_router(internal_knowledge_router)
