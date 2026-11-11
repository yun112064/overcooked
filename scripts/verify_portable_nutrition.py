"""Read-only portable-release parity audit using configured DATABASE_URL/env pins.

Run with --env-file <installer dotenv> --require-empty-runtime for a fresh install.
No users, jobs, source records or policy decisions are created by this audit.
"""
import argparse
from collections import Counter
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT, ROOT / 'backend'):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))


def argument_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--require-empty-runtime', action='store_true')
    parser.add_argument('--env-file', type=Path,
                        help='Load installer dotenv before application settings (overrides environment).')
    return parser


def load_environment(args):
    if args.env_file is not None:
        from dotenv import load_dotenv
        if not args.env_file.is_file():
            raise ValueError('env_file_missing')
        load_dotenv(args.env_file, override=True, interpolate=False)


# Settings are cached during app imports. Parse exactly once for direct CLI use
# and load quoted dotenv values before importing any application modules.
_CLI_ARGS = None
if __name__ == '__main__':
    _CLI_ARGS = argument_parser().parse_args()
    try:
        load_environment(_CLI_ARGS)
    except ValueError as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}))
        raise SystemExit(1)

from sqlalchemy import inspect, select, text

from app.db.session import SessionLocal
from app.models import Recipe
from app.services import reconciled_policy
from app.services.meal_calculation import IngredientLine, load_recipe_lines
from nutrition_planning.integration import ALL_NUTRIENTS, load_database_recipe_rows


# These contain user activity, not imported source/approval evidence.
RUNTIME_TABLES = (
    'users', 'households', 'household_memberships', 'user_sessions', 'household_members',
    'meal_requests', 'meal_request_revisions', 'background_jobs', 'budget_months',
    'usage_reservations', 'recognitions', 'adopted_meal_plans', 'meal_confirmation_events',
    'sim_market_orders', 'generation_results', 'meal_result_decisions', 'meal_menu_rejections',
    'knowledge_crawl_requests',
)
EXPECTED_BLOCKED = {
    'RI000580': 'FOOD000312', 'RI001209': 'FOOD000808',
    'RI001700': 'FOOD001170', 'RI002375': 'FOOD001682',
}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def runtime_counts(db):
    available = set(inspect(db.connection()).get_table_names())
    require(set(RUNTIME_TABLES) <= available, 'runtime_tables_missing')
    # Identifiers are the static allowlist above, never CLI/user text.
    return {table: db.scalar(text(f'SELECT count(*) FROM "{table}"')) for table in RUNTIME_TABLES}


def compare_record(output, record, grams, manifest_hash):
    require(not output.error and record is not None, 'resolved_record_missing')
    for nutrient in ALL_NUTRIENTS:
        expected = record['nutrition_per_100g'].get(nutrient)
        actual = output.nutrients[nutrient]
        require((actual is None) == (expected is None), f'null_parity:{nutrient}')
        if expected is not None:
            exact = Fraction(expected) * Fraction(grams) / 100
            require(actual == Decimal(exact.numerator) / Decimal(exact.denominator),
                    f'nutrient_parity:{nutrient}')
    for key, value in record['source'].items():
        require(output.trace.get(key) == value, f'source_parity:{key}')
    require(output.trace['nutrition_policy_manifest_sha256'] == manifest_hash, 'policy_hash_parity')


def verify(require_empty=False):
    policy = reconciled_policy.load_policy()
    require(policy.get('status') == 'approved_for_release', 'portable_release_policy_required')
    with SessionLocal() as db:
        require(db.bind.dialect.name == 'postgresql', 'postgresql_required')
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        reconciled_policy.validate_database(db, policy)

        # Keep preview and formal calculation in the same read-only snapshot.
        @contextmanager
        def factory():
            yield db

        calculator = reconciled_policy.make_calculator(factory)
        counts = runtime_counts(db) if require_empty else None
        require(not counts or not any(counts.values()), 'runtime_not_empty')
        ids = list(db.scalars(select(Recipe.recipe_id).where(
            Recipe.recipe_release_id == policy['recipe_release_id']).order_by(Recipe.recipe_id)))
        rows = load_database_recipe_rows(db.connection(), ids)
        shared = reconciled_policy.resolve_rows(db.connection(), rows, policy)
        lines = [line for rid in ids for line in load_recipe_lines(db, rid)]
        require(len(ids) == 293 and len(lines) == 2477, 'release_recipe_line_counts_changed')
        actual = calculator([(line, Decimal(100)) for line in lines])
        require(len(actual) == len(lines), 'formal_result_count_mismatch')
        compared, failures, unresolved = 0, Counter(), []
        for line, output in zip(lines, actual):
            record = shared.line_records.get(line.recipe_ingredient_id)
            if output.error:
                require(record is None, 'blocked_line_has_preview_record')
                require(output.error == 'nutrition_recipe_derived_only'
                        and EXPECTED_BLOCKED.get(line.recipe_ingredient_id) == line.food_id,
                        'unexpected_blocked_line')
                failures[output.error] += 1
                unresolved.append({'line_id': line.recipe_ingredient_id, 'food_id': line.food_id,
                                   'reason': output.error})
            else:
                compare_record(output, record, Decimal(100), policy['_sha256'])
                compared += 1
        require(compared == 2473 and len(unresolved) == 4, 'release_parity_counts_changed')
        staples = []
        for fid, name in [('FOOD000296', '\u767d\u98ef'), ('FOOD001487', '\u7cd9\u7c73\u98ef')]:
            lid = 'staple:' + fid
            staple_rows = replace(rows, recipes=({'recipe_id': 'staple', 'original_servings': 1,
                'review_status': 'approved', 'is_eligible': True},), ingredients=({
                'recipe_id': 'staple', 'recipe_ingredient_id': lid, 'food_id': fid,
                'canonical_name': name, 'weight_g': Decimal(160), 'unit': 'g', 'sequence': 1,
                'is_required': True, 'weight_status': 'source_explicit'},))
            preview = reconciled_policy.resolve_rows(db.connection(), staple_rows, policy)
            line = IngredientLine(food_id=fid, name=name, grams_per_n=Decimal(160), role='staple')
            result = calculator([(line, Decimal(160))])[0]
            compare_record(result, preview.line_records.get(lid), Decimal(160), policy['_sha256'])
            staples.append({'food_id': fid, 'name': name, 'grams': 160,
                'energy_kcal': str(result.nutrients['energy_kcal']),
                'protein_g': str(result.nutrients['protein_g']), 'provider': result.trace['provider']})
        return {'ok': True, 'synthetic': False, 'policy': policy['nutrition_policy_version'],
            'manifest_sha256': policy['_sha256'], 'recipe_count': len(ids), 'line_count': len(lines),
            'matching_source_and_nutrient_lines': compared, 'unresolved_counts': dict(failures),
            'unresolved_lines': unresolved, 'staples': staples, 'runtime_empty_required': require_empty,
            'runtime_counts': counts,
            'scope': 'source/arithmetic parity and optional fresh-install emptiness; not E2E or source-table tamper audit'}


def main():
    args = _CLI_ARGS
    if args is None:
        raise RuntimeError('Use this verifier as a CLI so dotenv loads before app settings.')
    try:
        report = verify(args.require_empty_runtime)
    except (ValueError, AssertionError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=True))
        return 1
    print(json.dumps(report, ensure_ascii=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
