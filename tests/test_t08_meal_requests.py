"""T08：當餐問卷、便當與手動食材草稿（AC03、AC09）。成員皆為合成測試資料。"""
import unittest

from db_support import seed_foods, make_client, migrate_fresh, requires_db, truncate_all

PASSWORD = "correct-horse-42"


def member_body(name, **extra):
    body = {
        "display_name": name, "birth_date": "1996-03-01", "equation_sex": "M", "weight_kg": "70",
        "activity_category": "sedentary", "goal": "maintain", "portion": "1",
        "allergen_ingredient_ids": [], "avoided_ingredient_ids": [],
    }
    body.update(extra)
    return body


def full_draft(participants, **extra):
    draft = {
        "meal_date": "2026-08-20", "meal_type": "dinner", "budget": "300", "staple": "白飯",
        "dish_count": 3, "taste_preferences": "清淡", "specified_dishes": [],
        "participants": participants, "available_ingredient_ids": [],
    }
    draft.update(extra)
    return draft


@requires_db
class T08ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        migrate_fresh()

    def setUp(self):
        truncate_all()
        seed_foods()
        self.client = make_client()
        self.login_with_household(self.client, "owner@example.test")
        self.a = self.client.post("/members", json=member_body(
            "成員A", portion="1.3", allergen_ingredient_ids=["FOOD001566"])).json()
        self.b = self.client.post("/members", json=member_body(
            "成員B", birth_date="2018-01-01", weight_kg="28", goal="general", portion="0.7",
            avoided_ingredient_ids=["FOOD000039", "FOOD001566"])).json()

    def login_with_household(self, client, email):
        self.assertEqual(client.post("/auth/register", json={"email": email, "password": PASSWORD}).status_code, 201)
        self.assertEqual(client.post("/household", json={"name": "測試家庭"}).status_code, 201)

    def create(self, draft):
        res = self.client.post("/meal-requests", json=draft)
        self.assertEqual(res.status_code, 201, res.text)
        return res.json()

    def test_partial_draft_saves_and_reports_missing_items(self):
        created = self.create({"meal_type": "lunch"})
        self.assertEqual(created["version"], 1)
        self.assertFalse(created["complete"])
        missing = {i["field"] for i in created["issues"]}
        self.assertTrue({"meal_date", "budget", "staple", "dish_count", "participants"} <= missing)
        self.assertEqual(created["draft"]["meal_type"], "lunch")

    def test_staple_options_come_from_backend_config_including_no_staple(self):
        """決策 #52：主食只能從清單選，清單由後端設定檔提供；沒有營養資料的選項不列入。"""
        from unittest.mock import patch
        from app.services import staples
        config = {**staples.table(), "none_values": ["不需要主食", "無"], "options": {
            "白飯": staples.table()["options"]["白飯"],
            "小米飯": {"aliases": [], "food_id": "FOOD999998", "tfda_name": None}}}
        with patch("app.services.staples.table", lambda: config):
            res = self.client.get("/meal-requests/staple-options")
        self.assertEqual(res.status_code, 200, res.text)
        body = res.json()
        self.assertEqual([o["label"] for o in body["options"]], ["白飯"])
        self.assertEqual(body["options"][0]["aliases"], staples.table()["options"]["白飯"]["aliases"])
        self.assertEqual(body["none_value"], "不需要主食")

    def test_unsupported_staple_is_reported_and_blocks_submit_but_draft_is_kept(self):
        created = self.create(full_draft([{"member_id": self.a["id"]}], staple="披薩"))
        issues = {i["field"]: i for i in created["issues"]}
        self.assertEqual(issues["staple"]["reason"], "STAPLE_NOT_SUPPORTED")
        self.assertIn("披薩", issues["staple"]["message"])
        self.assertFalse(created["complete"])
        self.assertEqual(created["draft"]["staple"], "披薩")
        res = self.client.post(f"/meal-requests/{created['id']}/submit", json={"version": 1},
                               headers={"Idempotency-Key": "key-00000001"})
        self.assertNotEqual(res.status_code, 202, res.text)
        for ok in ("白飯", "米飯", "不需要主食"):
            draft = self.create(full_draft([{"member_id": self.a["id"]}], staple=ok))
            self.assertNotIn("staple", {i["field"] for i in draft["issues"]}, ok)

    def test_update_keeps_versions_and_rejects_stale_edit(self):
        created = self.create({"meal_type": "lunch"})
        draft = full_draft([{"member_id": self.a["id"]}])
        res = self.client.put(f"/meal-requests/{created['id']}", json={"version": 1, "draft": draft})
        self.assertEqual(res.status_code, 200, res.text)
        updated = res.json()
        self.assertEqual(updated["version"], 2)
        self.assertTrue(updated["complete"], updated["issues"])
        stale = self.client.put(f"/meal-requests/{created['id']}", json={"version": 1, "draft": draft})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(stale.json()["error"]["code"], "VERSION_CONFLICT")
        v1 = self.client.get(f"/meal-requests/{created['id']}/versions/1").json()
        self.assertEqual(v1["draft"]["meal_type"], "lunch")
        self.assertIsNone(v1["draft"]["budget"])
        # 返回重讀：內容與最新版本相同
        reread = self.client.get(f"/meal-requests/{created['id']}").json()
        self.assertEqual(reread["draft"], updated["draft"])

    def test_invalid_input_is_rejected_without_losing_saved_draft(self):
        created = self.create(full_draft([{"member_id": self.a["id"]}]))
        bad = full_draft([{"member_id": self.a["id"]}], budget="-5")
        res = self.client.put(f"/meal-requests/{created['id']}", json={"version": 1, "draft": bad})
        self.assertEqual(res.status_code, 422)
        reread = self.client.get(f"/meal-requests/{created['id']}").json()
        self.assertEqual((reread["version"], reread["draft"]["budget"]), (1, "300"))

    def test_dining_and_bento_are_separate_items_and_bento_defaults_to_lunch(self):
        created = self.create(full_draft([
            {"member_id": self.a["id"], "dining": True, "bento": True},
            {"member_id": self.b["id"], "dining": True, "bento": True, "bento_meal": "breakfast"},
        ]))
        self.assertEqual(created["draft"]["participants"][0]["bento_meal"], "lunch")
        items = {(i["member_id"], i["kind"]): i["meal"] for i in created["items"]}
        self.assertEqual(items, {
            (self.a["id"], "dining"): "dinner", (self.a["id"], "bento"): "lunch",
            (self.b["id"], "dining"): "dinner", (self.b["id"], "bento"): "breakfast",
        })

    def test_target_preview_uses_meal_ratios_and_each_item_once(self):
        created = self.create(full_draft([
            {"member_id": self.a["id"], "dining": True, "bento": True},
            {"member_id": self.b["id"], "dining": True, "bento": False},
        ], meal_date="2026-08-20"))
        preview = created["targets_preview"]
        self.assertEqual(preview["status"], "available", preview)
        self.assertEqual(preview["rule_version"], "hpa-dri8-mvp-0.3.0")
        ratios = {(i["kind"], i["meal"]): i["meal_ratio"] for i in preview["items"]}
        self.assertEqual(ratios, {("dining", "dinner"): "0.4", ("bento", "lunch"): "0.3"})
        self.assertEqual(len(preview["items"]), 3)
        self.assertEqual(preview["total_n"], "3.3")
        # 成員 A：77 g/day 蛋白質；晚餐 0.4、午餐便當 0.3，各乘 1.3；成員 B：28×1.1×0.4×0.7
        expected_protein = 77 * 0.4 * 1.3 + 77 * 0.3 * 1.3 + 28 * 1.1 * 0.4 * 0.7
        self.assertAlmostEqual(float(preview["target"]["protein_g"]), expected_protein, places=9)
        self.assertFalse(preview["approved_for_adoption"])

    def test_breakfast_ratio_is_thirty_percent(self):
        created = self.create(full_draft([{"member_id": self.a["id"]}], meal_type="breakfast"))
        self.assertEqual(created["targets_preview"]["items"][0]["meal_ratio"], "0.3")

    def test_only_bento_member_counts_and_non_participants_are_excluded(self):
        created = self.create(full_draft([
            {"member_id": self.a["id"], "dining": False, "bento": True},
            {"member_id": self.b["id"], "dining": False, "bento": False},
        ]))
        self.assertEqual([(i["kind"], i["meal"]) for i in created["items"]], [("bento", "lunch")])
        # 未參與的成員 B 的限制不納入
        self.assertEqual([r["ingredient_id"] for r in created["restrictions"]["ingredients"]], ["FOOD001566"])

    def test_restrictions_are_union_of_participants(self):
        created = self.create(full_draft(
            [{"member_id": self.a["id"]}, {"member_id": self.b["id"]}],
            available_ingredient_ids=["FOOD001566", "FOOD001016", "FOOD001016"]))
        union = {r["ingredient_id"]: r for r in created["restrictions"]["ingredients"]}
        self.assertEqual(set(union), {"FOOD001566", "FOOD000039"})
        self.assertEqual(sorted(union["FOOD001566"]["kinds"]), ["allergen", "avoided"])
        self.assertEqual(sorted(union["FOOD001566"]["member_names"]), ["成員A", "成員B"])
        self.assertEqual(created["restrictions"]["available_but_restricted"], ["FOOD001566"])
        self.assertEqual(created["draft"]["available_ingredient_ids"], ["FOOD001566", "FOOD001016"])

    def test_synonym_foods_are_also_restricted(self):
        kid = self.client.post("/members", json=member_body(
            "成員C", birth_date="2016-01-01", weight_kg="25", goal="general", portion="0.7",
            allergen_ingredient_ids=["FOOD001016"])).json()  # 番茄
        created = self.create(full_draft([{"member_id": kid["id"]}], available_ingredient_ids=["FOOD001444"]))
        restrictions = created["restrictions"]
        self.assertEqual([r["ingredient_id"] for r in restrictions["ingredients"]], ["FOOD001016"])
        expanded = {r["ingredient_id"]: r for r in restrictions["expanded_ingredients"]}
        self.assertEqual(expanded["FOOD001444"]["name"], "蕃茄")
        self.assertEqual(expanded["FOOD001444"]["via"], ["FOOD001016"])
        self.assertEqual(expanded["FOOD001444"]["kinds"], ["allergen"])
        self.assertEqual(restrictions["excluded_food_ids"], ["FOOD001016", "FOOD001444"])
        # 家中有「蕃茄」也會被提醒：有成員對番茄過敏
        self.assertEqual(restrictions["available_but_restricted"], ["FOOD001444"])

    def test_available_ingredients_are_types_without_weights(self):
        created = self.create(full_draft([{"member_id": self.a["id"]}],
                                         available_ingredient_ids=["FOOD000678", "FOOD000867"]))
        self.assertEqual(created["available_ingredients"],
                         [{"ingredient_id": "FOOD000678", "name": "胡蘿蔔"},
                          {"ingredient_id": "FOOD000867", "name": "高麗菜"}])
        weighted = self.client.post("/meal-requests", json=full_draft(
            [{"member_id": self.a["id"]}], available_ingredient_ids=[{"ingredient_id": "FOOD000678", "weight_g": 100}]))
        self.assertEqual(weighted.status_code, 422)
        unknown = self.client.post("/meal-requests", json=full_draft(
            [{"member_id": self.a["id"]}], available_ingredient_ids=["FOOD999999"]))
        self.assertEqual(unknown.status_code, 422)
        self.assertEqual(unknown.json()["error"]["details"]["reason"], "UNKNOWN_INGREDIENT_ID")

    def test_specified_dishes_count_toward_dish_count(self):
        ok = self.create(full_draft([{"member_id": self.a["id"]}], dish_count=2,
                                    specified_dishes=["三杯雞", "  三杯雞 ", "番茄炒蛋"]))
        self.assertEqual(ok["draft"]["specified_dishes"], ["三杯雞", "番茄炒蛋"])
        self.assertTrue(ok["complete"], ok["issues"])
        over = self.create(full_draft([{"member_id": self.a["id"]}], dish_count=1,
                                      specified_dishes=["三杯雞", "番茄炒蛋"]))
        self.assertFalse(over["complete"])
        self.assertEqual(over["issues"][0]["reason"], "TOO_MANY_SPECIFIED_DISHES")
        # 不偷偷刪除或替換指定菜
        self.assertEqual(over["draft"]["specified_dishes"], ["三杯雞", "番茄炒蛋"])

    def test_duplicate_or_foreign_members_are_rejected(self):
        dup = self.client.post("/meal-requests", json=full_draft(
            [{"member_id": self.a["id"]}, {"member_id": self.a["id"], "bento": True}]))
        self.assertEqual(dup.status_code, 422)
        other = make_client()
        self.login_with_household(other, "other@example.test")
        foreign = other.post("/meal-requests", json=full_draft([{"member_id": self.a["id"]}]))
        self.assertEqual(foreign.status_code, 422)
        self.assertEqual(foreign.json()["error"]["details"]["reason"], "UNKNOWN_MEMBER")

    def test_deleted_member_is_reported_not_silently_dropped(self):
        created = self.create(full_draft([{"member_id": self.a["id"]}, {"member_id": self.b["id"]}]))
        self.assertEqual(self.client.delete(f"/members/{self.b['id']}").status_code, 204)
        reread = self.client.get(f"/meal-requests/{created['id']}").json()
        self.assertIn("UNKNOWN_MEMBER", {i["reason"] for i in reread["issues"]})
        self.assertEqual(len(reread["draft"]["participants"]), 2)

    def test_cross_household_access_is_denied(self):
        created = self.create(full_draft([{"member_id": self.a["id"]}]))
        other = make_client()
        self.login_with_household(other, "other@example.test")
        rid = created["id"]
        self.assertEqual(other.get(f"/meal-requests/{rid}").status_code, 404)
        self.assertEqual(other.get(f"/meal-requests/{rid}/versions/1").status_code, 404)
        self.assertEqual(other.put(f"/meal-requests/{rid}", json={"version": 1, "draft": {}}).status_code, 404)
        self.assertEqual(other.delete(f"/meal-requests/{rid}").status_code, 404)
        self.assertEqual(other.get("/meal-requests").json()["meal_requests"], [])
        listed = self.client.get("/meal-requests").json()["meal_requests"]
        self.assertEqual([r["id"] for r in listed], [rid])


if __name__ == "__main__":
    unittest.main()
