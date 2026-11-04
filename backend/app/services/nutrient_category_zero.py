"""decisions #45：「食品類別上必然為 0」的營養素規則（config/nutrient_category_zero.json）。

規則以 TFDA 樣本 ID 的類別碼前綴＋營養素表示，每條附理由。只在 TFDA 該欄位空白時才適用
（TFDA 有值一律優先，由呼叫端檢查）；不適用者交給 nutrient_fill 推估或維持未知。
"""
import json
from functools import lru_cache

from app.core.config import PROJECT_ROOT

RULES_PATH = PROJECT_ROOT / "config/nutrient_category_zero.json"
CATEGORY_ZERO = "category_zero"


@lru_cache(maxsize=1)
def rules() -> tuple[dict, ...]:
    data = json.loads(RULES_PATH.read_text(encoding="utf-8"))
    return tuple(data["rules"])


def zero_rule(sample_id: str | None, nutrient: str) -> dict | None:
    """回傳適用的規則（id、reason…），沒有則 None。"""
    if not sample_id:
        return None
    for rule in rules():
        if nutrient in rule["nutrients"] and any(sample_id.startswith(p) for p in rule["prefixes"]):
            return rule
    return None


def applies(record: dict | None, nutrient: str) -> dict | None:
    """TFDA 樣本（tfda_mapping.sample 的紀錄）該營養素空白且符合規則時回傳規則；否則 None。"""
    if not record or (record.get("nutrition_per_100g") or {}).get(nutrient) is not None:
        return None
    return zero_rule(record.get("ingredient_id"), nutrient)
