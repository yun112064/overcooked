import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_household_id, verify_origin
from app.api.errors import not_found, version_conflict
from app.db.models import Member
from app.db.session import get_db
from app.services import household as household_service
from app.services import ingredients

router = APIRouter(prefix="/members", tags=["members"], dependencies=[Depends(verify_origin)])

Activity = Literal["sedentary", "low_active", "active", "highly_active"]
Goal = Literal["general", "maintain", "muscle_gain", "fat_loss"]


class MemberBody(BaseModel):
    display_name: str = Field(min_length=1, max_length=50)
    birth_date: date
    equation_sex: Literal["M", "F"]
    weight_kg: Decimal = Field(gt=0, le=500, max_digits=6, decimal_places=2)
    activity_category: Activity
    goal: Goal
    # 只能是 0.7／0.85／1／1.15／1.3；實際檢查交給 T03 規則
    portion: Decimal
    allergen_ingredient_ids: list[str] = Field(default_factory=list, max_length=100)
    avoided_ingredient_ids: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("display_name")
    @classmethod
    def strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("請輸入成員名稱。")
        return value


class MemberUpdate(MemberBody):
    version: int = Field(ge=1)


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _out(m: Member) -> dict:
    today = _today()
    return {
        "id": str(m.id),
        "display_name": m.display_name,
        "birth_date": m.birth_date.isoformat(),
        "age_years": household_service.age_on(m.birth_date, today),
        "equation_sex": m.equation_sex,
        "weight_kg": str(household_service.plain_decimal(m.weight_kg)),
        "activity_category": m.activity_category,
        "goal": m.goal,
        "portion": str(household_service.plain_decimal(m.portion)),
        "allergens": ingredients.describe(m.allergen_ingredient_ids),
        "avoided": ingredients.describe(m.avoided_ingredient_ids),
        "version": m.version,
        "updated_at": m.updated_at.isoformat(),
    }


def _get_owned(db: Session, household_id, member_id: uuid.UUID) -> Member:
    member = db.get(Member, member_id)
    if member is None or member.household_id != household_id:
        raise not_found("找不到此成員。")
    return member


@router.get("/options")
def options():
    """表單選項與規則版本，前端據此呈現；後端仍會重新驗證。"""
    service = household_service.target_service()
    return {
        "rule_version": service.rule_version,
        "portions": ["0.7", "0.85", "1", "1.15", "1.3"],
        "activity_categories": ["sedentary", "low_active", "active", "highly_active"],
        "goals": ["general", "maintain", "muscle_gain", "fat_loss"],
        "adult_goals_min_age_years": 19,
        "minor_goals": list(service.available_goals(0)),
    }


@router.get("")
def list_members(household_id=Depends(current_household_id), db: Session = Depends(get_db)):
    rows = db.scalars(
        select(Member).where(Member.household_id == household_id).order_by(Member.created_at)
    ).all()
    return {"members": [_out(m) for m in rows]}


@router.post("", status_code=201)
def create_member(body: MemberBody, household_id=Depends(current_household_id),
                  db: Session = Depends(get_db)):
    fields = household_service.validate_member(body.model_dump(), today=_today())
    member = Member(household_id=household_id, version=1, **fields)
    db.add(member)
    db.commit()
    return _out(member)


@router.get("/{member_id}")
def get_member(member_id: uuid.UUID, household_id=Depends(current_household_id),
               db: Session = Depends(get_db)):
    return _out(_get_owned(db, household_id, member_id))


@router.put("/{member_id}")
def update_member(member_id: uuid.UUID, body: MemberUpdate,
                  household_id=Depends(current_household_id), db: Session = Depends(get_db)):
    member = _get_owned(db, household_id, member_id)
    if member.version != body.version:
        raise version_conflict()
    fields = household_service.validate_member(body.model_dump(exclude={"version"}), today=_today())
    for key, value in fields.items():
        setattr(member, key, value)
    member.version += 1
    member.updated_at = datetime.now(timezone.utc)
    db.commit()
    return _out(member)


@router.delete("/{member_id}", status_code=204)
def delete_member(member_id: uuid.UUID, household_id=Depends(current_household_id),
                  db: Session = Depends(get_db)):
    db.delete(_get_owned(db, household_id, member_id))
    db.commit()
    return Response(status_code=204)


@router.get("/{member_id}/target")
def member_target(member_id: uuid.UUID, household_id=Depends(current_household_id),
                  db: Session = Depends(get_db)):
    """今日每日基準（未乘 n）。G03 未通過前 approved_for_adoption 仍為 false。"""
    member = _get_owned(db, household_id, member_id)
    result = household_service.daily_target(member, on=_today())
    return {
        "member_id": str(member.id),
        "rule_version": result["rule_version"],
        "daily_reference": result["daily_reference"],
        "approved_for_adoption": result.get("approved_for_adoption", False),
    }
