"""同義食材對照（config/ingredient_synonyms.json）。

工作簿與資料庫的 FOOD 編號原樣保留；只在比對時把同一種食材的不同編號視為一起：
- 過敏／禁吃：所有未被否決的組都套用（含待確認），寧可多排除也不漏掉。
  上位類別雙向套用：對「牛肉」過敏會排除各部位，對某部位過敏也排除寫「牛肉」的食譜。
- 家中已有、查價：只套用隊友確認（status=confirmed）的組，避免合併錯誤。
"""
import json
from functools import lru_cache
from pathlib import Path

from app.core.config import settings


@lru_cache(maxsize=4)
def _load(path: str) -> dict:
    file = Path(path)
    if not file.is_file():
        return {"groups": [], "broader": []}
    return json.loads(file.read_text(encoding="utf-8"))


def _table() -> dict:
    return _load(str(settings.ingredient_synonyms_path))


def _group_ids(group: dict) -> set[str]:
    return {food["food_id"] for food in group["foods"]}


def restriction_expansion(food_ids) -> dict[str, list[dict]]:
    """過敏／禁吃用：每個編號額外要排除的編號與原因。"""
    table = _table()
    result: dict[str, list[dict]] = {}
    for food_id in food_ids:
        extra: dict[str, dict] = {}
        for group in table.get("groups", []):
            if group.get("status") == "rejected":
                continue
            ids = _group_ids(group)
            if food_id in ids:
                for other in ids - {food_id}:
                    extra[other] = {"food_id": other, "relation": "synonym", "group_id": group["id"],
                                    "status": group.get("status")}
        for broad in table.get("broader", []):
            if broad.get("status") == "rejected":
                continue
            includes = {item["food_id"] for item in broad["includes"]}
            related = includes if broad["food_id"] == food_id else ({broad["food_id"]} if food_id in includes else set())
            for other in related - {food_id}:
                extra.setdefault(other, {"food_id": other, "relation": "broader", "group_id": broad["food_id"],
                                         "status": broad.get("status")})
        result[food_id] = list(extra.values())
    return result


def confirmed_groups() -> list[set[str]]:
    """家中已有、查價用：只回傳已確認為同一種食材的組。"""
    return [_group_ids(g) for g in _table().get("groups", []) if g.get("status") == "confirmed"]


def confirmed_equivalents(food_ids) -> set[str]:
    """把已確認的同義編號一起納入（例如家中有「大蒜」也算有「蒜頭」）。"""
    wanted = set(food_ids)
    result = set(wanted)
    for ids in confirmed_groups():
        if wanted & ids:
            result |= ids
    return result
