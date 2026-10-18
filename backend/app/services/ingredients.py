"""共用標準食材字典（ING 編號），供過敏、禁吃與已有食材種類對映。

來源為 nutrition_model/data/ingredient_id_master.json，由 T04 匯入工具維護；
一般使用者不能改寫共用字典。
"""
import json
from functools import lru_cache
from pathlib import Path

from app.core.config import settings


@lru_cache(maxsize=1)
def _catalog(path: str) -> dict[str, str]:
    rows = json.loads(Path(path).read_text(encoding="utf-8"))
    return {row["ingredient_id"]: row["canonical_name"] for row in rows}


def catalog() -> dict[str, str]:
    return _catalog(str(settings.ingredient_master_path))


def unknown_ids(ingredient_ids: list[str]) -> list[str]:
    known = catalog()
    return [i for i in ingredient_ids if i not in known]


def describe(ingredient_ids: list[str]) -> list[dict]:
    known = catalog()
    return [{"ingredient_id": i, "name": known.get(i)} for i in ingredient_ids]


def search(query: str, limit: int = 20) -> list[dict]:
    q = query.strip()
    rows = catalog().items()
    if q:
        exact = [(i, n) for i, n in rows if n == q]
        prefix = [(i, n) for i, n in rows if n != q and n.startswith(q)]
        contains = [(i, n) for i, n in rows if q in n and not n.startswith(q)]
        rows = exact + prefix + contains
    return [{"ingredient_id": i, "name": n} for i, n in list(rows)[:limit]]
