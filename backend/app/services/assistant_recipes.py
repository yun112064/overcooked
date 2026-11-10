"""R14 飲食小幫手的食譜建議：依回答裡提到的食材，從食譜庫找 1–3 道以它為主要食材的菜。

- 只看回答（已只依參考資料生成）提到的食材；只出現在「避免、不宜、過量…」這類句子裡的食材不算。
- 食材名稱用 T01 食材別名表比對，最長詞優先（「紅蘿蔔」→胡蘿蔔，不會再拆出「蘿蔔」；「番茄醬」不算番茄）。
- 調味、粉類、油類等（與食譜庫選菜相同的規則）與單字名稱不比對。
- 食材至少占整道菜生食材重量的 MIN_SHARE 才算主要食材；重量未知時只認前三項食材。
- 菜一律取自啟用中發布的合格食譜，不生成菜名，也不宣稱療效；家庭成員過敏與禁吃的食材（含同義與過敏原群組）一律排除。
- 醫療婉拒、緊急、超出範圍等回覆不推薦。
"""
import re
from dataclasses import dataclass, field
from decimal import Decimal
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Recipe, RecipeIngredient
from app.models.recipe import IngredientAlias, RecipeCover
from app.services.recipe_catalog import NOT_MAIN_SUFFIXES, NOT_MAIN_WORDS
from app.services.recipe_retrieval import active_release_filter

ANSWER_TYPES = frozenset({"answer", "category_answer"})
MAX_RECIPES = 3
MIN_SHARE = Decimal("0.08")
MIN_TERM_LENGTH = 2
UNKNOWN_WEIGHT_TOP_LINES = 3
REASON_LENGTH = 70
# 調味料與香料：即使出現在回答裡也不拿來推薦菜
SEASONINGS = frozenset({
    "食鹽", "砂糖", "冰糖", "細糖", "黑糖", "紅冰糖", "糖粉", "米酒", "料理酒", "紹興酒", "啤酒", "胡椒", "八角",
    "花椒粒", "月桂葉", "迷迭香", "乾燥迷迭香", "巴西里", "蒜末", "蒜酥", "紅蔥酥", "油蔥酥", "味醂", "烹大師",
    "鮮味炒手", "咖哩塊", "胡椒鹽", "味噌",
})
# 子句含這些詞就視為負面語境（勸少吃、提醒風險）
NEGATIVE_WORDS = ("避免", "不宜", "少吃", "不要", "不建議", "不能", "不可", "不適合", "過量", "過多", "禁忌",
                  "忌口", "致癌", "小心", "注意", "勿", "別吃")
NUTRIENT_FOLLOWERS = ("素", "紅素", "鹼", "苷")
CITATION = re.compile(r"[ \t]*\[\d{1,2}\]")
SENTENCE_SPLIT = re.compile(r"(?<=[。！？；\n])")
CLAUSE_SPLIT = re.compile(r"[，,。！？；;\n]")
# 回答開頭的類別／別名說明與結尾的固定提醒不算內容
NOTICE_LINE = re.compile(r"^(目前沒有「[^」]+」本身的資料|「[^」]+」和「[^」]+」是同一種食物)[^\n]*\n")
TAIL = re.compile(r"\n*以上內容整理自參考文章[\s\S]*$")


@dataclass
class Mention:
    term: str            # 回答裡的寫法，例如「紅蘿蔔」
    food_ids: frozenset[str]
    reason: str          # 回答中提到它的那一句（去掉引用標號）
    names: tuple[str, ...] = ()  # 這個食材的所有名稱（標準名稱與別名），用來優先挑菜名就有它的菜


@dataclass
class _Line:
    food_id: str
    weight: Decimal | None
    sequence: Decimal


@dataclass
class _Recipe:
    recipe_id: str
    release_id: str
    standard_dish_id: str
    name: str
    category: str
    cuisine: str
    source_name: str
    source_url: str
    lines: list[_Line] = field(default_factory=list)


def _is_seasoning(name: str) -> bool:
    return name in SEASONINGS or name.endswith(NOT_MAIN_SUFFIXES) or any(w in name for w in NOT_MAIN_WORDS)


def build_vocabulary(aliases: dict[str, tuple[str, list[str]]]) -> dict[str, frozenset[str]]:
    """{food_id: (標準名稱, 別名)} → {名稱或別名: FOOD 編號}。

    調味料也收、但對應空集合：比對時仍佔住整個詞（「番茄醬」不會被拆成「番茄」），只是不推薦。單字不收。
    """
    vocab: dict[str, set[str]] = {}
    for food_id, (canonical, names) in aliases.items():
        seasoning = _is_seasoning(canonical)
        for name in (canonical, *names):
            name = (name or "").strip()
            if len(name) < MIN_TERM_LENGTH:
                continue
            ids = vocab.setdefault(name, set())
            if not seasoning and not _is_seasoning(name):
                ids.add(food_id)
    return {k: frozenset(v) for k, v in vocab.items()}


def _content(answer: str) -> str:
    text = NOTICE_LINE.sub("", answer.lstrip(), count=1)
    return CITATION.sub("", TAIL.sub("", text))


def find_mentions(answer: str, vocab: dict[str, frozenset[str]]) -> list[Mention]:
    """依出現順序列出回答中正面提到的食材（同一 FOOD 編號只列一次）。"""
    if not vocab:
        return []
    longest = max(map(len, vocab))
    mentions: dict[str, Mention] = {}
    seen_ids: set[str] = set()
    for sentence in SENTENCE_SPLIT.split(_content(answer)):
        for clause in CLAUSE_SPLIT.split(sentence):
            if not clause.strip() or any(w in clause for w in NEGATIVE_WORDS):
                continue
            i = 0
            while i < len(clause):
                term = next((clause[i:i + n] for n in range(min(longest, len(clause) - i), MIN_TERM_LENGTH - 1, -1)
                             if clause[i:i + n] in vocab), None)
                if term is None:
                    i += 1
                    continue
                ids = vocab[term]
                rest = clause[i + len(term):]
                if rest.startswith(NUTRIENT_FOLLOWERS):  # 「β-胡蘿蔔素」「番茄紅素」是營養素，不是食材
                    ids = frozenset()
                if ids and term not in mentions and not ids & seen_ids:
                    reason = sentence.strip().lstrip("・•-*·● ").strip()  # 去掉條列符號
                    if len(reason) > REASON_LENGTH:
                        reason = reason[:REASON_LENGTH - 1] + "…"
                    names = tuple(sorted({n for n, n_ids in vocab.items() if n_ids & ids}, key=len, reverse=True))
                    mentions[term] = Mention(term, ids, reason, names)
                    seen_ids |= ids
                i += len(term)
    return list(mentions.values())


def _main_food_ids(recipe: _Recipe) -> set[str]:
    """以重量占比判斷的主要食材。"""
    weights = [ln.weight for ln in recipe.lines if ln.weight is not None and ln.weight > 0]
    total = sum(weights, Decimal(0))
    ordered = sorted(recipe.lines, key=lambda ln: ln.sequence)
    main = set()
    for index, line in enumerate(ordered):
        if line.weight is not None and total > 0:
            if line.weight / total >= MIN_SHARE:
                main.add(line.food_id)
        elif index < UNKNOWN_WEIGHT_TOP_LINES:
            main.add(line.food_id)
    return main


def _share(recipe: _Recipe, food_ids: frozenset[str]) -> Decimal:
    total = sum((ln.weight for ln in recipe.lines if ln.weight), Decimal(0))
    if total <= 0:
        return Decimal(0)
    return sum((ln.weight for ln in recipe.lines if ln.weight and ln.food_id in food_ids), Decimal(0)) / total


def choose(mentions: list[Mention], recipes: list[_Recipe], excluded: set[str]) -> list[tuple[_Recipe, list[Mention]]]:
    """每個食材輪流挑一道最合適、還沒選過的菜，最多 MAX_RECIPES 道。"""
    usable = [r for r in recipes if not {ln.food_id for ln in r.lines} & excluded]
    main = {r.recipe_id: _main_food_ids(r) for r in usable}
    picks: list[tuple[_Recipe, list[Mention]]] = []
    dishes: set[str] = set()
    for mention in mentions:
        if len(picks) >= MAX_RECIPES:
            break
        candidates = [r for r in usable if r.standard_dish_id not in dishes and main[r.recipe_id] & mention.food_ids]
        if not candidates:
            continue

        def rank(r: _Recipe):
            # 菜名就有這個食材（紅蘿蔔炒蛋）優先，其次是用到較多回答提到的食材、這個食材占比較高
            named = any(n in r.name for n in (mention.names or (mention.term,)))
            covered = [m for m in mentions if main[r.recipe_id] & m.food_ids]
            return (not named, -len(covered), -_share(r, mention.food_ids), r.name)

        best = min(candidates, key=rank)
        dishes.add(best.standard_dish_id)
        # 選中它的食材排第一（理由取這個食材的句子），其餘回答也提到的食材跟在後面
        picks.append((best, [mention, *(m for m in mentions if m is not mention and main[best.recipe_id] & m.food_ids)]))
    return picks


def _load(db: Session) -> tuple[dict[str, tuple[str, list[str]]], list[_Recipe]]:
    active = active_release_filter(db)
    recipes: dict[str, _Recipe] = {}
    for r in db.execute(
        select(Recipe.recipe_id, Recipe.recipe_release_id, Recipe.standard_dish_id, Recipe.recipe_name,
               Recipe.category, Recipe.cuisine, Recipe.source_name, Recipe.source_url)
        .where(Recipe.is_eligible.is_(True), active).order_by(Recipe.recipe_id)
    ):
        recipes[r.recipe_id] = _Recipe(r.recipe_id, r.recipe_release_id, r.standard_dish_id, r.recipe_name,
                                       r.category, r.cuisine, r.source_name, r.source_url)
    names: dict[str, str] = {}
    for line in db.execute(
        select(RecipeIngredient.recipe_id, RecipeIngredient.food_id, RecipeIngredient.canonical_name,
               RecipeIngredient.weight_g, RecipeIngredient.sequence)
        .join(Recipe, Recipe.recipe_id == RecipeIngredient.recipe_id)
        .where(Recipe.is_eligible.is_(True), active)
    ):
        recipes[line.recipe_id].lines.append(_Line(line.food_id, line.weight_g, line.sequence))
        names.setdefault(line.food_id, line.canonical_name)
    aliases: dict[str, tuple[str, list[str]]] = {food_id: (name, []) for food_id, name in names.items()}
    if names:
        for row in db.execute(select(IngredientAlias.food_id, IngredientAlias.canonical_name, IngredientAlias.aliases)
                              .where(IngredientAlias.food_id.in_(list(names)))):
            extra = [a for a in (row.aliases or []) if isinstance(a, str)]
            aliases[row.food_id][1].extend([row.canonical_name, *extra])
    return aliases, list(recipes.values())


def suggest(db: Session, answer: str, excluded_food_ids: set[str]) -> list[dict]:
    aliases, recipes = _load(db)
    mentions = find_mentions(answer, build_vocabulary(aliases))
    picks = choose(mentions, recipes, excluded_food_ids)
    covers = set()
    if picks:
        covers = {(c.recipe_id, c.recipe_release_id) for c in db.execute(
            select(RecipeCover.recipe_id, RecipeCover.recipe_release_id)
            .where(RecipeCover.recipe_id.in_([r.recipe_id for r, _ in picks])))}
    return [{
        "dish_name": r.name,
        "recipe_id": r.recipe_id,
        "category": r.category,
        "cuisine": r.cuisine,
        "source_name": r.source_name,
        "source_url": r.source_url,
        "foods": [m.term for m in matched],
        "reason": matched[0].reason,
        "cover_url": (f"/recipe-covers/{quote(r.release_id, safe='')}/{quote(r.recipe_id, safe='')}"
                      if (r.recipe_id, r.release_id) in covers else None),
    } for r, matched in picks]
