import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, current_auth, verify_origin
from app.api.errors import not_found
from app.db.models import Household, HouseholdMembership
from app.db.session import get_db
from app.services.household import is_member_of

router = APIRouter(prefix="/household", tags=["household"], dependencies=[Depends(verify_origin)])


class HouseholdCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("請輸入家庭名稱。")
        return value


class HouseholdSelect(BaseModel):
    household_id: uuid.UUID


def _out(h: Household) -> dict:
    return {"id": str(h.id), "name": h.name}


@router.get("")
def list_households(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Household)
        .join(HouseholdMembership, HouseholdMembership.household_id == Household.id)
        .where(HouseholdMembership.user_id == auth.user_id)
        .order_by(Household.created_at)
    ).all()
    current = auth.session.household_id
    return {
        "households": [_out(h) for h in rows],
        "current_household_id": str(current) if current else None,
    }


@router.post("", status_code=201)
def create_household(body: HouseholdCreate, auth: AuthContext = Depends(current_auth),
                     db: Session = Depends(get_db)):
    household = Household(name=body.name)
    db.add(household)
    db.flush()
    db.add(HouseholdMembership(user_id=auth.user_id, household_id=household.id, role="owner"))
    # 建立後直接設為目前家庭
    auth.session.household_id = household.id
    db.commit()
    return _out(household)


@router.post("/select")
def select_household(body: HouseholdSelect, auth: AuthContext = Depends(current_auth),
                     db: Session = Depends(get_db)):
    if not is_member_of(db, auth.user_id, body.household_id):
        # 不區分「不存在」與「沒有權限」，避免探測其他家庭
        raise not_found("找不到此家庭。")
    auth.session.household_id = body.household_id
    db.commit()
    return _out(db.get(Household, body.household_id))
