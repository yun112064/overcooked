"""/meal-plans：採用資料庫菜單、依日期查看已採用紀錄與熱量趨勢（皆限本家庭；他家庭一律 404）。

參考 t11-t20-demo 的 T16 API；本分支沒有生成結果表，採用時由後端重算菜單與營養（services/meal_plans）。
"""
import uuid
from datetime import date

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, current_auth, current_household_id, verify_origin
from app.api.errors import ApiError, not_found
from app.db.models import AdoptedMealPlan, MealRequest, Member
from app.db.session import get_db
from app.services import meal_plans as service
from app.services import meal_request as request_service
from app.services import t12_planning

router = APIRouter(prefix="/meal-plans", tags=["meal-plans"], dependencies=[Depends(verify_origin)])


class AdoptBody(BaseModel):
    meal_request_id: uuid.UUID
    request_version: int = Field(ge=1)
    # 畫面上看到的那份菜單；後端重查後必須相同才採用
    recipe_ids: list[str] = Field(min_length=1, max_length=10)


@router.post("")
def adopt(body: AdoptBody, response: Response, idempotency_key: str = Header(min_length=8, max_length=100),
          auth: AuthContext = Depends(current_auth), household_id=Depends(current_household_id),
          db: Session = Depends(get_db)):
    """單一交易：重驗問卷版本與菜單、重算營養，保存完整快照，問卷改為 confirmed。"""
    row = db.get(MealRequest, body.meal_request_id, with_for_update=True)
    if row is None or row.household_id != household_id:
        raise not_found("找不到此問卷。")
    draft = request_service.load(row.payload)
    members = {m.id: m for m in db.scalars(select(Member).where(Member.household_id == household_id))}
    if request_service.completeness(draft, members):
        raise ApiError(422, "INVALID_INPUT", "請先完成問卷。")
    try:
        plan, created = service.adopt(db, household_id=household_id, user_id=auth.user_id, row=row, draft=draft,
                                      members=members, version=body.request_version, recipe_ids=body.recipe_ids,
                                      idempotency_key=idempotency_key)
    except t12_planning.T12PlanningUnavailable as exc:
        raise ApiError(503, exc.code, exc.message) from None
    db.commit()  # 任一步失敗由 get_db 回滾
    response.status_code = 201 if created else 200
    return {"meal_plan": service.summary(plan), "created": created}


@router.get("")
def list_plans(date: date | None = None, household_id=Depends(current_household_id), db: Session = Depends(get_db)):
    """指定 date：當日全部餐別的已採用餐食；未指定：最近有紀錄的日期。"""
    if date is None:
        return {"dates": service.recent_dates(db, household_id)}
    return {"meal_date": date.isoformat(),
            "meal_plans": [service.detail(p) for p in service.on_date(db, household_id, date)]}


@router.get("/trend")
def plan_trend(days: int = Query(30, ge=1, le=366), until: date | None = None,
               household_id=Depends(current_household_id), db: Session = Depends(get_db)):
    """截至 until（含，前端傳使用者本機的今天）的最近 days 天每筆已採用餐食的熱量與蛋白質。"""
    since, end = service.default_window(until, days)
    return {"since": since.isoformat(), "until": end.isoformat(), "items": service.trend(db, household_id, since, end)}


@router.get("/{plan_id}")
def get_plan(plan_id: uuid.UUID, household_id=Depends(current_household_id), db: Session = Depends(get_db)):
    plan = db.get(AdoptedMealPlan, plan_id)
    if plan is None or plan.household_id != household_id:
        raise not_found("找不到此已採用餐食紀錄。")
    return service.detail(plan)
