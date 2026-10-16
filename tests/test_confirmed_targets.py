"""Confirmed MVP daily and combined meal targets; no real member data."""

import json
import os
import subprocess
import sys
import unittest
from copy import deepcopy
from decimal import Decimal, localcontext
from fractions import Fraction
from pathlib import Path

from app.nutrition import NutritionTargetService, TargetInputError
from test_nutrition import member


ROOT = Path(__file__).resolve().parents[1]
RULE = ROOT / "config/nutrition/hpa-dri8-mvp-0.2.0.json"


def participation(identifier="dinner", *, kind="dining", meal="dinner", n="1.3", profile=None):
    return {
        "participation_id": identifier, "kind": kind, "member": profile or member(),
        "meal": meal, "n": n,
    }


class ConfirmedTargetTests(unittest.TestCase):
    def setUp(self):
        self.service = NutritionTargetService(RULE)

    def assert_reason(self, expected, operation, *args):
        with self.assertRaises(TargetInputError) as result:
            operation(*args)
        self.assertEqual(result.exception.reason, expected)

    def test_manual_daily_examples(self):
        examples = [
            (member(), "1942.8591", "77"),
            (member(goal="general"), "1942.8591", "77"),
            (member(goal="muscle_gain", activity_category="active"), "2667.694995", "77"),
            (member(goal="fat_loss", age_years=45, weight_kg="90"), "1669.24435", "99"),
            (member(equation_sex="F", weight_kg="55", activity_category="low_active"), "1713.822", "60.5"),
            (member(age_years=8, weight_kg="28", goal="general"), "984.031776", "30.8"),
            (member(age_years=75, equation_sex="F", weight_kg="50"), "1323.78675", "55"),
        ]
        for data, energy, protein in examples:
            with self.subTest(data=data):
                result = self.service.daily_target(data)
                self.assertEqual(Decimal(result["daily_reference"]["energy_kcal"]), Decimal(energy))
                self.assertEqual(Decimal(result["daily_reference"]["protein_g"]), Decimal(protein))
                self.assertTrue(result["daily_target_available"])
                self.assertFalse(result["approved_for_adoption"])

    def test_uniform_protein_across_ages_and_goals(self):
        for age in (0, 3, 8, 18, 19, 69, 70, 71, 75):
            for goal in ("general", "maintain", "muscle_gain", "fat_loss"):
                with self.subTest(age=age, goal=goal):
                    result = self.service.daily_target(member(age_years=age, weight_kg="90", goal=goal))
                    self.assertEqual(Decimal(result["daily_reference"]["protein_g"]), Decimal("99"))

    def test_fat_loss_floor_rejects_without_clamping(self):
        self.assert_reason("DAILY_ENERGY_BELOW_MINIMUM", self.service.daily_target, member(weight_kg="55", goal="fat_loss"))
        result = self.service.daily_target(member(weight_kg="60", goal="fat_loss"))
        self.assertEqual(Decimal(result["daily_reference"]["energy_kcal"]), Decimal("1261.5598"))
        # Maintain can be below 1200; the 1200 condition is not silently extended to all goals.
        result = self.service.daily_target(member(age_years=8, weight_kg="28", goal="general"))
        self.assertEqual(Decimal(result["daily_reference"]["energy_kcal"]), Decimal("984.031776"))

    def test_meal_ratios_do_not_apply_n(self):
        expected = {
            "breakfast": ("582.85773", "23.1"),
            "lunch": ("582.85773", "23.1"),
            "dinner": ("777.14364", "30.8"),
        }
        for meal, (energy, protein) in expected.items():
            result = self.service.meal_target(member(), meal)
            self.assertEqual(Decimal(result["meal_reference"]["energy_kcal"]), Decimal(energy))
            self.assertEqual(Decimal(result["meal_reference"]["protein_g"]), Decimal(protein))
            self.assertEqual(Decimal(result["daily_reference"]["energy_kcal"]), Decimal("1942.8591"))
        for meal in (None, "snack", [], {}):
            self.assert_reason("INVALID_MEAL", self.service.meal_target, member(), meal)

    def test_all_five_n_values_scale_once(self):
        for n, energy, protein in [
            ("0.7", "544.000548", "21.56"), ("0.85", "660.572094", "26.18"),
            ("1", "777.14364", "30.8"), ("1.15", "893.715186", "35.42"),
            ("1.3", "1010.286732", "40.04"),
        ]:
            with self.subTest(n=n):
                result = self.service.aggregate_meal_targets([participation(n=n)])
                self.assertEqual(Decimal(result["target"]["energy_kcal"]), Decimal(energy))
                self.assertEqual(Decimal(result["target"]["protein_g"]), Decimal(protein))
                exact = result["reference_exact"]["energy_kcal"]
                self.assertEqual(Fraction(int(exact["numerator"]), int(exact["denominator"])), Fraction("777.14364"))

    def test_dining_plus_bento_and_second_member_manual_totals(self):
        items = [participation(), participation("bento", kind="bento", meal="lunch"),
                 participation("young", n="0.7", profile=member(member_id="synthetic-young", age_years=8, weight_kg="28", goal="general"))]
        original = deepcopy(items)
        result = self.service.aggregate_meal_targets(items)
        self.assertEqual(items, original)
        self.assertEqual(Decimal(result["total_n"]), Decimal("3.3"))
        self.assertEqual(Decimal(result["target"]["energy_kcal"]), Decimal("2043.53067828"))
        self.assertEqual(Decimal(result["target"]["protein_g"]), Decimal("78.694"))
        for key, expected in [("energy_kcal", Fraction(17029422319, 27500000)), ("protein_g", Fraction(3577, 150))]:
            fraction = result["reference_exact"][key]
            actual = Fraction(int(fraction["numerator"]), int(fraction["denominator"]))
            self.assertEqual(actual, expected)
            self.assertEqual(actual * Fraction(result["total_n"]), Fraction(result["target"][key]))
        self.assertEqual(len(result["participations"]), 3)

    def test_total_n_is_not_restricted_to_member_choices(self):
        result = self.service.aggregate_meal_targets([participation("one"), participation("two", kind="bento", meal="lunch")])
        self.assertEqual(Decimal(result["total_n"]), Decimal("2.6"))

    def test_participation_validation_and_consistency(self):
        for data in ([], {}, None):
            self.assert_reason("INVALID_PARTICIPATIONS", self.service.aggregate_meal_targets, data)
        self.assert_reason("DUPLICATE_PARTICIPATION", self.service.aggregate_meal_targets, [participation(), participation()])
        self.assert_reason("INVALID_PARTICIPATION", self.service.aggregate_meal_targets, [{"member": member()}])
        self.assert_reason("INVALID_PORTION", self.service.aggregate_meal_targets, [participation(n="0.9")])
        changed = participation("other", profile=member(weight_kg="71"))
        self.assert_reason("MEMBER_SNAPSHOT_CONFLICT", self.service.aggregate_meal_targets, [participation(), changed])
        changed = participation("other", profile=member(member_id="different", calculation_date="2026-10-06"))
        self.assert_reason("CALCULATION_DATE_CONFLICT", self.service.aggregate_meal_targets, [participation(), changed])
        for field, value, reason in [("kind", "other", "INVALID_PARTICIPATION_KIND"), ("participation_id", "", "INVALID_PARTICIPATION"), ("meal", [], "INVALID_MEAL")]:
            item = participation(); item[field] = value
            self.assert_reason(reason, self.service.aggregate_meal_targets, [item])

    def test_external_decimal_context_cannot_round_targets(self):
        with localcontext() as ctx:
            ctx.prec = 6
            daily = self.service.daily_target(member(goal="muscle_gain", activity_category="active"))
            combined = self.service.aggregate_meal_targets([participation()])
        self.assertEqual(Decimal(daily["daily_reference"]["energy_kcal"]), Decimal("2667.694995"))
        self.assertEqual(Decimal(combined["target"]["energy_kcal"]), Decimal("1010.286732"))

    def test_daily_cli_accepts_decimal_json_numbers(self):
        data = member(weight_kg=70.125)
        result = subprocess.run(
            [sys.executable, "-m", "app.nutrition", "--rules", str(RULE), "--input", "-", "--operation", "daily"],
            input=json.dumps(data), capture_output=True, text=True,
            cwd=ROOT, env={**os.environ, "PYTHONPATH": str(ROOT / "backend")},
        )
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        target = json.loads(result.stdout)["daily_reference"]
        self.assertEqual(Decimal(target["energy_kcal"]), Decimal("1944.9223096875"))
        self.assertEqual(Decimal(target["protein_g"]), Decimal("77.1375"))

    def test_returned_snapshots_cannot_change_frozen_rule_metadata(self):
        first = self.service.daily_target(member())
        first["source"]["energy_printed_pages"].clear()
        first["adjustment_sources"][0]["url"] = "changed"
        second = self.service.daily_target(member())
        self.assertEqual(second["source"]["energy_printed_pages"], [37, 38, 40, 41])
        self.assertNotEqual(second["adjustment_sources"][0]["url"], "changed")


if __name__ == "__main__":
    unittest.main()
