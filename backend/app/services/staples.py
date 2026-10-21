"""主食（P1，decisions #16）：問卷的主食文字 → 一個 TFDA 食材的每 1n 計算列。

- 選項、TFDA 食材、預設份量與可調範圍來自 config/staples.json（版本化，份量待營養審核者確認）。
- 主食列 role=staple：計入營養與 ±10% 驗證，資料庫模式與菜色一起在可調範圍內對齊；
  不計入價格與缺料（T12 _requirements 不列入），也不查營養知識。
- 「不需要主食」等（none_values，或含「主食」且帶否定字的寫法，decisions #52）→ 沒有主食列。
  無法對應或沒有 TFDA 樣品 → 克數未知的列（不可採用，不以 0 代替）。
- 生重主食（糙米飯等，decisions #50）以生米克數計算；display_name、cooked_grams_per_n 只供畫面說明「約煮成 X g」。
- staple_options()：給 API 的可選主食清單（只列有 TFDA 樣本者），前端不寫死。
"""
import json
from dataclasses import dataclass
from decimal import Decimal
from functools import lru_cache

from app.core.config import PROJECT_ROOT
from app.services.meal_calculation import IngredientLine
from app.services.recipe_import import normalize_name

STAPLES_PATH = PROJECT_ROOT / "config" / "staples.json"
STAPLE_DISH_ID = "staple"
STAPLE_CONFIG = "staple_config"
NEGATIONS = ("不", "無", "免", "沒")


@dataclass(frozen=True)
class Staple:
    label: str
    line: IngredientLine
    config_version: str
    review_status: str

    def snapshot(self) -> dict:
        ln = self.line
        display = _display(self.label, table()["options"].get(self.label) or {})
        return {"label": self.label, "food_id": ln.food_id, "tfda_name": ln.name if ln.grams_per_n is not None else None,
                "grams_per_n": ln.grams_per_n, "adjustable_min": ln.adjustable_min,
                "adjustable_max": ln.adjustable_max, "weight_basis": ln.weight_basis,
                "unresolved_reason": ln.unresolved_reason, "config_version": self.config_version,
                "review_status": self.review_status, **display}


@lru_cache(maxsize=1)
def table() -> dict:
    return json.loads(STAPLES_PATH.read_text(encoding="utf-8"))


def _dec(value) -> Decimal | None:
    return None if value is None else Decimal(str(value))


def _display(label: str, option: dict) -> dict:
    """畫面用（decisions #50）：名稱（如「糙米（生）」）與生重主食的熟重說明（如「約煮成 150 g」，依預設份量）。"""
    cooked = _dec(option.get("cooked_grams_per_n"))
    return {"display_name": option.get("display_name") or label, "cooked_grams_per_n": cooked,
            "display_note": f"約煮成 {cooked.normalize():f} g" if cooked is not None else None}


def is_none_value(text: str | None) -> bool:
    """不需要主食：空白、設定的 none_values，或含「主食」且帶否定字（不吃主食、我沒有要主食…）。"""
    key = normalize_name(text or "")
    if not key or key in {normalize_name(v) for v in table().get("none_values", [])}:
        return True
    return "主食" in key and any(n in key for n in NEGATIONS)


def staple_options() -> list[dict]:
    """decisions #52：可選主食清單（只列有 TFDA 樣本者），依設定檔順序；給 API／前端選單使用。

    每項：label、aliases、display_name、display_note（生重主食的「約煮成 X g」，其餘 None）、
    default_grams_per_n、weight_basis、review_status。
    """
    config = table()
    return [{"label": label, "aliases": list(option.get("aliases", [])), **_display(label, option),
             "default_grams_per_n": _dec(option.get("default_grams_per_n")),
             "weight_basis": option.get("weight_basis", "raw"), "review_status": config["review_status"]}
            for label, option in config["options"].items() if option.get("tfda_name")]


def resolve(text: str | None) -> Staple | None:
    """主食文字 → Staple；不需要主食回傳 None。"""
    config = table()
    key = normalize_name(text or "")
    if is_none_value(text):
        return None
    meta = {"config_version": config["version"], "review_status": config["review_status"]}
    for label, option in config["options"].items():
        names = {normalize_name(n) for n in (label, *option.get("aliases", []))}
        if key not in names:
            continue
        common = {"food_id": option["food_id"], "dish_id": STAPLE_DISH_ID, "role": "staple",
                  "weight_basis_source": STAPLE_CONFIG}
        if not option.get("tfda_name"):
            line = IngredientLine(name=label, grams_per_n=None,
                                  unresolved_reason=option.get("unresolved_reason") or "staple_without_tfda_sample",
                                  raw_text=label, **common)
        else:
            line = IngredientLine(
                name=option["tfda_name"], grams_per_n=_dec(option["default_grams_per_n"]),
                weight_basis=option.get("weight_basis", "raw"), consumed_as_is=bool(option.get("consumed_as_is")),
                adjustable_min=_dec(option.get("adjustable_min")), adjustable_max=_dec(option.get("adjustable_max")),
                adjustment_approved=option.get("adjustable_min") is not None, **common)
        return Staple(label, line, **meta)
    line = IngredientLine(food_id=f"STAPLE:{text.strip()}", name=text.strip(), grams_per_n=None,
                          unresolved_reason="staple_not_supported", raw_text=text.strip(), dish_id=STAPLE_DISH_ID,
                          role="staple", weight_basis_source=STAPLE_CONFIG)
    return Staple(text.strip(), line, **meta)
