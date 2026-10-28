"""調味料（決策 #44）：視為家中足量——缺料清單不列、不查價、成本 0；營養照常計算。

設定檔 config/seasoning_ingredients.json（可人工增修）：
- rules：以標準食材名稱判斷的規則（關鍵字／字尾），用來掃描合格食譜、找出新的候選調味料。
- exclude：規則會誤判、但實際用途不是調味料的 FOOD 編號（例如主料用的麵粉、整瓶入菜的紅酒），附理由。
- ingredients：生效的明確清單（FOOD 編號 → 標準名稱、分類、選填理由）。T13 成本與 T02 查價只看這份清單，
  規則不會在執行期自動擴大範圍；新增食材請先用 classify() 檢查，再人工寫入清單。
"""
from __future__ import annotations

import json
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache
from pathlib import Path

SEASONINGS_PATH = Path(__file__).resolve().parents[3] / "config/seasoning_ingredients.json"


def _read(path: Path) -> dict:
    if not path.is_file():
        return {"rules": {}, "exclude": {}, "ingredients": {}}
    return json.loads(path.read_text(encoding="utf-8"))


def load_seasonings(path: str | Path = SEASONINGS_PATH) -> set[str]:
    """生效的調味料 FOOD 編號；檔案不存在時視為沒有調味料。"""
    return set(_read(Path(path)).get("ingredients", {}))


@lru_cache(maxsize=4)
def _rules(path: str) -> tuple[list[tuple[str, str]], list[tuple[str, str]], dict[str, dict]]:
    data = _read(Path(path))
    rules = data.get("rules", {})
    keywords = [(kw, cat) for cat, kws in rules.get("keywords", {}).items() for kw in kws]
    suffixes = [(sx, cat) for cat, sxs in rules.get("suffixes", {}).items() for sx in sxs]
    return keywords, suffixes, data.get("exclude", {})


def classify(food_id: str | None, name: str | None, path: str | Path = SEASONINGS_PATH) -> str | None:
    """依規則判斷標準名稱是否為調味料，回傳分類（如「醬料」）或 None；exclude 清單優先。"""
    keywords, suffixes, exclude = _rules(str(path))
    if not name or (food_id and food_id in exclude):
        return None
    for keyword, category in keywords:
        if keyword in name:
            return category
    for suffix, category in suffixes:
        if name.endswith(suffix):
            return category
    return None


@lru_cache(maxsize=4)
def _spoon_config(path: str) -> tuple[dict[str, Decimal], frozenset[str], frozenset[str]]:
    """{FOOD 編號: 每小匙克數}、計數單位、維持原單位的 FOOD 編號（設定檔 spoon_display）。"""
    data = _read(Path(path))
    spoon = data.get("spoon_display", {})
    per_category = {cat: Decimal(str(g)) for cat, g in spoon.get("grams_per_teaspoon", {}).items()}
    keep = frozenset(spoon.get("keep_original", {}).get("food_ids", []))
    grams = {fid: per_category[item["category"]] for fid, item in data.get("ingredients", {}).items()
             if item.get("category") in per_category and fid not in keep}
    return grams, frozenset(spoon.get("count_units", [])), keep


def _round_to(value: Decimal, step: Decimal) -> Decimal:
    return (value / step).quantize(Decimal("1"), rounding=ROUND_HALF_UP) * step


def spoon_display(food_id: str, grams, original_unit: str | None,
                  path: str | Path = SEASONINGS_PATH) -> dict | None:
    """調味料的畫面用量：整餐克數換成小匙／大匙／量杯（規則見設定檔 spoon_display.description）。
    只影響顯示；不是調味料、沒有克數、原單位是顆／片等計數單位或整顆香料時回傳 None（沿用原本顯示）。"""
    per_teaspoon, count_units, _ = _spoon_config(str(path))
    if grams is None or food_id not in per_teaspoon or original_unit in count_units:
        return None
    teaspoons = Decimal(str(grams)) / per_teaspoon[food_id]
    if teaspoons < Decimal("0.125"):
        return {"display_amount": None, "display_unit": None, "display_text": "少許"}
    # 每一級：（上限小匙數, 單位, 每單位小匙數, 取整步長）；取整後到達上限就升到下一級的 1 單位
    for limit, unit, size, step in _SPOON_STEPS:
        if teaspoons < limit:
            amount = _round_to(teaspoons / size, step)
            if amount * size >= limit:
                continue
            return {"display_amount": amount.quantize(Decimal("0.01")), "display_unit": unit, "display_text": None}
    amount = _round_to(teaspoons / 48, Decimal("0.25"))
    return {"display_amount": amount.quantize(Decimal("0.01")), "display_unit": "量杯", "display_text": None}


_SPOON_STEPS = (
    (Decimal(1), "小匙", Decimal(1), Decimal("0.25")),
    (Decimal(3), "小匙", Decimal(1), Decimal("0.5")),
    (Decimal(48), "大匙", Decimal(3), Decimal("0.5")),
)
