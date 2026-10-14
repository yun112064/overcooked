"""Synthetic, independently calculated examples and boundary checks for T03."""

import json
import tempfile
import unittest
from decimal import Decimal, localcontext
from pathlib import Path

from app.nutrition import NutritionTargetService, TargetInputError


RULE = Path(__file__).resolve().parents[1] / "config/nutrition/hpa-dri8-mvp-0.1.0.json"


def member(**changes):
    return {
        "member_id": "synthetic-A", "age_years": 30, "equation_sex": "M",
        "weight_kg": "70", "activity_category": "sedentary",
        "calculation_date": "2026-10-05", "goal": "maintain", **changes,
    }


class NutritionTests(unittest.TestCase):
    def setUp(self):
        self.service = NutritionTargetService(RULE)

    def assert_reason(self, reason, operation, data):
        with self.assertRaises(TargetInputError) as result:
            operation(data)
        self.assertEqual(result.exception.reason, reason)
        self.assertEqual(result.exception.code, "INVALID_INPUT")

    def test_independent_energy_examples(self):
        # Expected outputs are hand calculations, not regenerated from rule config.
        examples = [
            (member(), "1942.8591"),
            (member(equation_sex="F", weight_kg="55", activity_category="low_active"), "1713.822"),
            (member(age_years=19, weight_kg="65"), "1877.384665"),
            (member(age_years=75, equation_sex="F", weight_kg="50"), "1323.78675"),
            (member(weight_kg="70.125"), "1944.9223096875"),
        ]
        for data, expected in examples:
            with self.subTest(data=data):
                actual = self.service.energy_baseline(data)
                self.assertEqual(Decimal(actual["unadjusted_energy_kcal"]), Decimal(expected))

    def test_four_official_activity_coefficients(self):
        for category, expected in [
            ("sedentary", "1942.8591"), ("low_active", "2241.7605"),
            ("active", "2540.6619"), ("highly_active", "2839.5633"),
        ]:
            with self.subTest(category=category):
                result = self.service.energy_baseline(member(activity_category=category))
                self.assertEqual(Decimal(result["unadjusted_energy_kcal"]), Decimal(expected))

    def test_mvp_uses_adult_standard_without_claiming_source_coverage(self):
        result = self.service.energy_baseline(member(age_years=8, weight_kg="28", goal="general"))
        self.assertEqual(Decimal(result["unadjusted_energy_kcal"]), Decimal("984.031776"))
        self.assertEqual(result["source_energy_scope"], "adults_age_19_and_above")
        self.assertFalse(result["approved_for_adoption"])

    def test_energy_is_unadjusted_for_each_goal(self):
        for goal in ("general", "maintain", "muscle_gain", "fat_loss"):
            with self.subTest(goal=goal):
                result = self.service.energy_baseline(member(goal=goal))
                self.assertEqual(Decimal(result["unadjusted_energy_kcal"]), Decimal("1942.8591"))
                self.assertFalse(result["daily_target_available"])

    def test_pending_daily_rules_cannot_fall_back_to_maintain(self):
        for goal in ("muscle_gain", "fat_loss"):
            self.assert_reason("GOAL_RULE_PENDING_CONFIRMATION", self.service.daily_target, member(goal=goal))
        for goal in ("maintain", "general"):
            self.assert_reason("PROTEIN_RULE_PENDING_CONFIRMATION", self.service.daily_target, member(goal=goal))

    def test_rejects_missing_and_invalid_member_inputs(self):
        missing = member()
        del missing["activity_category"]
        self.assert_reason("REQUIRED_TARGET_INPUT_MISSING", self.service.energy_baseline, missing)
        for field, values, reason in [
            ("age_years", [True, -1, 3.5, "30"], "INVALID_AGE"),
            ("weight_kg", [True, 70.0, "NaN", "Infinity", "bad", None], None),
            ("weight_kg", ["0", "-1"], "INVALID_WEIGHT"),
            ("equation_sex", ["unknown", 1, [], {}], "UNSUPPORTED_EQUATION_SEX"),
            ("activity_category", ["unknown", 1, []], "INVALID_ACTIVITY"),
            ("goal", ["unknown", [], 1], "INVALID_GOAL"),
            ("calculation_date", ["2026-02-30", "20261005", "2026-10-05T00:00:00", 1], "INVALID_DATE"),
        ]:
            for value in values:
                with self.subTest(field=field, value=value):
                    with self.assertRaises(TargetInputError) as result:
                        self.service.energy_baseline(member(**{field: value}))
                    if reason:
                        self.assertEqual(result.exception.reason, reason)

    def test_portion_is_validated_but_never_enters_daily_formula(self):
        for value in ("0.7", "0.85", "1", "1.15", "1.3"):
            self.assertEqual(self.service.validate_portion(value), Decimal(value))
        for value in (True, "0.69", "0.9", "1.31", "2.6", "NaN", "Infinity"):
            with self.assertRaises(TargetInputError):
                self.service.validate_portion(value)
        self.assert_reason("UNKNOWN_FIELD", self.service.energy_baseline, member(n="1.3"))

    def test_nonpositive_derived_energy_is_rejected(self):
        self.assert_reason("UNSUPPORTED_TARGET_SCOPE", self.service.energy_baseline, member(weight_kg="1000"))

    def test_result_preserves_inputs_and_rule_identity(self):
        result = self.service.energy_baseline(member())
        self.assertEqual(result["input_snapshot"], member())
        self.assertEqual(result["rule_version"], "hpa-dri8-mvp-0.1.0")
        self.assertEqual(len(result["rule_config_sha256"]), 64)
        self.assertNotIn("protein_g", result)
        self.assertNotIn("daily_reference", result)

    def test_unknown_version_does_not_fall_back(self):
        rule = json.loads(RULE.read_text())
        rule["rule_version"] = "unknown"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "unknown.json"
            path.write_text(json.dumps(rule))
            self.assert_reason("UNKNOWN_RULE_VERSION", NutritionTargetService, path)

    def test_arithmetic_ignores_callers_decimal_precision(self):
        with localcontext() as ctx:
            ctx.prec = 6
            result = self.service.energy_baseline(member())
        self.assertEqual(Decimal(result["unadjusted_energy_kcal"]), Decimal("1942.8591"))

    def test_frozen_version_cannot_silently_change_coefficients(self):
        rule = json.loads(RULE.read_text())
        rule["energy"]["intercept"] = "30"
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "changed.json"
            path.write_text(json.dumps(rule))
            self.assert_reason("RULE_CONFIG_VERSION_CONFLICT", NutritionTargetService, path)


if __name__ == "__main__":
    unittest.main()
