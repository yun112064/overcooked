"""HouseholdService：家庭授權與成員規則。

成員的目標與 n 一律交給 T03 的 NutritionTargetService 驗證，前後端與營養模組
使用同一份版本化規則，不在此另寫一套判斷。
"""
from datetime import date
from decimal import Decimal
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.errors import invalid_input
from app.core.config import settings
from app.db.models import HouseholdMembership, Member
from app.services import ingredients
from nutrition_target import NutritionTargetService, TargetInputError

MAX_RESTRICTIONS = 50


@lru_cache(maxsize=1)
def _target_service(path: str) -> NutritionTargetService:
    return NutritionTargetService(path)


def target_service() -> NutritionTargetService:
    return _target_service(str(settings.nutrition_rules_path))


def age_on(birth_date: date, on: date) -> int:
    """足歲：生日當天才加一歲。"""
    years = on.year - birth_date.year
    if (on.month, on.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years


def is_member_of(db: Session, user_id, household_id) -> bool:
    return db.scalar(
        select(HouseholdMembership.id).where(
            HouseholdMembership.user_id == user_id,
            HouseholdMembership.household_id == household_id,
        )
    ) is not None


def plain_decimal(value) -> Decimal:
    """去掉資料庫補上的尾數 0（70.00 → 70），不改變數值。"""
    return Decimal(format(Decimal(value).normalize(), "f"))


def target_input(member_fields: dict, *, member_id: str, on: date) -> dict:
    """組成 T03 需要的成員輸入；n 不進每日公式，避免重複縮放。"""
    return {
        "member_id": member_id,
        "age_years": age_on(member_fields["birth_date"], on),
        "equation_sex": member_fields["equation_sex"],
        "weight_kg": plain_decimal(member_fields["weight_kg"]),
        "activity_category": member_fields["activity_category"],
        "calculation_date": on.isoformat(),
        "goal": member_fields["goal"],
    }


REASON_MESSAGES = {
    "INVALID_PORTION": "食量只能選 0.7、0.85、1、1.15 或 1.3。",
    "GOAL_NOT_ALLOWED_FOR_AGE": "未滿 19 歲只能選擇一般餐食，不提供維持／增肌／減脂目標。",
    "DAILY_ENERGY_BELOW_MINIMUM": "以目前體重與活動量設定減脂，每日熱量會低於 1200 大卡，請改選其他目標。",
    "INVALID_WEIGHT": "體重必須大於 0。",
    "INVALID_NUMBER": "數值格式不正確。",
    "UNSUPPORTED_NUMERIC_PRECISION": "數值位數過多，無法精確計算。",
    "UNSUPPORTED_TARGET_SCOPE": "此成員資料超出目前規則的計算範圍。",
}


def _raise_target_error(exc: TargetInputError):
    detail = exc.as_dict().get("details", {})
    reason = detail.get("reason")
    field = {"n": "portion", "age_years": "birth_date"}.get(detail.get("field"), detail.get("field"))
    message = REASON_MESSAGES.get(reason, "成員資料無法計算營養目標，請檢查輸入。")
    raise invalid_input(message, field=field, reason=reason) from None


def _clean_ids(db: Session, values: list[str], field: str) -> list[str]:
    cleaned = list(dict.fromkeys(v.strip() for v in values if v.strip()))
    if len(cleaned) > MAX_RESTRICTIONS:
        raise invalid_input(f"最多可設定 {MAX_RESTRICTIONS} 項。", field=field, reason="TOO_MANY_ITEMS")
    unknown = ingredients.unknown_ids(db, cleaned)
    if unknown:
        raise invalid_input(
            "包含不在標準食材字典中的項目，請從清單選擇。",
            field=field, reason="UNKNOWN_INGREDIENT_ID",
        )
    return cleaned


def validate_member(db: Session, fields: dict, *, today: date) -> dict:
    """驗證並正規化成員欄位；任何不合格都回報可修正的欄位與原因。"""
    if fields["birth_date"] > today:
        raise invalid_input("出生日期不能晚於今天。", field="birth_date", reason="INVALID_DATE")
    service = target_service()
    try:
        fields["portion"] = service.validate_portion(fields["portion"])
        age = age_on(fields["birth_date"], today)
        if fields["goal"] not in service.available_goals(age):
            raise TargetInputError(
                "GOAL_NOT_ALLOWED_FOR_AGE", "goal",
                "未滿 19 歲只能選擇一般餐食，不提供維持／增肌／減脂目標。",
            )
        # 確認以此設定算得出每日目標（例如減脂低於 1200 kcal 會被拒絕）
        service.daily_target(target_input(fields, member_id="validation", on=today))
    except TargetInputError as exc:
        _raise_target_error(exc)
    fields["allergen_ingredient_ids"] = _clean_ids(db, fields["allergen_ingredient_ids"], "allergen_ingredient_ids")
    fields["avoided_ingredient_ids"] = _clean_ids(db, fields["avoided_ingredient_ids"], "avoided_ingredient_ids")
    return fields


def daily_target(member: Member, *, on: date) -> dict:
    fields = {c: getattr(member, c) for c in ("birth_date", "equation_sex", "weight_kg", "activity_category", "goal")}
    try:
        return target_service().daily_target(target_input(fields, member_id=str(member.id), on=on))
    except TargetInputError as exc:
        _raise_target_error(exc)
