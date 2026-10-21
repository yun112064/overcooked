import unittest

from nutrition_service import calculate_nutrition, calculate_recipe_nutrition


class T04CompletionTests(unittest.TestCase):
    def test_red_carrot_alias_resolves_to_confirmed_tfda_ingredient(self):
        result = calculate_nutrition("紅蘿蔔", 80)

        self.assertTrue(result["success"])
        self.assertEqual(result["ingredient_id"], "E02001")
        self.assertEqual(result["standard_name"], "胡蘿蔔平均值")

    def test_recipe_total_is_unavailable_when_an_ingredient_fails(self):
        result = calculate_recipe_nutrition(
            recipe_name="可驗證食譜",
            recipe_ingredients=[
                {"name": "雞胸肉", "weight_g": 150},
                {"name": "不存在的食材", "weight_g": 80},
            ],
        )

        self.assertFalse(result["success"])
        self.assertFalse(result["nutrition_complete"])
        self.assertEqual(result["failed_ingredients"][0]["ingredient"], "不存在的食材")
        self.assertIsNone(result["total_nutrition"]["energy_kcal"])
        self.assertIsNone(result["total_nutrition"]["protein_g"])
        self.assertIsNotNone(result["partial_nutrition_totals"]["energy_kcal"])


if __name__ == "__main__":
    unittest.main()
