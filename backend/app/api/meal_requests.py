import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, current_auth, current_household_id, verify_origin
from app.api.errors import ApiError, not_found, version_conflict
from app.db.models import MealRequest, MealRequestRevision, Member
from app.db.session import get_db
from app.services import ingredients
from app.services import meal_request as service

router = APIRouter(prefix="/meal-requests", tags=["meal-requests"], dependencies=[Depends(verify_origin)])


class DraftUpdate(BaseModel):
    version: int = Field(ge=1)
    draft: service.MealRequestDraft


def _members(db: Session, household_id) -> dict:
    return {m.id: m for m in db.scalars(select(Member).where(Member.household_id == household_id))}


def _out(db: Session, row: MealRequest) -> dict:
    draft = service.load(row.payload)
    members = _members(db, row.household_id)
    issues = service.completeness(draft, members)
    return {
        "id": str(row.id),
        "status": row.status,
        "version": row.version,
        "updated_at": row.updated_at.isoformat(),
        "draft": service.dump(draft),
        "available_ingredients": ingredients.describe(draft.available_ingredient_ids),
        "items": service.items(draft),
        "restrictions": service.restrictions(draft, members),
        "targets_preview": service.target_preview(draft, members),
        "issues": issues,
        "complete": not issues,
    }


def _get_owned(db: Session, household_id, request_id: uuid.UUID, *, lock: bool = False) -> MealRequest:
    query = select(MealRequest).where(MealRequest.id == request_id)
    if lock:
        query = query.with_for_update()
    row = db.scalar(query)
    if row is None or row.household_id != household_id:
        raise not_found("找不到此問卷。")
    return row


def _reject_bad_references(db: Session, household_id, draft: service.MealRequestDraft) -> None:
    problems = service.check_references(db, household_id, draft)
    if problems:
        first = problems[0]
        raise ApiError(422, "INVALID_INPUT", first["message"],
                       {"field": first["field"], "reason": first["reason"]})


@router.get("")
def list_requests(household_id=Depends(current_household_id), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(MealRequest).where(MealRequest.household_id == household_id)
        .order_by(MealRequest.updated_at.desc()).limit(50)
    ).all()
    return {"meal_requests": [
        {"id": str(r.id), "status": r.status, "version": r.version,
         "meal_date": r.meal_date.isoformat() if r.meal_date else None,
         "meal_type": r.payload.get("meal_type"), "updated_at": r.updated_at.isoformat()}
        for r in rows
    ]}


@router.post("", status_code=201)
def create_request(draft: service.MealRequestDraft, auth: AuthContext = Depends(current_auth),
                   household_id=Depends(current_household_id), db: Session = Depends(get_db)):
    _reject_bad_references(db, household_id, draft)
    payload = service.dump(draft)
    row = MealRequest(household_id=household_id, status="draft", version=1,
                      meal_date=draft.meal_date, payload=payload)
    db.add(row)
    db.flush()
    db.add(MealRequestRevision(meal_request_id=row.id, version=1, payload=payload, created_by=auth.user_id))
    db.commit()
    return _out(db, row)


@router.get("/{request_id}")
def get_request(request_id: uuid.UUID, household_id=Depends(current_household_id),
                db: Session = Depends(get_db)):
    return _out(db, _get_owned(db, household_id, request_id))


@router.put("/{request_id}")
def update_request(request_id: uuid.UUID, body: DraftUpdate, auth: AuthContext = Depends(current_auth),
                   household_id=Depends(current_household_id), db: Session = Depends(get_db)):
    row = _get_owned(db, household_id, request_id, lock=True)
    if row.status != "draft":
        raise ApiError(409, "VERSION_CONFLICT", "此問卷已送出，不能再修改；請建立新的問卷。",
                       {"reason": "NOT_DRAFT"})
    if row.version != body.version:
        raise version_conflict()
    _reject_bad_references(db, household_id, body.draft)
    payload = service.dump(body.draft)
    row.version += 1
    row.payload = payload
    row.meal_date = body.draft.meal_date
    row.updated_at = datetime.now(timezone.utc)
    db.add(MealRequestRevision(meal_request_id=row.id, version=row.version, payload=payload,
                               created_by=auth.user_id))
    db.commit()
    return _out(db, row)


@router.get("/{request_id}/versions/{version}")
def get_revision(request_id: uuid.UUID, version: int, household_id=Depends(current_household_id),
                 db: Session = Depends(get_db)):
    _get_owned(db, household_id, request_id)
    revision = db.get(MealRequestRevision, (request_id, version))
    if revision is None:
        raise not_found("找不到此版本。")
    return {"id": str(request_id), "version": version, "draft": revision.payload,
            "created_at": revision.created_at.isoformat()}


@router.delete("/{request_id}", status_code=204)
def delete_request(request_id: uuid.UUID, household_id=Depends(current_household_id),
                   db: Session = Depends(get_db)):
    row = _get_owned(db, household_id, request_id)
    if row.status != "draft":
        raise ApiError(409, "VERSION_CONFLICT", "只能刪除尚未送出的草稿。", {"reason": "NOT_DRAFT"})
    db.delete(row)
    db.commit()
    return Response(status_code=204)
