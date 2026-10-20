"""T11 RecipeRetrievalService：雲端結構化欄位及規則查詢（R04），不用 embedding。

輸入問卷、必要限制（排除的 FOOD 編號）與 R13 昨日排除，回傳四種結果之一：
- database_menu：只含資料庫合格食譜（is_eligible）的菜單、依排名的候選組合（alternatives，
  第一組即 selected，最多 MAX_COMBINATIONS 組，供 T14 依序以 T12 驗證）與候選清單；
- no_feasible_database_menu：明確原因，供自動轉 AI 生成（不再詢問使用者）；
- dish_identity_unresolved：指定菜對到多道不同菜，回報問題而不默認為不同菜；
- specified_dish_conflict：指定菜的所有食譜都含過敏／禁吃食材 → 回報衝突原因，不轉 AI、不偷偷換菜
  （spec「與禁忌…衝突時回報原因，不偷偷換菜」、AC09）。
資料庫故障拋出 RecipeRetrievalError，不當作查無結果。

已有食材與口味偏好只影響排序；預算與營養由 T12／T13／T15 計算驗證。
"""
import itertools
from dataclasses import asdict, dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import Recipe, RecipeIngredient
from app.services import meal_history, synonyms
from app.services.meal_request import MealRequestDraft
from app.services.recipe_import import normalize_name

DATABASE_MENU = "database_menu"
NO_FEASIBLE = "no_feasible_database_menu"
UNRESOLVED = "dish_identity_unresolved"
SPECIFIED_CONFLICT = "specified_dish_conflict"
# 資料庫模式最多交給 T12 驗證的候選組合數（每組都要計算營養）
MAX_COMBINATIONS = 20


class RecipeRetrievalError(Exception):
    """食譜資料庫查詢故障：顯示失敗並保留草稿供重試，不可轉成「查無結果」。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class RetrievalResult:
    status: str
    selected: list[dict] = field(default_factory=list)
    candidates: list[dict] = field(default_factory=list)
    reason: str | None = None
    details: dict = field(default_factory=dict)
    previous_day: dict | None = None
    alternatives: list[list[dict]] = field(default_factory=list)

    @property
    def code(self) -> str | None:
        return {NO_FEASIBLE: "NO_FEASIBLE_DATABASE_MENU", UNRESOLVED: "DISH_IDENTITY_UNRESOLVED",
                SPECIFIED_CONFLICT: "SPECIFIED_DISH_DIETARY_CONFLICT"}.get(self.status)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["code"] = self.code
        return data


@dataclass
class _Dish:
    """同一標準菜色 ID 的所有合格食譜（不同來源的同一道菜）。"""
    standard_dish_id: str
    recipes: list[dict]

    @property
    def names(self) -> set[str]:
        return {n for r in self.recipes for n in (r["recipe_name"], *r["aliases"])}

    def identity(self) -> dict:
        first = self.recipes[0]
        aliases = sorted(self.names - {first["recipe_name"]})
        return {"standard_dish_id": self.standard_dish_id, "dish_name": first["recipe_name"], "aliases": aliases}


def _load_recipes(db: Session) -> list[dict]:
    rows = db.execute(
        select(Recipe.recipe_id, Recipe.standard_dish_id, Recipe.recipe_name, Recipe.name_aliases,
               Recipe.category, Recipe.cuisine, Recipe.cooking_methods)
        .where(Recipe.is_eligible.is_(True))
        .order_by(Recipe.recipe_id)
    ).all()
    foods: dict[str, set[str]] = {}
    for recipe_id, food_id in db.execute(
        select(RecipeIngredient.recipe_id, RecipeIngredient.food_id)
        .join(Recipe, Recipe.recipe_id == RecipeIngredient.recipe_id)
        .where(Recipe.is_eligible.is_(True))
    ):
        foods.setdefault(recipe_id, set()).add(food_id)
    return [{"recipe_id": r.recipe_id, "standard_dish_id": r.standard_dish_id, "recipe_name": r.recipe_name,
             "aliases": [a for a in (r.name_aliases or []) if isinstance(a, str) and a.strip()],
             "tags": [t for t in (r.category, r.cuisine, *(r.cooking_methods or [])) if isinstance(t, str) and t],
             "food_ids": sorted(foods.get(r.recipe_id, set()))} for r in rows]


def _group(recipes: list[dict]) -> list[_Dish]:
    groups: dict[str, _Dish] = {}
    for r in recipes:
        groups.setdefault(r["standard_dish_id"], _Dish(r["standard_dish_id"], [])).recipes.append(r)
    return list(groups.values())


def _resolve_specified(dishes: list[_Dish], names: list[str]) -> tuple[dict[str, _Dish], list[str], dict]:
    found, missing, ambiguous = {}, [], {}
    for name in names:
        key = normalize_name(name)
        hits = [d for d in dishes if key and key in {normalize_name(n) for n in d.names}]
        if not hits:
            missing.append(name)
        elif len(hits) > 1:
            ambiguous[name] = [d.identity() for d in hits]
        else:
            found[name] = hits[0]
    return found, missing, ambiguous


def _summary(recipe: dict, dish: _Dish, specified: bool) -> dict:
    return {"recipe_id": recipe["recipe_id"], "recipe_name": recipe["recipe_name"],
            "standard_dish_id": dish.standard_dish_id, "aliases": dish.identity()["aliases"],
            "food_ids": recipe["food_ids"], "specified": specified}


def retrieve_database_menu(db: Session, household_id, draft: MealRequestDraft, *,
                           excluded_food_ids: list[str]) -> RetrievalResult:
    if draft.meal_date is None or draft.dish_count is None:
        raise ValueError("查詢食譜需要餐食日期與菜數。")
    if len(draft.specified_dishes) > draft.dish_count:
        raise ValueError("指定菜計入菜數，不能超過菜數。")
    try:
        return _retrieve(db, household_id, draft, set(excluded_food_ids))
    except SQLAlchemyError as exc:
        raise RecipeRetrievalError("RECIPE_DATABASE_UNAVAILABLE",
                                   "食譜資料庫暫時無法查詢；問卷已保留，請稍後重試。") from exc


def _retrieve(db: Session, household_id, draft: MealRequestDraft, excluded: set[str]) -> RetrievalResult:
    dishes = _group(_load_recipes(db))
    found, missing, ambiguous = _resolve_specified(dishes, draft.specified_dishes)
    context = meal_history.previous_day_context(
        db, household_id, draft.meal_date, specified_dish_ids=[d.standard_dish_id for d in found.values()])

    if ambiguous:
        return RetrievalResult(UNRESOLVED, reason="SPECIFIED_DISH_AMBIGUOUS",
                               details={"ambiguous": ambiguous}, previous_day=context)
    if missing:
        return RetrievalResult(NO_FEASIBLE, reason="SPECIFIED_DISH_NOT_IN_DATABASE",
                               details={"specified_dishes": missing}, previous_day=context)

    available = synonyms.confirmed_equivalents(draft.available_ingredient_ids)
    taste = draft.taste_preferences

    def rank(recipe: dict):
        overlap = len(available.intersection(recipe["food_ids"]))
        taste_hit = int(any(tag in taste for tag in recipe["tags"])) if taste else 0
        return (-overlap, -taste_hit, recipe["recipe_id"])

    def allowed(recipe: dict) -> bool:
        return not excluded.intersection(recipe["food_ids"])

    def yesterday(dish: _Dish) -> bool:
        identity = {"standard_dish_id": dish.standard_dish_id, "dish_name": dish.recipes[0]["recipe_name"],
                    "aliases": sorted(dish.names)}
        return bool(meal_history.conflicts(context, [identity]))

    specified: list[list[dict]] = []  # 每道指定菜依排名的可用食譜
    conflicts: list[dict] = []
    for name, dish in found.items():
        options = sorted((r for r in dish.recipes if allowed(r)), key=rank)
        if not options:
            hits = sorted({f for r in dish.recipes for f in excluded.intersection(r["food_ids"])})
            conflicts.append({"dish_name": name, "food_ids": hits})
            continue
        if not any(group[0]["standard_dish_id"] == dish.standard_dish_id for group in specified):
            specified.append([_summary(r, dish, True) for r in options])
    if conflicts:
        return RetrievalResult(SPECIFIED_CONFLICT, reason="SPECIFIED_DISH_DIETARY_CONFLICT",
                               details={"specified_dishes": [c["dish_name"] for c in conflicts],
                                        "conflicts": conflicts}, previous_day=context)

    selected = [group[0] for group in specified]
    chosen = {s["standard_dish_id"] for s in selected}
    pool, pool_ignoring_history = [], 0
    for dish in dishes:
        options = sorted((r for r in dish.recipes if allowed(r)), key=rank)
        if not options or dish.standard_dish_id in chosen:
            continue
        pool_ignoring_history += 1
        if yesterday(dish):
            continue
        pool.append((options, dish))
    candidates = list(selected) + [_summary(r, d, False) for options, d in pool for r in options]
    pool.sort(key=lambda item: rank(item[0][0]))

    needed = draft.dish_count - len(selected)
    if needed > len(pool):
        reason = ("ALL_EXCLUDED_BY_PREVIOUS_DAY" if needed <= pool_ignoring_history else "NO_MATCHING_RECIPES")
        return RetrievalResult(NO_FEASIBLE, reason=reason,
                               details={"dish_count": draft.dish_count, "feasible_dishes": len(selected) + len(pool)},
                               previous_day=context)
    def combinations():
        for fixed in itertools.product(*specified):
            for picked in itertools.combinations(pool, needed):
                yield [*fixed, *(_summary(options[0], dish, False) for options, dish in picked)]

    alternatives = list(itertools.islice(combinations(), MAX_COMBINATIONS))
    return RetrievalResult(DATABASE_MENU, selected=alternatives[0], candidates=candidates, previous_day=context,
                           alternatives=alternatives)
