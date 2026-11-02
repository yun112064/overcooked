from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.core.config import settings
from app.services.generation import make_generation_handler


class GenerationNutritionSourceTests(unittest.TestCase):
    def test_default_generation_uses_explicit_sql_versions_and_never_legacy_cache(self):
        sentinel = object()
        factory = object()
        ctx = SimpleNamespace(factory=factory)
        with patch("app.services.meal_calculation.versioned_sql_calculator", return_value=sentinel) as sql_calc, \
             patch("app.services.meal_calculation.cached_factor_calculator",
                   side_effect=AssertionError("legacy calculator must not be used")) as legacy_calc, \
             patch("app.services.generation._generate", return_value={"ok": True}) as generate:
            handler = make_generation_handler(responder=None, knowledge_client=None, embedder=None)
            result = handler(ctx)
        self.assertEqual(result, {"ok": True})
        sql_calc.assert_called_once_with(factory,
            recipe_release_id=settings.generation_recipe_release_id,
            nutrition_import_id=settings.generation_nutrition_import_id,
            mapping_version=settings.generation_food_mapping_version,
            cooking_receipts=None)
        legacy_calc.assert_not_called()
        self.assertIs(generate.call_args.kwargs["calculator"], sentinel)


if __name__ == "__main__":
    unittest.main()
