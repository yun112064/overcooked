"""Opt-in simulated orders backed by current, source-bound T02 observations."""
import hashlib
import json
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

from sqlalchemy import func, select

from app.api.errors import ApiError
from app.db.models import AdoptedMealPlan, MealConfirmationEvent, SimMarketOrder, utcnow
from app.source_adapters.price_scope import latest_quotes, quote_unusable_reason


def ingredient_category(name):
    for category, terms in (
        ('豆蛋乳品', ('豆腐', '豆干', '豆乾', '雞蛋', '鴨蛋', '蛋液', '牛奶', '起司', '乳酪')),
        ('肉品海鮮', ('豬', '牛肉', '雞', '羊肉', '鴨肉', '魚', '蝦', '貝', '蛤', '蟹', '魷')),
        ('蔬果菇類', ('菜', '瓜', '菇', '薯', '筍', '筊白', '蕃茄', '番茄', '椒', '蘿蔔', '洋蔥', '大蒜', '蘋果', '檸檬')),
        ('米麵雜糧', ('米', '麵', '燕麥', '地瓜粉', '粉絲')),
    ):
        if any(term in name for term in terms):
            return category
    return '其他食材'


def source_image(quote):
    # Only the selected source product may supply its picture; never borrow a rejected candidate.
    candidate = next((c for c in quote.candidates if c.product_id == quote.product_id and quote.product_id), None)
    if candidate is None:
        return None
    image = candidate.raw.get('image')
    if isinstance(image, list):
        image = image[0] if image else None
    if isinstance(image, dict):
        image = image.get('url') or image.get('contentUrl')
    if not isinstance(image, str):
        return None
    try:
        url = urlsplit(image)
        if url.scheme == 'https' and url.hostname and not url.username and not url.password:
            return image
    except ValueError:
        pass
    return None


def real_catalog(db, release_id, names):
    now = datetime.now(timezone.utc)
    quotes = latest_quotes(db, names, now=now)
    products = real_products(db, release_id, names, quotes=quotes)
    available = {p['food_id'] for p in products}
    unavailable = [dict(food_id=food_id, name=name, category=ingredient_category(name),
                        reason=quote_unusable_reason(quotes.get(food_id), now, allow_volume=True) or 'PACKAGE_NOT_PURCHASABLE')
                   for food_id, name in names.items() if food_id not in available]
    return dict(recipe_release_id=release_id, real_checkout=True, products=products,
                unavailable_ingredients=unavailable, ingredient_count=len(names))


def real_products(db, release_id, names, *, quotes=None):
    now = datetime.now(timezone.utc)
    products = []
    for food_id, quote in (quotes if quotes is not None else latest_quotes(db, names, now=now)).items():
        if quote.recipe_release_id != release_id or quote_unusable_reason(quote, now, allow_volume=True):
            continue
        size = quote.package_quantity
        unit = quote.package_unit.casefold()
        if unit in {'l', '公升'}:
            size *= 1000
        cents = quote.price * 100
        # The cart contract requires integral grams/ml and cents. Do not round source data.
        if (size != size.to_integral_value() or cents != cents.to_integral_value() or not quote.snapshot_id
                or size > 9007199254740991 or cents > 9007199254740991):
            continue
        products.append(dict(food_id=food_id, name=names[food_id], category=ingredient_category(names[food_id]), image_url=source_image(quote),
            package_grams=int(size) if unit == 'g' else None,
            package_ml=None if unit == 'g' else int(size), price_cents=int(cents),
            quote_snapshot_id=quote.snapshot_id, source=quote.source, source_url=quote.source_url,
            product_url=quote.product_url, product_name=quote.product_name,
            queried_at=quote.queried_at.isoformat(), valid_until=quote.valid_until.isoformat() if quote.valid_until else None,
            region_condition=quote.region_condition, store_condition=quote.store_condition,
            promotion_description=quote.promotion_description, currency='TWD'))
    return products


def persist_order(db, grant, quantities, quote_ids, catalog_loader):
    plan_id = uuid.UUID(grant['plan']['id'])
    plan = db.scalar(select(AdoptedMealPlan).where(AdoptedMealPlan.id == plan_id).with_for_update())
    if plan is None or plan.status != 'adopted':
        raise ApiError(409, 'MARKET_PLAN_UNAVAILABLE', '餐單已失效。')
    digest = hashlib.sha256(json.dumps([quantities, quote_ids], sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    existing = db.scalar(select(SimMarketOrder).where(SimMarketOrder.handoff_id == grant['handoff_id']))
    if existing:
        if existing.request_hash != digest:
            raise ApiError(409, 'MARKET_ORDER_CONFLICT', '此購物連結已結帳，內容不可修改。')
        return existing.receipt
    catalog = catalog_loader(db)
    snapshot = grant['plan'].get('snapshot') or {}
    releases = [snapshot['market_shortages'].get('recipe_release_id')] if 'market_shortages' in snapshot else [
        dish.get('recipe_release_id') for dish in snapshot.get('result', {}).get('dishes', [])]
    if not releases or any(release != catalog['recipe_release_id'] for release in releases):
        raise ApiError(409, 'MARKET_RELEASE_CHANGED', '餐單與商品目錄版本不同。')
    products = {row['food_id']: row for row in catalog['products']}
    if not quantities or set(quantities) != set(quote_ids):
        raise ApiError(422, 'MARKET_CART_INVALID', '購物車內容不完整。')
    lines = []
    for food_id, quantity in sorted(quantities.items()):
        product = products.get(food_id)
        if not product or product['quote_snapshot_id'] != quote_ids[food_id]:
            raise ApiError(409, 'MARKET_PRICE_CHANGED', '商品報價已更新或失效，請重新載入購物清單。')
        lines.append({**product, 'quantity': quantity, 'line_total_cents': product['price_cents'] * quantity})
    total = sum(line['line_total_cents'] for line in lines)
    if total > 9007199254740991:
        raise ApiError(422, 'MARKET_CART_INVALID', '購物車金額超出上限。')
    order_id = uuid.uuid4()
    receipt = dict(order_id=str(order_id), plan_id=str(plan_id), simulated=True, currency='TWD',
                   recipe_release_id=catalog['recipe_release_id'], lines=lines, package_total_cents=total,
                   confirmed_at=utcnow().isoformat())
    db.add(SimMarketOrder(id=order_id, plan_id=plan_id, handoff_id=grant['handoff_id'], request_hash=digest, receipt=receipt))
    sequence = (db.scalar(select(func.max(MealConfirmationEvent.sequence)).where(MealConfirmationEvent.plan_id == plan_id)) or 0) + 1
    db.add(MealConfirmationEvent(plan_id=plan_id, sequence=sequence, stage='sim_checkout_confirmed', occurred_at=utcnow()))
    db.commit()
    return receipt
