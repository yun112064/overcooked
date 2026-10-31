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
同一份菜單裡，兩道非指定菜不能以同一種蔬菜為主（2026-10-26 使用者決定，例如「清炒油菜」配「蒜香炒油菜」）。
"""
import hashlib
import itertools
from dataclasses import asdict, dataclass, field
from fractions import Fraction

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import Recipe, RecipeIngredient
from app.services import meal_history, staples, synonyms
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
# TFDA 樣本編號首字母：E 蔬菜、F 藻類、G 菇類 → 視為「蔬菜」判斷主菜蔬
VEGETABLE_SAMPLE_PREFIXES = ("E", "F", "G")
# 爆香／配色用的辛香料不算一道菜的主菜蔬（洋蔥除外，常是主料）
AROMATIC_KEYWORDS = ("蒜", "蔥", "薑", "辣椒", "香菜", "九層塔")


def _aromatic(name: str) -> bool:
    return "洋蔥" not in name and any(k in name for k in AROMATIC_KEYWORDS)


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
    lines: dict[str, list] = {}
    for line in db.execute(
        select(RecipeIngredient.recipe_id, RecipeIngredient.recipe_ingredient_id, RecipeIngredient.food_id,
               RecipeIngredient.canonical_name, RecipeIngredient.weight_g, RecipeIngredient.source_weight_g)
        .join(Recipe, Recipe.recipe_id == RecipeIngredient.recipe_id)
        .where(Recipe.is_eligible.is_(True))
    ):
        foods.setdefault(line.recipe_id, set()).add(line.food_id)
        lines.setdefault(line.recipe_id, []).append(line)
    main_vegetable = _main_vegetables(db, lines)
    return [{"recipe_id": r.recipe_id, "standard_dish_id": r.standard_dish_id, "recipe_name": r.recipe_name,
             "category": r.category,
             "aliases": [a for a in (r.name_aliases or []) if isinstance(a, str) and a.strip()],
             "tags": [t for t in (r.category, r.cuisine, *(r.cooking_methods or [])) if isinstance(t, str) and t],
             "food_ids": sorted(foods.get(r.recipe_id, set())),
             "main_vegetable": main_vegetable.get(r.recipe_id)} for r in rows]


def _main_vegetables(db: Session, lines: dict[str, list]) -> dict[str, str]:
    """每道食譜的主菜蔬：蔬菜／菇／藻類食材（不含辛香料）中克數最大者，以 TFDA 樣本編號表示（同種異名視為同一種）。
    同克數取食譜中較前面的食材。

    克數依工作簿並套用 config/recipe_weight_corrections.json；沒有蔬菜或無法對照的食譜沒有主菜蔬。
    """
    from app.services import tfda_mapping
    from app.services.meal_calculation import weight_corrections

    resolver = tfda_mapping.load_resolver(db)
    _, fixed = weight_corrections()
    samples: dict[tuple, tuple | None] = {}
    out = {}
    for recipe_id, rows in lines.items():
        best = None
        for row in sorted(rows, key=lambda r: r.recipe_ingredient_id):
            grams = fixed.get(row.recipe_ingredient_id, row.weight_g if row.weight_g is not None else row.source_weight_g)
            if not grams:
                continue
            key = (row.food_id, row.canonical_name)
            if key not in samples:
                res = resolver.resolve(row.food_id, row.canonical_name)
                samples[key] = (res.sample_id, res.tfda_name) if res else None
            if samples[key] is None:
                continue
            sample, tfda_name = samples[key]
            # 辛香料以食譜名稱或對到的 TFDA 名稱判斷（食譜名稱可能是別名）
            if _aromatic(row.canonical_name or "") or _aromatic(tfda_name or ""):
                continue
            if sample.startswith(VEGETABLE_SAMPLE_PREFIXES) and (best is None or grams > best[0]):
                best = (grams, sample)
        if best:
            out[recipe_id] = best[1]
    return out


def _distinct_vegetables(menu: list[dict], vegetable_of: dict[str, str | None]) -> bool:
    """非指定菜的主菜蔬不能與菜單中其他菜重複；使用者自己指定的菜彼此重複則尊重。"""
    seen: set[str] = set()
    for dish in sorted(menu, key=lambda d: not d["specified"]):  # 指定菜先登記
        veg = vegetable_of.get(dish["recipe_id"])
        if veg is None:
            continue
        if veg in seen and not dish["specified"]:
            return False
        seen.add(veg)
    return True


def _group(recipes: list[dict]) -> list[_Dish]:
    """依菜名重新計算標準菜色 ID 分組（決策 #36）：異體字規則上線前匯入、存成不同 ID 的
    「番茄炒蛋／蕃茄炒蛋」仍是同一道菜，不必等重新匯入。"""
    groups: dict[str, _Dish] = {}
    for r in recipes:
        key = standard_dish_id(r["recipe_name"])
        groups.setdefault(key, _Dish(key, [])).recipes.append(r)
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
            "standard_dish_id": dish.standard_dish_id, "category": recipe["category"],
            "aliases": dish.identity()["aliases"],
            "food_ids": recipe["food_ids"], "specified": specified}


def tie_break(household_id, meal_date, recipe_id: str) -> str:
    """同分食譜的輪替順序：由（家庭、餐食日期）決定，同一請求可重現，不同日期換菜。"""
    return hashlib.sha256(f"{household_id}|{meal_date}|{recipe_id}".encode()).hexdigest()


def _structured_picks(categories: list[str | None], needed: int, need_main: bool, soup_room: int,
                      vegetables: list[str | None] | None = None, taken: frozenset = frozenset(),
                      budget: list[int] | None = None):
    """依排名順序（字典序）逐一產生符合菜色結構的索引組合，不先產生再丟棄。

    以後綴計數剪枝：只走「剩下的菜仍湊得出合格組合」的分支。給 vegetables 時，主菜蔬已在
    taken（指定菜）或已選的菜中出現者直接跳過；這個條件無法用後綴計數保證，所以分支可能走不出
    結果；budget（剩餘步數，可跨多次呼叫共用）用完時拋出 SearchExhausted，避免菜數多時窮舉爆炸
    （2026-10-28：10 道菜卡死工作者）。
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

    def walk(start: int, left: int, main: bool, room: int, prefix: tuple, used: frozenset):
        if left == 0:
            yield prefix
            return
        for i in range(start, n):
            if not feasible(i, left, main, room):
                return  # 後綴只會越來越少，之後的起點也不可行
            if budget is not None:
                budget[0] -= 1
                if budget[0] < 0:
                    raise SearchExhausted
            cat = categories[i]
            if cat == SOUP and room == 0:
                continue
            veg = vegetables[i] if vegetables is not None else None
            if veg is not None and veg in used:
                continue
            nxt_main, nxt_room = main and cat != MAIN, room - (cat == SOUP)
            if feasible(i + 1, left - 1, nxt_main, nxt_room):
                yield from walk(i + 1, left - 1, nxt_main, nxt_room, prefix + (i,),
                                used | {veg} if veg is not None else used)

    if feasible(0, needed, need_main, soup_room):
        yield from walk(0, needed, need_main, soup_room, (), frozenset(taken))


class SearchExhausted(Exception):
    """組合搜尋超過步數上限：停止搜尋，已產生的組合照用。"""


# 一次查詢的組合搜尋最多嘗試的步數（所有指定菜做法共用；菜數多、主菜蔬重複多時避免窮舉）
MAX_SEARCH_STEPS = 50_000


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
        db, household_id, draft.meal_date, specified_dish_ids=[d.standard_dish_id for d in found.values()],
        meal_type=draft.meal_type)

    if ambiguous:
        return RetrievalResult(UNRESOLVED, reason="SPECIFIED_DISH_AMBIGUOUS",
                               details={"ambiguous": ambiguous}, previous_day=context)
    if missing:
        return RetrievalResult(NO_FEASIBLE, reason="SPECIFIED_DISH_NOT_IN_DATABASE",
                               details={"specified_dishes": missing}, previous_day=context)

    available = synonyms.confirmed_equivalents(draft.available_ingredient_ids)
    taste = draft.taste_preferences
    has_staple = staples.resolve(draft.staple) is not None

    def rank(recipe: dict):
        # 決策 #22：已有食材重疊數 → 重疊比例 → 口味 → 家庭＋餐食日期決定的輪替順序（不再固定照 recipe_id）
        overlap = len(available.intersection(recipe["food_ids"]))
        share = Fraction(overlap, len(recipe["food_ids"])) if recipe["food_ids"] else Fraction(0)
        taste_hit = int(any(tag in taste for tag in recipe["tags"])) if taste else 0
        return (-overlap, -share, -taste_hit, tie_break(household_id, draft.meal_date, recipe["recipe_id"]))

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
    candidates = list(selected) + [_summary(r, d, False) for options, d in pool for r in options]
    pool.sort(key=lambda item: rank(item[0][0]))

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

    vegetable_of = {r["recipe_id"]: r["main_vegetable"] for d in dishes for r in d.recipes}

    categories = [options[0]["category"] for options, _ in pool]
    pool_vegetables = [vegetable_of.get(options[0]["recipe_id"]) for options, _ in pool]

    budget = [MAX_SEARCH_STEPS]

    def combinations():
        for fixed in itertools.product(*specified):
            # 指定菜的主菜蔬先登記；非指定菜在搜尋中就避開重複，不必先列出再丟棄
            taken = frozenset(v for d in fixed if (v := vegetable_of.get(d["recipe_id"])) is not None)
            for picked in _structured_picks(categories, needed, need_main, soup_room,
                                            vegetables=pool_vegetables, taken=taken, budget=budget):
                menu = [*fixed, *(_summary(pool[i][0][0], pool[i][1], False) for i in picked)]
                if _distinct_vegetables(menu, vegetable_of):
                    yield menu

    alternatives = []
    try:
        for menu in combinations():
            alternatives.append(menu)
            if len(alternatives) == MAX_COMBINATIONS:
                break
    except SearchExhausted:
        pass  # 已找到的組合照用；一組都沒有則轉 AI
    if not alternatives:
        return RetrievalResult(NO_FEASIBLE, reason="DUPLICATE_MAIN_VEGETABLE",
                               details={"dish_count": draft.dish_count}, previous_day=context)
    return RetrievalResult(DATABASE_MENU, selected=alternatives[0], candidates=candidates, previous_day=context,
                           alternatives=alternatives)
