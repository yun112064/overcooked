import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_household_id
from app.api.errors import not_found
from app.db.models import BackgroundJob
from app.db.session import get_db
from app.services.jobs import public_view

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("")
def list_jobs(meal_request_id: uuid.UUID | None = Query(None), kind: str | None = Query(None, max_length=40),
              household_id=Depends(current_household_id), db: Session = Depends(get_db)):
    """kind 可篩選工作種類（meal_generation、knowledge_retry、price_retry）。"""
    query = select(BackgroundJob).where(BackgroundJob.household_id == household_id)
    if meal_request_id is not None:
        query = query.where(BackgroundJob.meal_request_id == meal_request_id)
    if kind is not None:
        query = query.where(BackgroundJob.kind == kind)
    rows = db.scalars(query.order_by(BackgroundJob.created_at.desc()).limit(50)).all()
    return {"jobs": [public_view(j) for j in rows]}


@router.get("/{job_id}")
def get_job(job_id: uuid.UUID, household_id=Depends(current_household_id), db: Session = Depends(get_db)):
    job = db.get(BackgroundJob, job_id)
    if job is None or job.household_id != household_id:
        raise not_found("找不到此工作。")
    return public_view(job)
