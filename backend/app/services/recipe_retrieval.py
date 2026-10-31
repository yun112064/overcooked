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
import hashlib
import itertools
from dataclasses import asdict, dataclass, field
from fractions import Fraction

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import Recipe, RecipeIngredient
from app.services import meal_history, synonyms
from app.services.meal_request import MealRequestDraft
from app.services.recipe_import import normalize_name, standard_dish_id

DATABASE_MENU = "database_menu"
NO_FEASIBLE = "no_feasible_database_menu"
UNRESOLVED = "dish_identity_unresolved"
SPECIFIED_CONFLICT = "specified_dish_conflict"
STAPLE_CONFLICT = "specified_dish_staple_conflict"
# 資料庫模式最多交給 T12 驗證的候選組合數（每組都要計算營養）
MAX_COMBINATIONS = 20
# 食譜分類（recipes.category）；決策 #22 的菜色結構
MAIN, STAPLE, SOUP, DESSERT = "主菜", "主食", "湯", "點心"


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
                SPECIFIED_CONFLICT: "SPECIFIED_DISH_DIETARY_CONFLICT",
                STAPLE_CONFLICT: "SPECIFIED_DISH_STAPLE_CONFLICT"}.get(self.status)

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
             "category": r.category,
             "aliases": [a for a in (r.name_aliases or []) if isinstance(a, str) and a.strip()],
             "tags": [t for t in (r.category, r.cuisine, *(r.cooking_methods or [])) if isinstance(t, str) and t],
             "food_ids": sorted(foods.get(r.recipe_id, set()))} for r in rows]


def _group(recipes: list[dict]) -> list[_Dish]:
    """Stored standard ID OR normalized primary name identifies the same dish.

    Shared generic aliases alone stay ambiguous (e.g. two different 炒青菜).
    """
    groups: list[_Dish] = []
    for r in recipes:
        matches = [g for g in groups if any(
            old['standard_dish_id'] == r['standard_dish_id']
            or normalize_name(old['recipe_name']) == normalize_name(r['recipe_name'])
            for old in g.recipes)]
        if not matches:
            groups.append(_Dish(r['standard_dish_id'], [r]))
            continue
        first = matches[0]
        first.recipes.append(r)
        for group in matches[1:]:
            first.recipes.extend(group.recipes)
            groups.remove(group)
    return groups


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
            "standard_dish_id": dish.standard_dish_id, "category": recipe["category"],
            "aliases": dish.identity()["aliases"],
            "food_ids": recipe["food_ids"], "specified": specified}


def tie_break(household_id, meal_date, recipe_id: str) -> str:
    """同分食譜的輪替順序：由（家庭、餐食日期）決定，同一請求可重現，不同日期換菜。"""
    return hashlib.sha256(f"{household_id}|{meal_date}|{recipe_id}".encode()).hexdigest()


def _structured_picks(categories: list[str | None], needed: int, need_main: bool, soup_room: int):
    """依排名順序（字典序）逐一產生符合菜色結構的索引組合，不先產生再丟棄。

    以後綴計數剪枝：只走「剩下的菜仍湊得出合格組合」的分支，所以每個分支都會產出一組。
    """
    n = len(categories)
    mains, others = [0] * (n + 1), [0] * (n + 1)  # 後綴：主菜數、非湯菜數
    soups = [0] * (n + 1)
    for i in range(n - 1, -1, -1):
        mains[i] = mains[i + 1] + (categories[i] == MAIN)
        soups[i] = soups[i + 1] + (categories[i] == SOUP)
        others[i] = others[i + 1] + (categories[i] != SOUP)

    def feasible(start: int, left: int, main: bool, room: int) -> bool:
        if left == 0:
            return not main
        return others[start] + min(room, soups[start]) >= left and (not main or mains[start] > 0)

    def walk(start: int, left: int, main: bool, room: int, prefix: tuple):
        if left == 0:
            yield prefix
            return
        for i in range(start, n):
            if not feasible(i, left, main, room):
                return  # 後綴只會越來越少，之後的起點也不可行
            cat = categories[i]
            if cat == SOUP and room == 0:
                continue
            nxt_main, nxt_room = main and cat != MAIN, room - (cat == SOUP)
            if feasible(i + 1, left - 1, nxt_main, nxt_room):
                yield from walk(i + 1, left - 1, nxt_main, nxt_room, prefix + (i,))

    if feasible(0, needed, need_main, soup_room):
        yield from walk(0, needed, need_main, soup_room, ())


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


def _catalog_matches(identity: dict, dishes: list[_Dish]) -> list[_Dish]:
    exact = [d for d in dishes if any(r['standard_dish_id'] == identity['standard_dish_id'] for r in d.recipes)]
    primary = [d for d in dishes if any(normalize_name(r['recipe_name']) == normalize_name(identity['dish_name']) for r in d.recipes)]
    aliases = [d for d in dishes if meal_history.same_dish(identity, d.identity())]
    return exact or primary or aliases


def resolve_menu_identities(db: Session, identities: list[dict]) -> tuple[list[dict], list[dict]]:
    """Resolve proposed menu dishes with the same ID/name/alias rules as history."""
    catalog = _group(_load_recipes(db))
    resolved, errors = [], []
    for identity in identities:
        matches = _catalog_matches(identity, catalog)
        if len(matches) > 1:
            errors.append({'dish': identity, 'candidates': [d.identity() for d in matches]})
        resolved.append({**identity, 'resolved_standard_dish_id': matches[0].standard_dish_id} if len(matches) == 1
                        else identity)
    return resolved, errors


def _resolve_catalog_history(context: dict, dishes: list[_Dish]) -> dict:
    # Resolve historical identity before applying exceptions. A generic alias alone
    # must not silently exclude several different dishes or exempt the wrong one.
    specified_ids = set(context['specified_dish_ids'])
    context['specified_exceptions'] = []
    resolved_history = set()
    history_ambiguous = []
    kept = []
    for old in context['excluded_dishes']:
        matches = _catalog_matches(old, dishes)
        if len(matches) > 1:
            history_ambiguous.append({'dish': old, 'candidates': [d.identity() for d in matches]})
            continue
        if matches:
            resolved_id = matches[0].standard_dish_id
            if resolved_id in specified_ids:
                context['specified_exceptions'].append(resolved_id)
                continue
            resolved_history.add(resolved_id)
            old = {**old, 'resolved_standard_dish_id': resolved_id}
        kept.append(old)
    context['excluded_dishes'] = kept
    context['resolved_history_ids'] = sorted(resolved_history)
    context['identity_resolution'] = 'catalog-v1'
    context['identity_errors'] = history_ambiguous
    return context


def resolve_previous_day_context(db: Session, context: dict) -> dict:
    """Use the same catalog resolution at retrieval and adoption recheck."""
    return _resolve_catalog_history(context, _group(_load_recipes(db)))


def _retrieve(db: Session, household_id, draft: MealRequestDraft, excluded: set[str]) -> RetrievalResult:
    assert draft.meal_date is not None and draft.dish_count is not None
    dishes = _group(_load_recipes(db))
    found, missing, ambiguous = _resolve_specified(dishes, draft.specified_dishes)
    context = meal_history.previous_day_context(
        db, household_id, draft.meal_date, specified_dish_ids=[])
    if ambiguous:
        return RetrievalResult(UNRESOLVED, reason="SPECIFIED_DISH_AMBIGUOUS",
                               details={"ambiguous": ambiguous}, previous_day=context)
    if missing:
        return RetrievalResult(NO_FEASIBLE, reason="SPECIFIED_DISH_NOT_IN_DATABASE",
                               details={"specified_dishes": missing}, previous_day=context)
    context['specified_dish_ids'] = sorted({d.standard_dish_id for d in found.values()})
    context = _resolve_catalog_history(context, dishes)
    if context['identity_errors']:
        return RetrievalResult(UNRESOLVED, reason='PREVIOUS_DISH_AMBIGUOUS',
                               details={'ambiguous': context['identity_errors']}, previous_day=context)
    resolved_history = set(context['resolved_history_ids'])

    available = synonyms.confirmed_equivalents(draft.available_ingredient_ids)
    taste = draft.taste_preferences
    has_staple = bool(draft.staple and not any(word in draft.staple for word in ("不", "無", "免", "沒")))

    def rank(recipe: dict):
        # 決策 #22：已有食材重疊數 → 重疊比例 → 口味 → 家庭＋餐食日期決定的輪替順序（不再固定照 recipe_id）
        overlap = len(available.intersection(recipe["food_ids"]))
        share = Fraction(overlap, len(recipe["food_ids"])) if recipe["food_ids"] else Fraction(0)
        taste_hit = int(any(tag in taste for tag in recipe["tags"])) if taste else 0
        return (-overlap, -share, -taste_hit, tie_break(household_id, draft.meal_date, recipe["recipe_id"]))

    def allowed(recipe: dict) -> bool:
        return not excluded.intersection(recipe["food_ids"])

    def yesterday(dish: _Dish) -> bool:
        return dish.standard_dish_id in resolved_history

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
    # 決策 #22：有選主食又指定主食類的菜 → 回報讓使用者擇一，不默默丟掉指定菜、也不轉 AI
    staple_clash = [name for name, dish in found.items()
                    if has_staple and any(r["category"] == STAPLE for r in dish.recipes if allowed(r))]
    if staple_clash:
        return RetrievalResult(STAPLE_CONFLICT, reason="SPECIFIED_DISH_STAPLE_CONFLICT",
                               details={"specified_dishes": staple_clash, "staple": draft.staple},
                               previous_day=context)

    selected = [group[0] for group in specified]
    chosen = {s["standard_dish_id"] for s in selected}
    # 決策 #22：點心不放入正餐；有選主食時主食類食譜不進資料庫候選（指定菜另行處理）
    skipped_categories = {DESSERT, STAPLE} if has_staple else {DESSERT}
    pool, pool_ignoring_history = [], 0
    for dish in dishes:
        options = sorted((r for r in dish.recipes if allowed(r) and r["category"] not in skipped_categories), key=rank)
        if not options or dish.standard_dish_id in chosen:
            continue
        pool_ignoring_history += 1
        if yesterday(dish):
            continue
        pool.append((options, dish))
    pool.sort(key=lambda item: rank(item[0][0]))
    ranked_recipes = sorted(((r, d) for options, d in pool for r in options), key=lambda item: rank(item[0]))
    candidates = list(selected) + [_summary(r, d, False) for r, d in ranked_recipes]

    needed = draft.dish_count - len(selected)
    if needed > len(pool):
        reason = ("ALL_EXCLUDED_BY_PREVIOUS_DAY" if needed <= pool_ignoring_history else "NO_MATCHING_RECIPES")
        return RetrievalResult(NO_FEASIBLE, reason=reason,
                               details={"dish_count": draft.dish_count, "feasible_dishes": len(selected) + len(pool)},
                               previous_day=context)
    # 菜色結構：至少一道主菜（指定菜沒有主菜時由資料庫補），湯總數最多一道（指定菜已含湯則不再補）
    need_main = needed > 0 and not any(s["category"] == MAIN for s in selected)
    soup_room = max(0, 1 - sum(s["category"] == SOUP for s in selected))
    if next(_structured_picks([o[0]["category"] for o, _ in pool], needed, need_main, soup_room), None) is None:
        return RetrievalResult(NO_FEASIBLE, reason="MENU_STRUCTURE_UNMET",
                               details={"dish_count": draft.dish_count, "need_main": need_main,
                                        "soup_room": soup_room,
                                        "main_dishes": sum(o[0]["category"] == MAIN for o, _ in pool)},
                               previous_day=context)

    def combinations():
        for fixed in itertools.product(*specified):
            for picked in _structured_picks([options[0]["category"] for options, _ in pool],
                                            needed, need_main, soup_room):
                yield [*fixed, *(_summary(pool[i][0][0], pool[i][1], False) for i in picked)]

    alternatives = list(itertools.islice(combinations(), MAX_COMBINATIONS))
    return RetrievalResult(DATABASE_MENU, selected=alternatives[0], candidates=candidates, previous_day=context,
                           alternatives=alternatives)
