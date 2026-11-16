import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, current_auth, current_household_id, verify_origin
from app.api.errors import ApiError, already_adopted, not_found, version_conflict
from app.core.config import settings
from app.db.models import AdoptedMealPlan, MealRequest, MealRequestRevision, Member
from app.db.session import SessionLocal, get_db
from app.services import database_menu_plans as menu_plans
from app.services import ingredients
from app.services import jobs as job_service
from app.services import knowledge_explanation
from app.services import meal_request as service
from app.services import recipe_catalog as catalog_service
from app.services import recipe_retrieval, t12_planning
from app.services.assistant import RateLimiter

router = APIRouter(prefix="/meal-requests", tags=["meal-requests"], dependencies=[Depends(verify_origin)])


class DraftUpdate(BaseModel):
    version: int = Field(ge=1)
    draft: service.MealRequestDraft


_tips_limiter = RateLimiter(settings.knowledge_tips_rate_per_minute)


def get_tips_limiter() -> RateLimiter:
    return _tips_limiter


class NutritionPreviewBody(BaseModel):
    recipe_ids: list[str] = Field(min_length=1, max_length=10)


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
         "meal_type": r.payload.get("meal_type"), "mode": r.payload.get("mode", "questionnaire"),
         "updated_at": r.updated_at.isoformat()}
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


@router.get("/{request_id}/recipes")
def retrieve_recipes(request_id: uuid.UUID, household_id=Depends(current_household_id),
                     db: Session = Depends(get_db)):
    """T11 資料庫菜單預覽（略過這一版問卷「不採用」過的組合）；營養與預算驗證在 nutrition-preview 與採用時重算。"""
    row = get_owned_request(db, household_id, request_id)
    draft = service.load(row.payload)
    adopted = db.scalar(select(AdoptedMealPlan).where(AdoptedMealPlan.meal_request_id == row.id))
    if draft.mode == "picker" and adopted is not None and adopted.result_version is None:
        selected = (adopted.snapshot or {}).get("selected") or []
        result = recipe_retrieval.RetrievalResult(recipe_retrieval.DATABASE_MENU, selected=selected).to_dict()
        return {**result, "input_version": row.version, "fallback_eligible": False,
                "adopted_plan": menu_plans.summary(adopted)}
    _reject_bad_references(db, household_id, draft)
    members = _members(db, household_id)
    issues = service.completeness(draft, members)
    if issues:
        raise ApiError(422, "INVALID_INPUT", "請先完成問卷。", {"issues": issues})
    try:
        result = menu_plans.current_menu(db, household_id, row, draft, members)
    except recipe_retrieval.RecipeRetrievalError as exc:
        raise ApiError(503, exc.code, exc.message) from None
    return {**result, "input_version": row.version,
            "fallback_eligible": result["status"] == recipe_retrieval.NO_FEASIBLE,
            "adopted_plan": menu_plans.summary(adopted) if adopted is not None else None}


class RejectBody(BaseModel):
    version: int = Field(ge=1)
    recipe_ids: list[str] = Field(min_length=1, max_length=10)


@router.post("/{request_id}/menu-rejections", status_code=204)
def reject_menu(request_id: uuid.UUID, body: RejectBody, auth: AuthContext = Depends(current_auth),
                household_id=Depends(current_household_id), db: Session = Depends(get_db)):
    """「不採用，換一份」：記錄這組食譜，同一版問卷下次查詢改給下一組。"""
    row = get_owned_request(db, household_id, request_id, lock=True)
    menu_plans.reject(db, row, version=body.version, recipe_ids=body.recipe_ids, user_id=auth.user_id)
    db.commit()
    return Response(status_code=204)


@router.post("/{request_id}/nutrition-preview")
def nutrition_preview(request_id: uuid.UUID, body: NutritionPreviewBody,
                      household_id=Depends(current_household_id), db: Session = Depends(get_db)):
    """所選食譜的 T12 營養計算；只用伺服器端的問卷、食譜與營養資料，不接受前端傳來的營養數值。"""
    row = get_owned_request(db, household_id, request_id)
    draft = service.load(row.payload)
    _reject_bad_references(db, row.household_id, draft)
    members = _members(db, row.household_id)
    issues = service.completeness(draft, members)
    if issues:
        raise ApiError(422, "INVALID_INPUT", "請先完成問卷。", {"issues": issues})
    try:
        return t12_planning.public_view(
            t12_planning.preview(db, draft=draft, members=members, recipe_ids=body.recipe_ids))
    except t12_planning.T12PlanningUnavailable as exc:
        raise ApiError(exc.status, exc.code, exc.message) from None



@router.post("/{request_id}/knowledge-tips")
def knowledge_tips(request_id: uuid.UUID, body: NutritionPreviewBody,
                   household_id=Depends(current_household_id), db: Session = Depends(get_db),
                   limiter: RateLimiter = Depends(get_tips_limiter)):
    """T12 之後的營養小知識：後端重算最終食材 → T11 檢索審核知識 → 生成一句說明（雲端經 T10）。

    不接受前端傳來的食材或知識；營養未通過（含主食防線）或菜單不能採用時不呼叫任何付費服務，直接回 unavailable。
    付費查詢每戶每分鐘有上限（429 RATE_LIMITED），避免重複觸發耗盡共用的 T10 額度。
    缺知識、知識服務故障或額度不足都不阻擋採用，只回報狀態與原因。
    """
    row = get_owned_request(db, household_id, request_id)
    draft = service.load(row.payload)
    _reject_bad_references(db, row.household_id, draft)
    members = _members(db, row.household_id)
    issues = service.completeness(draft, members)
    if issues:
        raise ApiError(422, "INVALID_INPUT", "請先完成問卷。", {"issues": issues})
    try:
        menu = t12_planning.preview(db, draft=draft, members=members, recipe_ids=body.recipe_ids)
    except t12_planning.T12PlanningUnavailable as exc:
        raise ApiError(exc.status, exc.code, exc.message) from None
    food_ids = list(menu.get("final_food_ids") or [])
    extra = {"input_version": row.version, "recipe_ids": body.recipe_ids, "nutrition_status": menu.get("status")}
    # 與採用同一條判斷：營養未通過或有任何阻擋採用的問題（例如菜含禁吃食材）都不花付費額度
    if menu.get("status") != "nutrition_passed" or menu.get("adoption_blocked", True):
        reason = "NUTRITION_NOT_PASSED" if menu.get("status") != "nutrition_passed" else "ADOPTION_BLOCKED"
        return {"status": "unavailable", "reason": reason, "items": [], "dropped": [],
                "not_found": [], "final_food_ids": [], "knowledge_status": None, "index_version": None, **extra}
    if not limiter.allow(str(household_id)):
        raise ApiError(429, "RATE_LIMITED", "營養小知識查詢太頻繁，請稍等一分鐘再試。")
    names = ingredients.names(db, food_ids) if food_ids else {}
    version = row.version  # rollback 後讀 ORM 屬性會重新開交易，先記下
    db.rollback()  # 釋放讀取交易；以下的付費呼叫用各自的短交易
    result = knowledge_explanation.tips_for_menu(
        SessionLocal, final_food_ids=food_ids, household_id=household_id,
        request_tag=f"tips:{request_id}:{version}", names=names)
    return {**result, **extra}

@router.get("/{request_id}/recipe-catalog")
def recipe_catalog(request_id: uuid.UUID, household_id=Depends(current_household_id),
                   db: Session = Depends(get_db)):
    """食譜庫選菜：合格食譜依主要食材／菜系／類型／主食種類分類，並標出與本問卷成員禁忌衝突的菜。"""
    draft = service.load(get_owned_request(db, household_id, request_id).payload)
    excluded = service.restrictions(db, draft, _members(db, household_id))["excluded_food_ids"]
    return catalog_service.catalog(db, set(excluded))


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
