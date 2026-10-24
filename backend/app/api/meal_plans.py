"""T16 /meal-plans：採用指定結果版本，依日期查看已採用餐食紀錄（皆限本家庭；他家庭一律 404）。"""
import uuid
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Header, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, current_auth, current_household_id, verify_origin
from app.api.errors import not_found
from app.db.models import AdoptedMealPlan
from app.db.session import get_db
from app.services import meal_plans as service

router = APIRouter(prefix="/meal-plans", tags=["meal-plans"], dependencies=[Depends(verify_origin)])


class AdoptBody(BaseModel):
    meal_request_id: uuid.UUID
    result_version: int = Field(ge=1)
    request_version: int = Field(ge=1)
    # 以缺價例外採用時，必須指名 T15 的接受紀錄（POST .../decisions 回傳的 decision.id）
    price_exception_decision_id: uuid.UUID | None = None


@router.post("")
def adopt(body: AdoptBody, response: Response, idempotency_key: str = Header(min_length=8, max_length=100),
          auth: AuthContext = Depends(current_auth), household_id=Depends(current_household_id),
          db: Session = Depends(get_db)):
    """單一交易：重驗版本、資格、缺價接受紀錄與前一日歷史，保存完整快照，問卷改為 confirmed。"""
    plan, created = service.adopt(
        db, household_id=household_id, user_id=auth.user_id, meal_request_id=body.meal_request_id,
        result_version=body.result_version, request_version=body.request_version,
        price_exception_decision_id=body.price_exception_decision_id, idempotency_key=idempotency_key)
    db.commit()  # 任一步失敗由 get_db 回滾
    response.status_code = 201 if created else 200
    return {"meal_plan": service.summary(plan), "created": created}


@router.get("")
def list_plans(date: date | None = None, household_id=Depends(current_household_id),
               db: Session = Depends(get_db)):
    """指定 date：當日全部餐別的已採用餐食；未指定：最近有紀錄的日期。"""
    if date is None:
        return {"dates": service.recent_dates(db, household_id)}
    return {"meal_date": date.isoformat(),
            "meal_plans": [service.detail(p) for p in service.on_date(db, household_id, date)]}


@router.get("/trend")
def plan_trend(days: int = Query(30, ge=1, le=366), household_id=Depends(current_household_id),
               db: Session = Depends(get_db)):
    """最近 days 天（含今天）每筆已採用餐食的營養與成本，供歷史圖表使用。"""
    since = date.today() - timedelta(days=days - 1)
    return {"since": since.isoformat(), "items": service.trend(db, household_id, since)}


@router.get("/{plan_id}")
def get_plan(plan_id: uuid.UUID, household_id=Depends(current_household_id), db: Session = Depends(get_db)):
    plan = db.get(AdoptedMealPlan, plan_id)
    if plan is None or plan.household_id != household_id:
        raise not_found("找不到此已採用餐食紀錄。")
    return service.detail(plan)
