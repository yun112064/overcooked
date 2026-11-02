from fractions import Fraction as F
from pathlib import Path

from nutrition_target import NutritionTargetService
from nutrition_planning import targets_from_t03


ROOT = Path(__file__).resolve().parents[2]


def test_t03_dining_and_bento_keep_exact_combined_reference():
    service = NutritionTargetService(ROOT / "nutrition_target/rules/hpa-dri8-mvp-0.3.0.json")
    member = {
        "member_id": "synthetic-adult", "age_years": 30, "equation_sex": "M",
        "weight_kg": "70", "activity_category": "sedentary",
        "calculation_date": "2026-10-28", "goal": "maintain",
    }
    aggregate = service.aggregate_meal_targets([
        {"participation_id": "dinner", "kind": "dining", "member": member,
         "meal": "dinner", "n": "1.3"},
        {"participation_id": "box", "kind": "bento", "member": member,
         "meal": "lunch", "n": "1.3"},
        {"participation_id": "child", "kind": "dining", "meal": "dinner", "n": "0.7",
         "member": {**member, "member_id": "synthetic-child", "age_years": 8,
                    "weight_kg": "28", "goal": "general"}},
    ])
    target = targets_from_t03(aggregate)
    assert target.total_n == F("3.3")
    assert target.target == {"energy_kcal": F("2043.53067828"), "protein_g": F("78.694")}
    assert target.reference == {"energy_kcal": F(17029422319, 27500000), "protein_g": F(3577, 150)}
    assert len(target.snapshot["participations"]) == 3
    assert target.approved_for_adoption is False
