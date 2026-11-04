"""T11 過敏原群組與問卷接線，使用合成群組。"""
import json
import tempfile
import unittest
from pathlib import Path

import pytest

from app.services.allergen_groups import default_path
from db_support import make_client, migrate_fresh, requires_db, seed_foods, truncate_all
from test_t08_meal_requests import PASSWORD, full_draft, member_body

EGG, CENTURY_EGG, EGG_TOFU, MAYO = "FOOD001566", "FOOD000309", "FOOD000944", "FOOD000671"


@pytest.fixture
def synthetic_allergen_policy(tmp_path, monkeypatch):
    from app.services import allergen_groups

    def group(group_id, name, members):
        foods = [{"food_id": food_id, "name": label, "basis": "typical_formula"}
                 for food_id, label in members]
        return {"allergen_id": group_id, "allergen_name": name, "triggers": foods[:1], "members": foods,
                "official_basis": "Synthetic source field", "source_url": "https://example.invalid/allergens",
                "legal_status": "強制標示"}

    path = tmp_path / "groups.json"
    path.write_text(json.dumps({
        "schema_version": allergen_groups.SCHEMA_VERSION, "status": "approved",
        "approved_by": "Synthetic reviewer", "version": "test-only",
        "groups": [group("egg", "蛋", [(EGG, "雞蛋"), (CENTURY_EGG, "皮蛋"), (EGG_TOFU, "蛋豆腐")]),
                   group("soy", "大豆", [("TEST-SOY", "大豆"), ("FOOD001541", "醬油")])],
    }, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(allergen_groups, "default_path", lambda: path)


@pytest.mark.usefixtures("synthetic_allergen_policy")
class AllergenGroupFileTests(unittest.TestCase):
    def test_project_file_is_valid_and_egg_covers_processed_egg_foods(self):
        from app.services import allergen_groups
        extra = {e["food_id"]: e for e in allergen_groups.expansion([EGG])[EGG]}
        self.assertIn(CENTURY_EGG, extra)
        self.assertIn(EGG_TOFU, extra)
        self.assertEqual(extra[CENTURY_EGG]["relation"], "allergen_group")
        self.assertEqual(extra[CENTURY_EGG]["group_name"], "蛋")

    def test_only_trigger_foods_open_a_group(self):
        from app.services import allergen_groups
        # 醬油同時屬於大豆、麩質群組，但不是觸發食材：選它不會排除整組麵粉製品
        soy_sauce = next(m for g in allergen_groups._load(str(allergen_groups.default_path()))
                         if g["allergen_name"] == "大豆" for m, n in g["members"].items() if n == "醬油")
        self.assertEqual(allergen_groups.expansion([soy_sauce])[soy_sauce], [])

    def test_invalid_file_fails_closed(self):
        from app.services import allergen_groups
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "groups.json"
            path.write_text(json.dumps({"schema_version": "t11-allergen-groups-v1", "status": "draft",
                                        "groups": []}), encoding="utf-8")
            with self.assertRaises(allergen_groups.AllergenGroupsInvalid):
                allergen_groups.expansion([EGG], path)
            with self.assertRaises(allergen_groups.AllergenGroupsInvalid):
                allergen_groups.expansion([EGG], Path(folder) / "missing.json")


@requires_db
@pytest.mark.usefixtures("synthetic_allergen_policy")
class AllergenGroupRestrictionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        truncate_all()
        seed_foods()
        self.client = make_client()
        self.assertEqual(self.client.post("/auth/register", json={"email": "g@example.test",
                                                                  "password": PASSWORD}).status_code, 201)
        self.assertEqual(self.client.post("/household", json={"name": "測試家庭"}).status_code, 201)

    def restrictions(self, **member):
        m = self.client.post("/members", json=member_body("成員", **member)).json()
        res = self.client.post("/meal-requests", json=full_draft([{"member_id": m["id"]}]))
        self.assertEqual(res.status_code, 201, res.text)
        return res.json()["restrictions"]

    def test_egg_allergy_excludes_the_whole_egg_group(self):
        r = self.restrictions(allergen_ingredient_ids=[EGG])
        self.assertTrue({EGG, CENTURY_EGG, EGG_TOFU} <= set(r["excluded_food_ids"]))
        row = next(x for x in r["expanded_ingredients"] if x["ingredient_id"] == CENTURY_EGG)
        self.assertEqual((row["relation"], row["kinds"], row["via"]), ("allergen_group", ["allergen"], [EGG]))

    def test_avoided_egg_is_not_expanded_to_the_group(self):
        r = self.restrictions(avoided_ingredient_ids=[EGG])
        self.assertNotIn(CENTURY_EGG, r["excluded_food_ids"])


@unittest.skipUnless(default_path().is_file(), "Local allergen source file is not distributed with code")
class OfficialBasisTests(unittest.TestCase):
    """2026-11-02：群組依衛福部食品過敏原標示規定與食藥署建議標示事項。"""

    def setUp(self):
        from app.services import allergen_groups
        self.data = json.loads(allergen_groups.default_path().read_text(encoding="utf-8"))

    def test_every_group_cites_an_official_source(self):
        for group in self.data["groups"]:
            self.assertTrue(group["official_basis"] and group["source_url"].startswith("https://"), group["allergen_name"])
            self.assertIn(group["legal_status"], ("強制標示", "建議標示"))
            for member in group["members"]:
                self.assertIn(member["basis"], ("official_definition", "typical_formula"))

    def test_official_exemptions_are_not_members(self):
        names = {m["name"] for g in self.data["groups"] for m in g["members"]}
        for exempt in ("黃豆油", "啤酒", "黑啤酒"):
            self.assertNotIn(exempt, names)
