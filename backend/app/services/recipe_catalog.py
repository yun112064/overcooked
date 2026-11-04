"""食譜庫選菜（2026-11-04）：把合格食譜依菜系、菜色類型、主食種類分類，供問卷直接勾選指定菜。

每道菜以 T11 的標準菜色分組（同一道菜的不同來源只列一次），菜名就是指定菜要填的名稱；
名稱對到多道菜（會被 T11 判為 dish_identity_unresolved）的不列出。含本餐過敏／禁吃食材的菜
照列但標出衝突食材，前端不能勾選（送出也會被 T11 擋下）。
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Recipe, RecipeIngredient
from app.services.recipe_import import normalize_name
from app.services.recipe_retrieval import DESSERT, MAIN, SOUP, STAPLE, _group

CATEGORY_ORDER = (MAIN, SOUP, STAPLE, DESSERT)
# 主食細分（依菜名判斷，順序即優先序：「烏龍麵」算麵、「蛋炒飯」算飯）
STAPLE_KINDS = (("粥", ("粥",)), ("麵", ("麵", "米粉", "烏龍")), ("飯", ("飯", "丼")))
OTHER_STAPLE = "其他主食"
# 主要食材（2026-11-04 使用者要求：牛豬雞羊、海鮮、蔬菜等）：依食材名稱判斷，順序即優先序
# （「雞蛋豆腐」算豆腐、「雞蛋」算蛋、「雞里肌」算雞）；一道菜可有多個
MAIN_INGREDIENT_RULES = (
    ("豆腐", ("豆腐", "豆干", "豆乾", "豆皮", "百頁")),
    ("蛋", ("蛋",)),
    ("雞肉", ("雞",)),
    ("牛肉", ("牛",)),
    ("羊肉", ("羊",)),
    ("海鮮", ("魚", "蝦", "蟹", "蛤", "蚵", "透抽", "小卷", "魷", "花枝", "章魚", "干貝", "鮭", "鯛", "鱈", "鮪",
              "鯖", "鱸", "貝", "甜不辣")),
    ("豬肉", ("豬", "五花", "排骨", "絞肉", "培根", "火腿", "里肌", "梅花", "香腸", "肉絲", "肉片", "腰內肉",
              "肉燥", "貢丸", "松阪")),
)
MEATS = {"雞肉", "牛肉", "羊肉", "海鮮", "豬肉"}
VEGETABLE = "蔬菜（無肉）"
# 調味、高湯、乾貨提味不算主要食材（「蠔油」「雞粉」「蝦米」「柴魚」不讓菜變成海鮮或雞肉）
NOT_MAIN_SUFFIXES = ("粉", "醬", "油", "露", "汁", "精", "水", "湯", "乳")
NOT_MAIN_WORDS = ("高湯", "蝦米", "蝦皮", "柴魚", "昆布", "牛奶", "牛蒡", "牛番茄")
PREVIEW_INGREDIENTS = 6


def staple_kind(name: str, category: str) -> str | None:
    if category != STAPLE:
        return None
    return next((kind for kind, words in STAPLE_KINDS if any(w in name for w in words)), OTHER_STAPLE)


def main_ingredients(names) -> list[str]:
    found = set()
    for name in names:
        if name.endswith(NOT_MAIN_SUFFIXES) or any(w in name for w in NOT_MAIN_WORDS):
            continue
        tag = next((tag for tag, words in MAIN_INGREDIENT_RULES if any(w in name for w in words)), None)
        if tag:
            found.add(tag)
    if not found & MEATS:
        found.add(VEGETABLE)
    order = [tag for tag, _ in MAIN_INGREDIENT_RULES] + [VEGETABLE]
    return sorted(found, key=order.index)


def _facet(values) -> list[dict]:
    counts: dict[str, int] = {}
    for v in values:
        if v:
            counts[v] = counts.get(v, 0) + 1
    return [{"value": v, "count": c} for v, c in sorted(counts.items(), key=lambda kv: -kv[1])]


def catalog(db: Session, excluded_food_ids: set[str]) -> dict:
    rows = db.execute(
        select(Recipe.recipe_id, Recipe.standard_dish_id, Recipe.recipe_name, Recipe.name_aliases,
               Recipe.category, Recipe.cuisine, Recipe.meal_type)
        .where(Recipe.is_eligible.is_(True)).order_by(Recipe.recipe_id)
    ).all()
    ingredients: dict[str, list] = {}
    for line in db.execute(
        select(RecipeIngredient.recipe_id, RecipeIngredient.food_id, RecipeIngredient.canonical_name)
        .join(Recipe, Recipe.recipe_id == RecipeIngredient.recipe_id)
        .where(Recipe.is_eligible.is_(True)).order_by(RecipeIngredient.recipe_id, RecipeIngredient.sequence)
    ):
        ingredients.setdefault(line.recipe_id, []).append(line)
    recipes = [{"recipe_id": r.recipe_id, "standard_dish_id": r.standard_dish_id, "recipe_name": r.recipe_name,
                "category": r.category, "cuisine": r.cuisine, "meal_type": r.meal_type,
                "aliases": [a for a in (r.name_aliases or []) if isinstance(a, str) and a.strip()]} for r in rows]
    dishes = _group(recipes)
    # 與 T11 _resolve_specified 相同的比對：菜名正規化後只能對到一道菜
    owners: dict[str, set[str]] = {}
    for dish in dishes:
        for name in dish.names:
            owners.setdefault(normalize_name(name), set()).add(dish.standard_dish_id)

    items = []
    for dish in dishes:
        first = dish.recipes[0]
        name = first["recipe_name"]
        if len(owners.get(normalize_name(name), ())) != 1:
            continue
        lines = ingredients.get(first["recipe_id"], [])
        # 每個來源都含禁忌才算衝突（T11 會挑沒有衝突的那份食譜）
        clashes = [{ln.canonical_name for ln in ingredients.get(r["recipe_id"], []) if ln.food_id in excluded_food_ids}
                   for r in dish.recipes]
        conflicts = sorted(clashes[0]) if all(clashes) else []
        items.append({
            "dish_name": name, "standard_dish_id": dish.standard_dish_id,
            "cuisine": first["cuisine"], "category": first["category"],
            "staple_kind": staple_kind(name, first["category"]), "meal_type": first["meal_type"],
            "main_ingredients": main_ingredients(ln.canonical_name for ln in lines),
            "ingredients": list(dict.fromkeys(ln.canonical_name for ln in lines))[:PREVIEW_INGREDIENTS],
            "conflicts": conflicts,
        })
    rank = {c: i for i, c in enumerate(CATEGORY_ORDER)}
    items.sort(key=lambda d: (rank.get(d["category"], len(rank)), d["dish_name"]))
    return {
        "dishes": items,
        "facets": {"main_ingredient": _facet(t for d in items for t in d["main_ingredients"]),
                   "cuisine": _facet(d["cuisine"] for d in items),
                   "category": sorted(_facet(d["category"] for d in items),
                                      key=lambda f: rank.get(f["value"], len(rank))),
                   "staple_kind": _facet(d["staple_kind"] for d in items)},
    }
