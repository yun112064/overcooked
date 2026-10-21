"""調味料（決策 #44）：視為家中足量——缺料清單不列、不查價、成本 0；營養照常計算。

設定檔 config/seasoning_ingredients.json（可人工增修）：
- rules：以標準食材名稱判斷的規則（關鍵字／字尾），用來掃描合格食譜、找出新的候選調味料。
- exclude：規則會誤判、但實際用途不是調味料的 FOOD 編號（例如主料用的麵粉、整瓶入菜的紅酒），附理由。
- ingredients：生效的明確清單（FOOD 編號 → 標準名稱、分類、選填理由）。T13 成本與 T02 查價只看這份清單，
  規則不會在執行期自動擴大範圍；新增食材請先用 classify() 檢查，再人工寫入清單。
"""
from __future__ import annotations

import json
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
