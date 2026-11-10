from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app.db.session import engine
from fastapi import APIRouter

from app.core.logging_config import api_logger


router = APIRouter()


@router.get("/health")
def health_check():
    api_logger.info(
        "Health check succeeded",
        extra={
            "event": "health_check",
            "source": "api",
        },
    )

    return {
        "status": "ok",
        "service": "backend",
    }


@router.get("/health/db")
def database_health_check():
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1")).scalar_one()

    except SQLAlchemyError:
        api_logger.error(
            "Database health check failed",
            extra={
                "event": "db_health_check_failed",
                "source": "api",
            },
        )
        raise HTTPException(
            status_code=503,
            detail="Database unavailable",
        )

    return {
        "status": "ok",
        "service": "database",
    }
