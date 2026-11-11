"""Read-only T01 catalogue for the simulated supermarket."""

import uuid
from typing import Annotated
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import AuthContext, current_auth, current_household_id, verify_origin
from app.api.errors import ApiError
from app.core.config import settings
from app.db.session import get_db
from app.models.recipe import IngredientAlias, Recipe, RecipeDataRelease, RecipeIngredient
from app.services.recipe_volume import source_volume_ml
from app.services import market_handoff


router = APIRouter(prefix="/sim-market", tags=["sim-market"])


class HandoffBody(BaseModel):
    plan_id: uuid.UUID


class CheckoutBody(BaseModel):
    token: str = Field(min_length=1, max_length=2048)


class OrderBody(CheckoutBody):
    quantities: dict[str, Annotated[int, Field(strict=True, ge=1, le=1_000_000)]] = Field(max_length=1000)
    quote_ids: dict[str, Annotated[int, Field(strict=True, ge=1)]] = Field(max_length=1000)


@router.post('/orders')
def confirm_order(body: OrderBody, request: Request, response: Response, db: Session = Depends(get_db)):
    if request.headers.get('origin') != market_handoff.market_origin():
        raise ApiError(403, 'FORBIDDEN', '請求來源未被允許。')
    if not settings.sim_market_real_checkout_enabled:
        raise ApiError(503, 'MARKET_UNAVAILABLE', '持久化模擬結帳尚未啟用。')
    response.headers['Cache-Control'] = 'no-store'
    grant = market_handoff.redeem(db, body.token)
    from app.services.sim_checkout import persist_order
    return persist_order(db, grant, body.quantities, body.quote_ids, get_catalog)


@router.post('/handoffs', dependencies=[Depends(verify_origin)])
def create_handoff(body: HandoffBody, response: Response, auth: AuthContext = Depends(current_auth),
                   household_id=Depends(current_household_id), db: Session = Depends(get_db)):
    response.headers['Cache-Control'] = 'no-store'
    return market_handoff.issue(db, auth, household_id, body.plan_id)


@router.post('/checkout')
def checkout(body: CheckoutBody, request: Request, response: Response, db: Session = Depends(get_db)):
    if request.headers.get('origin') != market_handoff.market_origin():
        raise ApiError(403, 'FORBIDDEN', '請求來源未被允許。')
    response.headers['Cache-Control'] = 'no-store'
    result = market_handoff.redeem(db, body.token)
    result['catalog'] = get_catalog(db)
    if settings.sim_market_real_checkout_enabled:
        from app.db.models import SimMarketOrder
        order = db.scalar(select(SimMarketOrder).where(SimMarketOrder.handoff_id == result['handoff_id']))
        result['order'] = order.receipt if order else None
        if order:
            result['catalog'] = {'recipe_release_id': order.receipt['recipe_release_id'],
                                 'real_checkout': True, 'products': order.receipt['lines']}
    return result

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
    if settings.sim_market_real_checkout_enabled:
        from app.services.sim_checkout import real_products
        names = {food_id: name for food_id, name, ingredient in used}
        return {'recipe_release_id': release_id, 'real_checkout': True,
                'products': real_products(db, release_id, names)}
    for food_id, name, ingredient in used:
        capacity = source_volume_ml(ingredient) is not None
        if food_id not in products or capacity:
            products[food_id] = _product(food_id, name, capacity)
    return {"recipe_release_id": release_id, "products": list(products.values())}
