"""Exact source capacities for pricing, separate from nutrition mass evidence."""
from decimal import Decimal, InvalidOperation
from fractions import Fraction


VOLUME_UNITS = {"ml": Decimal(1), "毫升": Decimal(1), "l": Decimal(1000),
                "公升": Decimal(1000), "升": Decimal(1000)}


def volume_in_ml(amount, unit):
    multiplier = VOLUME_UNITS.get(str(unit).strip().casefold())
    if amount is None or multiplier is None:
        return None
    try:
        value = Decimal(str(amount)) * multiplier
    except InvalidOperation:
        return None
    return value if value.is_finite() and value > 0 else None


def source_volume_ml(row):
    if getattr(row, "quantity_status", None) != "source_exact":
        return None
    if getattr(row, "volume_evidence", None) not in ("workbook_volume", "source_explicit_volume"):
        return None
    stored = getattr(row, "volume_ml", None)
    expected = volume_in_ml(getattr(row, "amount", None), getattr(row, "unit", None))
    if expected is None or stored is None or Decimal(str(stored)) != expected:
        return None
    return expected


def attach_preview_volumes(db, result, payload):
    """Attach capacity to exact source line IDs without altering nutritional inputs."""
    from sqlalchemy import select
    from app.models.recipe import RecipeIngredient

    lines = [line for dish in payload.get("dishes") or [] for line in dish["ingredients"]]
    ids = {line["line_id"] for line in lines if line.get("line_id")}
    if not ids:
        return
    sources = {row.recipe_ingredient_id: row for row in db.scalars(
        select(RecipeIngredient).where(RecipeIngredient.recipe_ingredient_id.in_(ids)))}
    labels = result.line_labels or {}
    for dish in payload.get("dishes") or []:
        for line in dish["ingredients"]:
            source = sources.get(line.get("line_id"))
            volume = source_volume_ml(source) if source is not None else None
            if source is None or source.recipe_id != dish["recipe_id"] or volume is None:
                line["volume_ml"] = None
                continue
            scale = Fraction(1)
            if dish.get("adjusted"):
                grams = (labels.get(line["line_id"]) or {}).get("grams")
                if grams is None or Fraction(grams) <= 0 or line.get("grams") is None:
                    line["volume_ml"] = None
                    continue
                scale = Fraction(line["grams"]) / Fraction(grams)
            line["volume_ml"] = str(Fraction(volume) * scale)
