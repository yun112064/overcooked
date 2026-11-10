"""R14 飲食小幫手的食譜建議（合成資料；API 用記憶體 SQLite，不連正式資料庫、不連網）。"""
import os
import unittest
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://placeholder:unused@127.0.0.1:9/not_used")

from app.services import assistant_recipes as svc  # noqa: E402

CARROT, EGG, SPINACH, RADISH, TOMATO, KETCHUP, SALT, PIDAN, PORK = (
    "F-CARROT", "F-EGG", "F-SPINACH", "F-RADISH", "F-TOMATO", "F-KETCHUP", "F-SALT", "F-PIDAN", "F-PORK")
ALIASES = {
    CARROT: ("胡蘿蔔", ["紅蘿蔔"]),
    EGG: ("雞蛋", ["全蛋"]),
    SPINACH: ("菠菜", []),
    RADISH: ("蘿蔔", []),
    TOMATO: ("番茄", ["蕃茄"]),
    KETCHUP: ("番茄醬", []),
    SALT: ("食鹽", ["鹽"]),
    PIDAN: ("皮蛋", []),
    PORK: ("豬絞肉", []),
}
VOCAB = svc.build_vocabulary(ALIASES)

EYES = ("紅蘿蔔富含β-胡蘿蔔素，可在人體轉化為維生素A [1]。深綠色蔬菜如菠菜含有葉黃素 [2]。"
        "皮蛋則不建議多吃 [3]。\n\n以上內容整理自參考文章，僅供一般飲食參考，不能取代醫師或營養師的個別建議。")


def recipe(rid, name, lines, dish=None):
    r = svc._Recipe(rid, "rel-1", dish or f"D-{name}", name, "主菜", "台式", "合成來源", f"https://example.invalid/{rid}")
    r.lines = [svc._Line(food, None if grams is None else Decimal(grams), Decimal(i))
               for i, (food, grams) in enumerate(lines, start=1)]
    return r


RECIPES = [
    recipe("R1", "紅蘿蔔炒蛋", [(CARROT, 150), (EGG, 100), (SALT, 2)]),
    recipe("R2", "紅蘿蔔炒蛋", [(CARROT, 120), (EGG, 120)], dish="D-紅蘿蔔炒蛋"),  # 同一道菜的另一來源
    recipe("R3", "菠菜炒蛋", [(SPINACH, 200), (EGG, 100)]),
    recipe("R4", "肉燥", [(PORK, 480), (CARROT, 10)]),  # 紅蘿蔔只占 2%，不算主要食材
    recipe("R5", "皮蛋豆腐", [(PIDAN, 100)]),
    recipe("R6", "番茄炒蛋", [(TOMATO, 200), (EGG, 100)]),
]


class MentionTests(unittest.TestCase):
    def terms(self, answer):
        return [m.term for m in svc.find_mentions(answer, VOCAB)]

    def test_alias_nutrient_name_and_negative_clause(self):
        mentions = svc.find_mentions(EYES, VOCAB)
        # 紅蘿蔔（胡蘿蔔的別名）與菠菜；「β-胡蘿蔔素」是營養素；皮蛋只出現在勸少吃的句子；結尾提醒不算
        self.assertEqual([m.term for m in mentions], ["紅蘿蔔", "菠菜"])
        self.assertEqual(mentions[0].food_ids, frozenset({CARROT}))
        self.assertEqual(mentions[0].reason, "紅蘿蔔富含β-胡蘿蔔素，可在人體轉化為維生素A。")

    def test_longest_match_and_seasonings(self):
        # 「番茄醬」整個是調味料，不拆出番茄；「紅蘿蔔」不拆出「蘿蔔」；食鹽不推薦
        self.assertEqual(self.terms("番茄醬熱量不低，食鹽也是。紅蘿蔔很好。"), ["紅蘿蔔"])
        self.assertEqual(self.terms("番茄紅素是抗氧化物。"), [])
        self.assertEqual(self.terms("蕃茄和番茄都可以。"), ["蕃茄"])  # 同一食材只列一次

    def test_negative_only_in_its_clause(self):
        # 同一句裡，正面子句提到的仍算；只在負面子句出現的不算
        self.assertEqual(self.terms("菠菜含鐵，但皮蛋不宜過量。"), ["菠菜"])
        self.assertEqual(self.terms("紅蘿蔔有好處，但也不要過量食用紅蘿蔔。"), ["紅蘿蔔"])

    def test_category_notice_line_is_ignored(self):
        answer = "目前沒有「皮蛋」本身的資料，以下是「蛋類」這一類的一般資訊：\n雞蛋富含蛋白質 [1]。"
        self.assertEqual(self.terms(answer), ["雞蛋"])


class ChooseTests(unittest.TestCase):
    def test_one_dish_per_food_main_ingredient_only(self):
        picks = svc.choose(svc.find_mentions(EYES, VOCAB), RECIPES, set())
        self.assertEqual([(r.name, [m.term for m in ms]) for r, ms in picks],
                         [("紅蘿蔔炒蛋", ["紅蘿蔔"]), ("菠菜炒蛋", ["菠菜"])])

    def test_excluded_foods_are_never_suggested(self):
        picks = svc.choose(svc.find_mentions(EYES, VOCAB), RECIPES, {EGG})
        # 兩道紅蘿蔔炒蛋與菠菜炒蛋都含蛋；肉燥的紅蘿蔔不是主要食材 → 沒有菜
        self.assertEqual(picks, [])

    def test_prefers_dish_covering_more_mentioned_foods_and_caps_at_three(self):
        many = [recipe(f"X{i}", f"紅蘿蔔料理{i}", [(CARROT, 100)]) for i in range(5)]
        mentions = svc.find_mentions("紅蘿蔔、菠菜、雞蛋、番茄都很營養。", VOCAB)
        picks = svc.choose(mentions, RECIPES + many, set())
        self.assertEqual(len(picks), 3)
        # 紅蘿蔔炒蛋同時用到紅蘿蔔與雞蛋，比只有紅蘿蔔的菜優先；同一道菜不重複
        self.assertEqual(picks[0][0].name, "紅蘿蔔炒蛋")
        self.assertEqual([m.term for m in picks[0][1]], ["紅蘿蔔", "雞蛋"])
        self.assertEqual(len({r.standard_dish_id for r, _ in picks}), 3)

    def test_unknown_weights_count_only_first_lines(self):
        r = recipe("U1", "什錦湯", [(PORK, None), (EGG, None), (TOMATO, None), (CARROT, None)])
        self.assertEqual(svc._main_food_ids(r), {PORK, EGG, TOMATO})


class HouseholdExclusionTests(unittest.TestCase):
    def test_union_of_members_with_synonym_expansion(self):
        from app.services import meal_request
        members = [SimpleNamespace(allergen_ingredient_ids=["A"], avoided_ingredient_ids=[]),
                   SimpleNamespace(allergen_ingredient_ids=[], avoided_ingredient_ids=["B"])]
        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(meal_request.synonyms, "restriction_expansion",
                       lambda ids: {"A": [{"food_id": "A2", "relation": "synonym", "status": "approved"}]})
            mp.setattr(meal_request.allergen_groups, "default_path",
                       lambda: SimpleNamespace(is_file=lambda: False))
            self.assertEqual(meal_request.household_excluded_food_ids(members), {"A", "A2", "B"})


# ---- API：記憶體 SQLite 只建食譜相關資料表 ----

def _sqlite_factory():
    from app.db.base import Base
    from app.models import Recipe, RecipeIngredient
    from app.models.recipe import IngredientAlias, RecipeCover, RecipeDataRelease
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[t.__table__ for t in (
        RecipeDataRelease, IngredientAlias, Recipe, RecipeIngredient, RecipeCover)])
    factory = sessionmaker(engine)
    with factory() as db, db.begin():
        db.add(RecipeDataRelease(release_id="rel-1", dataset_version="v-test", source_sha256="0" * 64,
                                 schema_version="t", source_kind="xlsx_import", row_counts={}, active=True))
        for food_id, (name, aliases) in ALIASES.items():
            db.add(IngredientAlias(food_id=food_id, recipe_release_id="rel-1", canonical_name=name, aliases=aliases))
        db.flush()
        for r in RECIPES + [recipe("R9", "紅蘿蔔濃湯", [(CARROT, 300)])]:
            db.add(Recipe(recipe_id=r.recipe_id, recipe_release_id="rel-1", raw_id=f"RAW-{r.recipe_id}",
                          standard_dish_id=r.standard_dish_id, recipe_name=r.name, normalized_name=r.name,
                          servings_raw="2人份", servings_status="parsed", category="主菜", meal_type="晚餐",
                          cuisine="台式", source_name="合成來源", source_url=r.source_url,
                          is_eligible=r.recipe_id != "R9"))  # R9 未通過審核
            db.flush()
            for line in r.lines:
                db.add(RecipeIngredient(recipe_ingredient_id=f"{r.recipe_id}-{line.sequence}", recipe_release_id="rel-1",
                                        recipe_id=r.recipe_id, sequence=line.sequence, raw_text=line.food_id,
                                        canonical_name=ALIASES[line.food_id][0], food_id=line.food_id,
                                        weight_g=line.weight, quantity_status="parsed", unit_status="parsed",
                                        weight_status="parsed"))
        db.add(RecipeCover(recipe_id="R3", recipe_release_id="rel-1", filename="R3.jpg", image_url="x",
                           sha256="1" * 64, media_type="image/jpeg", image_bytes=b"jpg"))
    return factory


@pytest.fixture
def client():
    from app.api import assistant as api
    from app.api.deps import current_auth, verify_origin
    from app.api.errors import register_error_handlers
    from app.db.session import get_db
    factory = _sqlite_factory()

    def db():
        with factory() as session:
            yield session

    app = FastAPI()
    register_error_handlers(app)
    app.include_router(api.router)
    app.dependency_overrides[current_auth] = lambda: SimpleNamespace(
        user_id="user-1", session=SimpleNamespace(user_id="user-1", household_id=None))
    app.dependency_overrides[verify_origin] = lambda: None
    app.dependency_overrides[get_db] = db
    return TestClient(app)


def test_api_suggests_eligible_recipes_with_cover(client):
    res = client.post("/assistant/recipes", json={"answer": EYES, "type": "answer"})
    assert res.status_code == 200, res.text
    data = res.json()
    assert data["household_filtered"] is False
    assert [(r["dish_name"], r["foods"]) for r in data["recipes"]] == [("紅蘿蔔炒蛋", ["紅蘿蔔"]), ("菠菜炒蛋", ["菠菜"])]
    first, second = data["recipes"]
    assert first["recipe_id"] == "R1" and first["cover_url"] is None
    assert first["reason"].startswith("紅蘿蔔富含β-胡蘿蔔素")
    assert second["cover_url"] == "/recipe-covers/rel-1/R3"
    assert second["source_url"] == "https://example.invalid/R3"


@pytest.mark.parametrize("kind", ["declined_medical", "emergency", "out_of_scope", "greeting"])
def test_api_never_suggests_for_non_answers(client, kind):
    res = client.post("/assistant/recipes", json={"answer": EYES, "type": kind})
    assert res.status_code == 200
    assert res.json()["recipes"] == []


def test_api_no_foods_no_recipes(client):
    res = client.post("/assistant/recipes", json={"answer": "多喝水、規律作息 [1]。", "type": "answer"})
    assert res.json()["recipes"] == []


# ---- PostgreSQL（需要隔離的 TEST_DATABASE_URL；未設定時略過）：已選家庭時排除成員過敏食材 ----
from db_support import make_client, migrate_fresh, requires_db, seed_foods, session_factory, truncate_all  # noqa: E402


@requires_db
class HouseholdRecipeApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        from test_t08_meal_requests import PASSWORD, member_body
        from test_t11_recipe_retrieval import CARROT as REAL_CARROT, seed_recipes
        truncate_all()
        seed_foods()
        seed_recipes(session_factory())
        self.client = make_client()
        self.assertEqual(self.client.post("/auth/register", json={"email": "recipes@example.test",
                                                                  "password": PASSWORD}).status_code, 201)
        self.answer = "紅蘿蔔和番茄都含有豐富的營養 [1]。"
        self.before = self.client.post("/assistant/recipes", json={"answer": self.answer, "type": "answer"}).json()
        self.assertEqual(self.client.post("/household", json={"name": "測試家庭"}).status_code, 201)
        self.client.post("/members", json=member_body("成員A", allergen_ingredient_ids=[REAL_CARROT]))

    def test_member_allergy_removes_dishes_with_that_food(self):
        # 沒選家庭：紅蘿蔔炒蛋與番茄炒蛋都會推薦
        self.assertFalse(self.before["household_filtered"])
        self.assertEqual({r["dish_name"] for r in self.before["recipes"]}, {"紅蘿蔔炒蛋", "番茄炒蛋"})
        res = self.client.post("/assistant/recipes", json={"answer": self.answer, "type": "answer"})
        self.assertEqual(res.status_code, 200, res.text)
        data = res.json()
        self.assertTrue(data["household_filtered"])
        self.assertEqual([r["dish_name"] for r in data["recipes"]], ["番茄炒蛋"])


def test_dish_named_after_the_food_is_preferred_even_with_lower_share():
    stew = recipe("S1", "什錦燉肉", [(CARROT, 300), (PORK, 100)])           # 紅蘿蔔占 75%
    stir = recipe("S2", "紅蘿蔔炒蛋", [(CARROT, 100), (EGG, 150)])          # 紅蘿蔔占 40%，但菜名就有
    picks = svc.choose(svc.find_mentions("胡蘿蔔富含β-胡蘿蔔素。", VOCAB), [stew, stir], set())
    # 回答寫「胡蘿蔔」，菜名寫「紅蘿蔔」（別名）也算
    assert [r.name for r, _ in picks] == ["紅蘿蔔炒蛋"]


def test_reason_drops_list_bullet():
    [m] = svc.find_mentions("・菠菜含有葉黃素 [2]。\n", VOCAB)
    assert m.reason == "菠菜含有葉黃素。"
