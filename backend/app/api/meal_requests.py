import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, current_auth, current_household_id, verify_origin
from app.api.errors import ApiError, not_found, version_conflict
from app.db.models import AdoptedMealPlan, MealRequest, MealRequestRevision, Member
from app.db.session import get_db
from app.services import ingredients
from app.services import recipe_retrieval
from app.services import jobs as job_service
from app.services import meal_request as service
from app.services import t12_planning
from app.services import knowledge_explanation
from app.services import meal_plans
from app.db.session import SessionLocal

router = APIRouter(prefix="/meal-requests", tags=["meal-requests"], dependencies=[Depends(verify_origin)])


class DraftUpdate(BaseModel):
    version: int = Field(ge=1)
    draft: service.MealRequestDraft


class NutritionPreviewBody(BaseModel):
    recipe_ids: list[str] = Field(min_length=1, max_length=10)


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
        "available_ingredients": ingredients.describe(db, draft.available_ingredient_ids),
        "items": service.items(draft),
        "restrictions": service.restrictions(db, draft, members),
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


@router.get("/{request_id}/recipes")
def retrieve_recipes(request_id: uuid.UUID, household_id=Depends(current_household_id),
                     db: Session = Depends(get_db)):
    """T11 preview only; nutrition/budget validation and AI generation belong to T12–T14."""
    row = _get_owned(db, household_id, request_id)
    draft = service.load(row.payload)
    _reject_bad_references(db, household_id, draft)
    members = _members(db, household_id)
    issues = service.completeness(draft, members)
    if issues:
        raise ApiError(422, "INVALID_INPUT", "請先完成問卷。", {"issues": issues})
    try:
        # 略過這一版問卷「不採用」過的組合（2026-11-03）
        result = meal_plans.current_menu(db, household_id, row, draft, members)
    except recipe_retrieval.RecipeRetrievalError as exc:
        raise ApiError(503, exc.code, exc.message) from None
    adopted = db.scalar(select(AdoptedMealPlan).where(AdoptedMealPlan.meal_request_id == row.id))
    return {**result, 'input_version': row.version,
            'fallback_eligible': result['status'] == recipe_retrieval.NO_FEASIBLE,
            'adopted_plan': meal_plans.summary(adopted) if adopted is not None else None}


class RejectBody(BaseModel):
    version: int = Field(ge=1)
    recipe_ids: list[str] = Field(min_length=1, max_length=10)


@router.post("/{request_id}/menu-rejections", status_code=204)
def reject_menu(request_id: uuid.UUID, body: RejectBody, auth: AuthContext = Depends(current_auth),
                household_id=Depends(current_household_id), db: Session = Depends(get_db)):
    """「不採用，換一份」：記錄這組食譜，同一版問卷下次查詢改給下一組。"""
    row = _get_owned(db, household_id, request_id, lock=True)
    meal_plans.reject(db, row, version=body.version, recipe_ids=body.recipe_ids, user_id=auth.user_id)
    db.commit()
    return Response(status_code=204)


@router.post("/{request_id}/nutrition-preview")
def nutrition_preview(request_id: uuid.UUID, body: NutritionPreviewBody,
                      household_id=Depends(current_household_id), db: Session = Depends(get_db)):
    """T12 result for selected T11 recipe IDs; never accepts client nutrition facts."""
    row = _get_owned(db, household_id, request_id)
    draft = service.load(row.payload)
    members = _members(db, row.household_id)
    issues = service.completeness(draft, members)
    if issues:
        raise ApiError(422, 'INVALID_INPUT', '請先完成問卷。', {'issues': issues})
    try:
        return t12_planning.preview(db, draft=draft, members=members, recipe_ids=body.recipe_ids)
    except t12_planning.T12PlanningUnavailable as exc:
        raise ApiError(503, exc.code, exc.message) from None


@router.post("/{request_id}/knowledge-tips")
def knowledge_tips(request_id: uuid.UUID, body: NutritionPreviewBody,
                   household_id=Depends(current_household_id), db: Session = Depends(get_db)):
    """T12 之後的營養小知識：後端重算最終食材 → T11 檢索審核知識 → 雲端 OpenAI 生成（經 T10）。

    不接受前端傳來的食材或知識；缺知識、知識服務故障或額度不足都不阻擋採用，只回報狀態與原因。
    """
    row = _get_owned(db, household_id, request_id)
    draft = service.load(row.payload)
    members = _members(db, row.household_id)
    issues = service.completeness(draft, members)
    if issues:
        raise ApiError(422, 'INVALID_INPUT', '請先完成問卷。', {'issues': issues})
    try:
        menu = t12_planning.preview(db, draft=draft, members=members, recipe_ids=body.recipe_ids)
    except t12_planning.T12PlanningUnavailable as exc:
        raise ApiError(503, exc.code, exc.message) from None
    food_ids = list(menu.get('final_food_ids') or [])
    db.rollback()  # 釋放讀取交易；以下的付費呼叫用各自的短交易
    result = knowledge_explanation.tips_for_menu(
        SessionLocal, final_food_ids=food_ids, household_id=household_id,
        request_tag=f'tips:{request_id}:{row.version}', names=ingredients.names(db, food_ids) if food_ids else {})
    return {**result, 'input_version': row.version, 'recipe_ids': body.recipe_ids,
            'nutrition_status': menu.get('status')}


@router.put("/{request_id}")
def update_request(request_id: uuid.UUID, body: DraftUpdate, auth: AuthContext = Depends(current_auth),
                   household_id=Depends(current_household_id), db: Session = Depends(get_db)):
    row = _get_owned(db, household_id, request_id, lock=True)
    if row.status in ("queued", "running"):
        raise ApiError(409, "VERSION_CONFLICT", "此問卷正在生成中，請等待結果後再修改。",
                       {"reason": "GENERATION_IN_PROGRESS"})
    if row.version != body.version:
        raise version_conflict()
    _reject_bad_references(db, household_id, body.draft)
    payload = service.dump(body.draft)
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
    row = _get_owned(db, household_id, request_id, lock=True)
    complete = not service.completeness(service.load(row.payload), _members(db, household_id))
    job, created = job_service.submit_generation(
        db, household_id=household_id, meal_request=row, version=body.version,
        idempotency_key=idempotency_key, complete=complete)
    db.commit()
    return {"job": job_service.public_view(job), "created": created}
