"""Read-only real portable AI food-source eligibility probe; no model calls."""
from collections import Counter
from contextlib import contextmanager
from decimal import Decimal
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]
from sqlalchemy import text
from app.db.session import SessionLocal
from app.services import reconciled_policy
from app.services.generation import FoodDictionary, uncomputable_policy_food_ids
from app.services.meal_calculation import IngredientLine


def main():
    policy = reconciled_policy.load_policy()
    if policy['status'] != 'approved_for_release':
        raise ValueError('portable_policy_required')
    with SessionLocal() as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        reconciled_policy.validate_database(db, policy)
        @contextmanager
        def factory():
            yield db
        calculator = reconciled_policy.make_calculator(factory)
        foods = FoodDictionary.load(db)
        ids = sorted(foods.candidate_ids())
        blocked = uncomputable_policy_food_ids(foods, calculator)
        results = calculator([(IngredientLine(fid, foods.names[fid], Decimal(100)), Decimal(100)) for fid in ids])
        derived = {'FOOD000312', 'FOOD000808', 'FOOD001170', 'FOOD001682'}
        if not (derived & set(ids)) <= blocked:
            raise ValueError('recipe_derived_source_was_allowed')
        accepted = set(policy['overlap_review_decision']['individually_accepted_food_ids'])
        report = {'synthetic': False, 'policy': policy['nutrition_policy_version'],
            'candidate_count': len(ids), 'allowed_count': len(set(ids) - blocked),
            'blocked_reasons': dict(Counter(result.error or 'required_nutrient_unknown'
                for fid, result in zip(ids, results) if fid in blocked)),
            'reviewed70_allowed': sorted(accepted & set(ids) - blocked),
            'blocked': [{'food_id': fid, 'name': foods.names[fid], 'reason': result.error or 'required_nutrient_unknown'}
                        for fid, result in zip(ids, results) if fid in blocked],
            'contract': 'AI declares raw mass; cooked sources require a separate explicit contract',
            'scope': 'read-only source eligibility, not generated meal acceptance'}
        print(json.dumps(report, ensure_ascii=True))


if __name__ == '__main__':
    main()
