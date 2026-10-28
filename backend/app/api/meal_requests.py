import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, current_auth, current_household_id, verify_origin
from app.api.errors import ApiError, already_adopted, not_found, version_conflict
from app.db.models import MealRequest, MealRequestRevision, Member
from app.db.session import get_db
from app.services import ingredients
from app.services import jobs as job_service
from app.services import meal_request as service

router = APIRouter(prefix="/meal-requests", tags=["meal-requests"], dependencies=[Depends(verify_origin)])


class DraftUpdate(BaseModel):
    version: int = Field(ge=1)
    draft: service.MealRequestDraft


def _members(db: Session, household_id) -> dict:
    return {m.id: m for m in db.scalars(select(Member).where(Member.household_id == household_id))}


def _check(db: Session, draft: service.MealRequestDraft, members: dict) -> tuple[list[dict], dict, object]:
    """待補項目（含預算下限）、目標預覽與預算下限；問卷讀取與送出共用同一套判斷。"""
    issues = service.completeness(draft, members)
    preview = service.target_preview(draft, members)
    floor = service.budget_floor(db, draft, preview)
    low = service.budget_issue(draft, floor)
    if low:
        issues.append(low)
    return issues, preview, floor


def _out(db: Session, row: MealRequest) -> dict:
    draft = service.load(row.payload)
    members = _members(db, row.household_id)
    issues, preview, floor = _check(db, draft, members)
    return {
        "id": str(row.id),
        "status": row.status,
        "version": row.version,
        "updated_at": row.updated_at.isoformat(),
        "draft": service.dump(draft),
        "available_ingredients": ingredients.describe(db, draft.available_ingredient_ids),
        "items": service.items(draft),
        "restrictions": service.restrictions(db, draft, members),
        "targets_preview": preview,
        "budget_floor": f"{floor:f}" if floor is not None else None,
        "issues": issues,
        "complete": not issues,
    }


def get_owned_request(db: Session, household_id, request_id: uuid.UUID, *, lock: bool = False) -> MealRequest:
    """本家庭的問卷（他家庭一律 404）；lock 時 SELECT ... FOR UPDATE。結果與決定 API 共用。"""
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


@router.get("/staple-options")
def staple_options(household_id=Depends(current_household_id)):
    """決策 #52：問卷主食欄只能從此清單選（含「不需要主食」）；需在 /{request_id} 之前註冊。"""
    return service.staple_options()


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
    return _out(db, get_owned_request(db, household_id, request_id))


@router.put("/{request_id}")
def update_request(request_id: uuid.UUID, body: DraftUpdate, auth: AuthContext = Depends(current_auth),
                   household_id=Depends(current_household_id), db: Session = Depends(get_db)):
    row = get_owned_request(db, household_id, request_id, lock=True)
    payload = service.dump(body.draft)
    if row.version == body.version and payload == row.payload:
        # 內容沒變（例如回看問卷）：不升版、不改狀態，已有的結果與採用都保留
        db.rollback()
        return _out(db, row)
    if row.status in ("queued", "running"):
        raise ApiError(409, "VERSION_CONFLICT", "此問卷正在生成中，請等待結果後再修改。",
                       {"reason": "GENERATION_IN_PROGRESS"})
    if row.status == "confirmed":
        raise already_adopted()
    if row.version != body.version:
        raise version_conflict()
    _reject_bad_references(db, household_id, body.draft)
    row.version += 1
    # 失敗或已有結果後再修改：回到草稿，新版本需重新送出生成
    row.status = "draft"
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
    get_owned_request(db, household_id, request_id)
    revision = db.get(MealRequestRevision, (request_id, version))
    if revision is None:
        raise not_found("找不到此版本。")
    return {"id": str(request_id), "version": version, "draft": revision.payload,
            "created_at": revision.created_at.isoformat()}


@router.delete("/{request_id}", status_code=204)
def delete_request(request_id: uuid.UUID, household_id=Depends(current_household_id),
                   db: Session = Depends(get_db)):
    row = get_owned_request(db, household_id, request_id)
    if row.status in ("queued", "running"):
        raise ApiError(409, "VERSION_CONFLICT", "此問卷正在生成中，不能刪除。",
                       {"reason": "GENERATION_IN_PROGRESS"})
    db.delete(row)
    db.commit()
    return Response(status_code=204)


class SubmitBody(BaseModel):
    version: int = Field(ge=1)


@router.post("/{request_id}/submit", status_code=202)
def submit_request(request_id: uuid.UUID, body: SubmitBody,
                   idempotency_key: str = Header(min_length=8, max_length=100),
                   household_id=Depends(current_household_id), db: Session = Depends(get_db)):
    """送出生成：相同 Idempotency-Key 與相同問卷版本回傳同一工作，不重複建立或扣額度。"""
    row = get_owned_request(db, household_id, request_id, lock=True)
    complete = not _check(db, service.load(row.payload), _members(db, household_id))[0]
    job, created = job_service.submit_generation(
        db, household_id=household_id, meal_request=row, version=body.version,
        idempotency_key=idempotency_key, complete=complete)
    db.commit()
    return {"job": job_service.public_view(job), "created": created}


@router.post("/{request_id}/cancel")
def cancel_generation(request_id: uuid.UUID, household_id=Depends(current_household_id),
                      db: Session = Depends(get_db)):
    """取消排隊中或執行中的生成；問卷回到送出前的狀態，可再修改或重新送出。"""
    row = get_owned_request(db, household_id, request_id, lock=True)
    job = job_service.cancel_generation(db, row)
    if job is None:
        raise ApiError(409, "VERSION_CONFLICT", "這份問卷目前沒有進行中的生成。", {"reason": "NOTHING_TO_CANCEL"})
    db.commit()
    return {"job": job_service.public_view(job), "meal_request": _out(db, row)}
