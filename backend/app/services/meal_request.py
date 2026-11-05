"""T08 當餐問卷：草稿內容、完整性檢查、用餐／便當項目與必要限制。

草稿可以只填一部分；格式錯誤會在儲存時拒絕，但「尚未填完」只列為
issues，讓使用者返回修改時不會遺失已填資料。
"""
import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Member
from app.services import household as household_service
from app.services import ingredients, synonyms
from nutrition_target import TargetInputError

MealType = Literal["breakfast", "lunch", "dinner"]
MAX_DISHES = 10


class Participant(BaseModel):
    member_id: uuid.UUID
    dining: bool = True
    bento: bool = False
    # 便當預設午餐，可改早餐或晚餐
    bento_meal: MealType = "lunch"


class MealRequestDraft(BaseModel):
    meal_date: date | None = None
    meal_type: MealType | None = None
    budget: Decimal | None = Field(default=None, gt=0, le=100000, max_digits=8, decimal_places=2)
    staple: str | None = Field(default=None, max_length=30)
    dish_count: int | None = Field(default=None, ge=1, le=MAX_DISHES)
    taste_preferences: str = Field(default="", max_length=200)
    specified_dishes: list[str] = Field(default_factory=list, max_length=MAX_DISHES)
    participants: list[Participant] = Field(default_factory=list, max_length=20)
    available_ingredient_ids: list[str] = Field(default_factory=list, max_length=200)

    @field_validator("staple")
    @classmethod
    def strip_staple(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("taste_preferences")
    @classmethod
    def strip_taste(cls, value: str) -> str:
        return value.strip()

    @field_validator("specified_dishes")
    @classmethod
    def clean_dishes(cls, values: list[str]) -> list[str]:
        cleaned = list(dict.fromkeys(v.strip() for v in values if v.strip()))
        if any(len(v) > 50 for v in cleaned):
            raise ValueError("指定菜色名稱最多 50 個字。")
        return cleaned

    @field_validator("available_ingredient_ids")
    @classmethod
    def dedupe_ingredients(cls, values: list[str]) -> list[str]:
        # 以標準食材 ID 去重；不同 ID（不同品種或原料狀態）不合併
        return list(dict.fromkeys(v.strip() for v in values if v.strip()))

    @model_validator(mode="after")
    def unique_members(self):
        ids = [p.member_id for p in self.participants]
        if len(ids) != len(set(ids)):
            raise ValueError("同一位成員只能出現一次；用餐與便當請在同一列勾選。")
        return self


def dump(draft: MealRequestDraft) -> dict:
    return draft.model_dump(mode="json")


def load(payload: dict) -> MealRequestDraft:
    return MealRequestDraft.model_validate(payload)


def check_references(db: Session, household_id, draft: MealRequestDraft) -> list[dict]:
    """儲存前必須通過：成員屬於本家庭、食材在標準字典中。"""
    problems = []
    member_ids = [p.member_id for p in draft.participants]
    if member_ids:
        owned = set(db.scalars(
            select(Member.id).where(Member.household_id == household_id, Member.id.in_(member_ids))
        ))
        if any(m not in owned for m in member_ids):
            problems.append(_issue("participants", "UNKNOWN_MEMBER", "包含不屬於此家庭的成員。"))
    if ingredients.unknown_ids(db, draft.available_ingredient_ids):
        problems.append(_issue("available_ingredient_ids", "UNKNOWN_INGREDIENT_ID",
                               "包含不在標準食材字典中的項目，請從清單選擇。"))
    return problems


def _issue(field: str, reason: str, message: str) -> dict:
    return {"field": field, "reason": reason, "message": message}


def items(draft: MealRequestDraft) -> list[dict]:
    """展開為製作項目：同一成員用餐＋便當是兩個項目，各自套餐別與 n。"""
    result = []
    for p in draft.participants:
        if p.dining and draft.meal_type:
            result.append({"participation_id": f"{p.member_id}:dining", "member_id": str(p.member_id),
                           "kind": "dining", "meal": draft.meal_type})
        if p.bento:
            result.append({"participation_id": f"{p.member_id}:bento", "member_id": str(p.member_id),
                           "kind": "bento", "meal": p.bento_meal})
    return result


def completeness(draft: MealRequestDraft, members: dict) -> list[dict]:
    issues = []
    required = (
        ("meal_date", "請選擇餐食日期。"),
        ("meal_type", "請選擇餐別。"),
        ("budget", "請輸入預算。"),
        ("staple", "請輸入主食（不需要主食也請註明）。"),
        ("dish_count", "請選擇菜數。"),
    )
    for field, message in required:
        if getattr(draft, field) is None:
            issues.append(_issue(field, "REQUIRED", message))
    if not items(draft):
        issues.append(_issue("participants", "REQUIRED", "請至少選擇一位用餐或帶便當的成員。"))
    if any(p.member_id not in members for p in draft.participants):
        issues.append(_issue("participants", "UNKNOWN_MEMBER", "有成員已被刪除，請重新選擇。"))
    if draft.dish_count is not None and len(draft.specified_dishes) > draft.dish_count:
        issues.append(_issue("specified_dishes", "TOO_MANY_SPECIFIED_DISHES",
                             f"指定菜色有 {len(draft.specified_dishes)} 道，超過菜數 {draft.dish_count}；"
                             "指定菜會計入菜數，請增加菜數或減少指定菜。"))
    return issues


def restrictions(db: Session, draft: MealRequestDraft, members: dict) -> dict:
    """必要限制取本次用餐與便當成員的聯集，並標出來自哪些成員。"""
    involved = [i for m in members.values() for i in (*m.allergen_ingredient_ids, *m.avoided_ingredient_ids)]
    names = ingredients.names(db, involved)
    union: dict[str, dict] = {}
    for p in draft.participants:
        member = members.get(p.member_id)
        if member is None or not (p.bento or (p.dining and draft.meal_type)):
            continue
        for kind, ids in (("allergen", member.allergen_ingredient_ids), ("avoided", member.avoided_ingredient_ids)):
            for ingredient_id in ids:
                row = union.setdefault(ingredient_id, {
                    "ingredient_id": ingredient_id, "name": names.get(ingredient_id),
                    "kinds": [], "member_names": [],
                })
                if kind not in row["kinds"]:
                    row["kinds"].append(kind)
                if member.display_name not in row["member_names"]:
                    row["member_names"].append(member.display_name)
    # 同義食材（含待確認的組）也一併排除，例如對「番茄」過敏也排除「蕃茄」
    expanded: dict[str, dict] = {}
    for base_id, extras in synonyms.restriction_expansion(list(union)).items():
        for extra in extras:
            if extra["food_id"] in union:
                continue
            row = expanded.setdefault(extra["food_id"], {
                "ingredient_id": extra["food_id"], "relation": extra["relation"], "status": extra["status"],
                "via": [], "kinds": [], "member_names": [],
            })
            row["via"].append(base_id)
            for key in ("kinds", "member_names"):
                for value in union[base_id][key]:
                    if value not in row[key]:
                        row[key].append(value)
    extra_names = ingredients.names(db, list(expanded))
    for row in expanded.values():
        row["name"] = extra_names.get(row["ingredient_id"])
    excluded = set(union) | set(expanded)
    available = set(draft.available_ingredient_ids)
    return {
        "ingredients": list(union.values()),
        "expanded_ingredients": list(expanded.values()),
        # 生成菜單時要排除的完整 FOOD 編號
        "excluded_food_ids": sorted(excluded),
        # 家中有、但有人過敏或禁吃：提醒使用者，生成時仍會排除
        "available_but_restricted": [i for i in draft.available_ingredient_ids if i in excluded],
    }


def target_preview(draft: MealRequestDraft, members: dict) -> dict:
    """以 T03 規則預覽本餐目標；未乘配方、不代表可採用。"""
    entries = items(draft)
    if not entries or draft.meal_date is None:
        return {"status": "incomplete"}
    participations = []
    for entry in entries:
        member = members.get(uuid.UUID(entry["member_id"]))
        if member is None:
            return {"status": "incomplete"}
        fields = {c: getattr(member, c) for c in ("birth_date", "equation_sex", "weight_kg", "activity_category", "goal")}
        participations.append({
            "participation_id": entry["participation_id"],
            "kind": entry["kind"],
            "meal": entry["meal"],
            "n": household_service.plain_decimal(member.portion),
            "member": household_service.target_input(fields, member_id=entry["member_id"], on=draft.meal_date),
        })
    try:
        result = household_service.target_service().aggregate_meal_targets(participations)
    except TargetInputError as exc:
        detail = exc.as_dict()["details"]
        return {"status": "unavailable", "reason": detail.get("reason"),
                "message": household_service.REASON_MESSAGES.get(detail.get("reason"), "部分成員資料無法計算目標，請檢查成員設定。")}
    return {
        "status": "available",
        "rule_version": result["rule_version"],
        "total_n": result["total_n"],
        "target": result["target"],
        "items": [
            {"participation_id": p["participation_id"], "kind": p["kind"], "meal": p["meal"],
             "meal_ratio": p["meal_ratio"], "n": p["n"], "scaled_target": p["scaled_target"]}
            for p in result["participations"]
        ],
        "approved_for_adoption": False,
    }
