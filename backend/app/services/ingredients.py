"""共用標準食材字典（FOOD 編號），供過敏、禁吃、已有食材種類與辨識結果對映。

來源為 T01 匯入的 ingredient_aliases 資料表（recipe_dataset 工作簿的「標準食材整合」
與食材別名），與食譜的 recipe_ingredients.food_id 同一套編號。一般使用者不能改寫
共用字典，只能透過匯入工具更新。
"""
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.recipe import IngredientAlias


def names(db: Session, ingredient_ids: list[str]) -> dict[str, str]:
    if not ingredient_ids:
        return {}
    rows = db.execute(
        select(IngredientAlias.food_id, IngredientAlias.canonical_name)
        .where(IngredientAlias.food_id.in_(set(ingredient_ids)))
    ).all()
    return dict(rows)


def unknown_ids(db: Session, ingredient_ids: list[str]) -> list[str]:
    known = names(db, ingredient_ids)
    return [i for i in ingredient_ids if i not in known]


def describe(db: Session, ingredient_ids: list[str]) -> list[dict]:
    known = names(db, ingredient_ids)
    return [{"ingredient_id": i, "name": known.get(i)} for i in ingredient_ids]


def search(db: Session, query: str, limit: int = 20) -> list[dict]:
    """列出名稱／別名候選供使用者選擇；歧義別名只供搜尋，不自動合併 ID。"""
    q = query.strip()
    stmt = select(IngredientAlias.food_id, IngredientAlias.canonical_name,
                  IngredientAlias.aliases, IngredientAlias.ambiguous_aliases)
    if q:
        pattern = f"%{q.replace('%', '').replace('_', '')}%"

        def alias_matches(column):
            # Decode JSON elements before matching: JSON text can contain escaped
            # Unicode even when the decoded alias is an exact Chinese match.
            if db.get_bind().dialect.name == 'postgresql':
                values = func.json_array_elements_text(column).table_valued('value').render_derived()
            else:
                values = func.json_each(column).table_valued('key', 'value')
            return select(1).select_from(values).where(values.c.value.ilike(pattern)).exists()

        stmt = stmt.where(or_(
            IngredientAlias.canonical_name.ilike(pattern),
            alias_matches(IngredientAlias.aliases),
            alias_matches(IngredientAlias.ambiguous_aliases),
        ))
    rows = db.execute(stmt.order_by(IngredientAlias.canonical_name).limit(500)).all()

    def rank(row):
        name = row.canonical_name
        aliases = [*(row.aliases or []), *(row.ambiguous_aliases or [])]
        if name == q or q in aliases:
            return 0
        if name.startswith(q) or any(a.startswith(q) for a in aliases):
            return 1
        return 2

    rows = sorted(rows, key=lambda r: (rank(r), len(r.canonical_name), r.canonical_name)) if q else rows
    return [{"ingredient_id": r.food_id, "name": r.canonical_name} for r in rows[:limit]]
