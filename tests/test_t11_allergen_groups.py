"""T11 過敏原群組（使用者 2026-11-02 核准）：對雞蛋過敏也排除皮蛋、蛋豆腐等；不吃不套用。

DEMO 只把群組用在飲食小幫手的食譜建議（meal_request.household_excluded_food_ids），
不放寬菜單生成的 restrictions()，因此這裡只驗證設定檔本身，不測問卷限制。
"""
import json
import tempfile
import unittest
from pathlib import Path


EGG, CENTURY_EGG, EGG_TOFU, MAYO = "FOOD001566", "FOOD000309", "FOOD000944", "FOOD000671"


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
