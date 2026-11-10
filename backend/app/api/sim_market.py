"""Read-only T01 catalogue for the simulated supermarket."""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import current_household_id, verify_origin
from app.core.config import settings
from app.db.session import get_db
from app.models.recipe import IngredientAlias, Recipe, RecipeDataRelease, RecipeIngredient
from app.services.recipe_volume import source_volume_ml


router = APIRouter(prefix="/sim-market", tags=["sim-market"], dependencies=[Depends(verify_origin)])

# Store-defined demonstration packs and TWD cents. These values do not come from T01 or T02.
DEMO_PACKS = (
    ("豆蛋乳品", ("豆腐", "豆干", "豆乾", "雞蛋", "鴨蛋", "蛋液", "牛奶", "起司", "乳酪"), 300, 4900),
    ("肉品海鮮", ("豬", "牛肉", "雞", "羊肉", "鴨肉", "魚", "蝦", "貝", "蛤", "蟹", "魷"), 300, 9900),
    ("蔬果菇類", ("菜", "瓜", "菇", "薯", "筍", "筊白", "蕃茄", "番茄", "椒", "蘿蔔", "蘋果", "檸檬"), 500, 5900),
    ("米麵雜糧", ("米", "麵", "燕麥", "地瓜粉", "粉絲"), 600, 6900),
)


def _product(food_id: str, name: str, capacity: bool = False) -> dict:
    category, grams, cents = next(
        ((category, grams, cents) for category, terms, grams, cents in DEMO_PACKS
         if any(term in name for term in terms)),
        ("其他食材", 300, 4900),
    )
    product = {"food_id": food_id, "name": name, "category": category,
               "package_grams": grams, "price_cents": cents}
    if capacity:
        product.update(package_grams=None, package_ml=500, price_cents=15000)
    return product


@router.get("/catalog", dependencies=[Depends(current_household_id)])
def get_catalog(db: Session = Depends(get_db)):
    explicit = (settings.generation_recipe_release_id or "").strip()
    if explicit:
        release = db.get(RecipeDataRelease, explicit)
        release_id = release.release_id if release else None
    else:
        active = db.scalars(select(RecipeDataRelease.release_id).where(RecipeDataRelease.active.is_(True)).limit(2)).all()
        release_id = active[0] if len(active) == 1 else None
    if release_id is None:
        return {"recipe_release_id": None, "products": []}

    used = db.execute(
        select(IngredientAlias.food_id, IngredientAlias.canonical_name, RecipeIngredient)
        .join(RecipeIngredient, RecipeIngredient.food_id == IngredientAlias.food_id)
        .join(Recipe, Recipe.recipe_id == RecipeIngredient.recipe_id)
        .where(IngredientAlias.recipe_release_id == release_id,
               RecipeIngredient.recipe_release_id == release_id,
               Recipe.recipe_release_id == release_id)
        .order_by(IngredientAlias.canonical_name, IngredientAlias.food_id)
    )
    products = {}
    for food_id, name, ingredient in used:
        capacity = source_volume_ml(ingredient) is not None
        if food_id not in products or capacity:
            products[food_id] = _product(food_id, name, capacity)
    return {"recipe_release_id": release_id, "products": list(products.values())}
