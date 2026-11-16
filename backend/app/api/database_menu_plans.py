"""資料庫菜單（食譜庫選菜／換一份）的採用：由共用的 POST /meal-plans 依請求內容分派（沒有自己的路由）。"""
import uuid

from fastapi import Depends, Header, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, current_auth, current_household_id
from app.api.errors import ApiError, not_found
from app.db.models import MealRequest, Member
from app.db.session import get_db
from app.services import database_menu_plans as service
from app.services import meal_request as request_service
from app.services import t12_planning


class AdoptBody(BaseModel):
    meal_request_id: uuid.UUID
    request_version: int = Field(ge=1)
    # 畫面上看到的那份菜單；後端重查後必須相同才採用
    recipe_ids: list[str] = Field(min_length=1, max_length=10)


def adopt(body: AdoptBody, response: Response, idempotency_key: str = Header(min_length=8, max_length=100),
          auth: AuthContext = Depends(current_auth), household_id=Depends(current_household_id),
          db: Session = Depends(get_db)):
    """單一交易：重驗問卷版本與菜單、重算營養，保存完整快照，問卷改為 confirmed。"""
    # 與生成結果採用相同的鎖順序：先家庭、再問卷
    from app.services.meal_plans import _lock_household
    _lock_household(db, household_id)
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
        raise ApiError(exc.status, exc.code, exc.message) from None
    db.commit()  # 任一步失敗由 get_db 回滾
    response.status_code = 201 if created else 200
    return {"meal_plan": service.summary(plan), "created": created}
