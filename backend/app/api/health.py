from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app.db.session import engine
from fastapi import APIRouter, Depends

from app.api.deps import current_auth

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


@router.get("/health/team")
def team_health(_auth=Depends(current_auth)):
    """團隊電腦服務（YOLO 辨識、知識 RAG）是否在線；需登入，結果快取 30 秒（pre-review #5）。"""
    from app.services.team_status import team_status
    return team_status()
