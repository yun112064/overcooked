"""T11 過敏原群組（config/t11_allergen_groups.json，使用者 2026-11-02 核准）。

成員把某食材設為「過敏」時，同一過敏原群組的其他食材（含加工品、調味料）一併排除，
例如對「雞蛋」過敏也排除皮蛋、鹹蛋、蛋豆腐、美乃滋。群組只依食材名稱人工整理，
不使用食譜表中不一致的過敏原標記；「不吃」不套用本表。食譜 Food_ID 不變。
檔案格式錯誤時拋出 AllergenGroupsInvalid，呼叫端不可當成「沒有群組」而少排除。
"""
import json
from functools import lru_cache
from pathlib import Path

from app.core.config import PROJECT_ROOT

SCHEMA_VERSION = "t11-allergen-groups-v1"


class AllergenGroupsInvalid(ValueError):
    """群組檔無法信任；寧可停止也不少排除。"""


def default_path() -> Path:
    return PROJECT_ROOT / "config/t11_allergen_groups.json"


@lru_cache(maxsize=4)
def _load(path: str) -> tuple[dict, ...]:
    file = Path(path)
    if not file.is_file():
        raise AllergenGroupsInvalid("missing")
    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise AllergenGroupsInvalid("unreadable") from None
    if (not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION
            or data.get("status") != "approved" or not data.get("approved_by") or not data.get("version")
            or not isinstance(data.get("groups"), list)):
        raise AllergenGroupsInvalid("header")
    groups = []
    for group in data["groups"]:
        try:
            triggers = {item["food_id"] for item in group["triggers"]}
            members = {item["food_id"]: item.get("name") for item in group["members"]}
            name, allergen_id = group["allergen_name"], group["allergen_id"]
        except (KeyError, TypeError):
            raise AllergenGroupsInvalid("group") from None
        if not triggers or not triggers <= set(members) or not name or not allergen_id:
            raise AllergenGroupsInvalid(f"group {allergen_id}")
        groups.append({"allergen_id": allergen_id, "allergen_name": name, "triggers": triggers,
                       "members": members, "version": data["version"]})
    return tuple(groups)


def expansion(allergen_food_ids, path: Path | None = None) -> dict[str, list[dict]]:
    """每個過敏食材編號額外要排除的編號（依群組）。"""
    groups = _load(str(path or default_path()))
    result: dict[str, list[dict]] = {}
    for food_id in allergen_food_ids:
        extra: dict[str, dict] = {}
        for group in groups:
            if food_id not in group["triggers"]:
                continue
            for other, name in group["members"].items():
                if other != food_id:
                    extra.setdefault(other, {"food_id": other, "name": name, "relation": "allergen_group",
                                             "group_id": group["allergen_id"],
                                             "group_name": group["allergen_name"],
                                             "status": group["version"]})
        result[food_id] = list(extra.values())
    return result
