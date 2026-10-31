"""Build the T02 collection scope from the active SQL recipe release."""

from __future__ import annotations

from collections import OrderedDict

from sqlalchemy import Engine, text

from .price_models import IngredientQuery


ACTIVE_RECIPE_INGREDIENTS_SQL = text(
    """
    SELECT f.food_id, f.canonical_name, f.aliases
    FROM ingredient_aliases AS f
    JOIN recipe_data_releases AS release
      ON release.release_id = f.recipe_release_id
     AND release.active = TRUE
    WHERE EXISTS (
        SELECT 1
        FROM recipe_ingredients AS i
        WHERE i.food_id = f.food_id
          AND i.recipe_release_id = release.release_id
    )
    ORDER BY f.canonical_name, f.food_id
    """
)


def load_ingredient_queries(engine: Engine) -> list[IngredientQuery]:
    """Return one price query per canonical name used by active recipes."""

    with engine.connect() as connection:
        rows = connection.execute(ACTIVE_RECIPE_INGREDIENTS_SQL).mappings().all()

    grouped: OrderedDict[str, dict[str, object]] = OrderedDict()
    for row in rows:
        canonical_name = str(row["canonical_name"]).strip()
        food_id = str(row["food_id"]).strip()
        if not canonical_name or not food_id:
            continue
        item = grouped.setdefault(
            canonical_name,
            {
                "canonical_name": canonical_name,
                "food_ids": [],
                "aliases": [],
                "mapping_missing_reason": None,
            },
        )
        item["food_ids"].append(food_id)
        for alias in row["aliases"] or []:
            alias = str(alias).strip()
            if alias and alias != canonical_name and alias not in item["aliases"]:
                item["aliases"].append(alias)
    return [IngredientQuery(**values) for values in grouped.values()]
