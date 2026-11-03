"""Verify T03 and the selected T01 release using a read-only V3 transaction."""
from __future__ import annotations

import argparse
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import uuid
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT)]

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.core.config import settings
from app.services import household, ingredients, meal_request

T01_TABLES = ('recipe_data_releases', 'source_archive', 'recipes', 'recipe_steps',
              'recipe_ingredients', 'ingredient_aliases', 'allergens')
T02_TABLES = ('price_data_releases', 'price_collection_runs', 'price_quote_snapshots',
              'price_snapshot_foods', 'retailer_product_mappings')


def check(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def require_v3(url) -> None:
    check(url.database == 'meal_planner_1102_v3' and url.host == '127.0.0.1'
          and url.port == 55435, 'Verification requires the dedicated local V3 database')


def source_digests(connection) -> dict:
    result = {}
    for table in (*T01_TABLES, *T02_TABLES):
        rows = connection.execute(text(
            f'SELECT to_jsonb(t) FROM "{table}" t ORDER BY to_jsonb(t)::text')).scalars().all()
        result[table] = hashlib.sha256(json.dumps(
            rows, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return result


def synthetic_members() -> tuple[date, dict]:
    on = date(2026, 11, 3)
    adult_id = uuid.UUID('00000000-0000-0000-0000-000000000001')
    child_id = uuid.UUID('00000000-0000-0000-0000-000000000002')
    common = dict(equation_sex='M', activity_category='sedentary',
                  allergen_ingredient_ids=[], avoided_ingredient_ids=[])
    return on, {
        adult_id: SimpleNamespace(id=adult_id, birth_date=date(1996, 3, 1),
            weight_kg=Decimal('70'), goal='maintain', portion=Decimal('1.3'), **common),
        child_id: SimpleNamespace(id=child_id, birth_date=date(2018, 1, 1),
            weight_kg=Decimal('28'), goal='general', portion=Decimal('0.7'), **common),
    }


def verify() -> dict:
    manifest = json.loads((ROOT / 'data/recipe_release_manifest.json').read_text(encoding='utf-8'))
    workbook = ROOT / manifest['workbook']
    workbook_hash = hashlib.sha256(workbook.read_bytes()).hexdigest()
    check(workbook_hash == manifest['sha256'], 'Selected recipe workbook hash mismatch')
    check(settings.generation_recipe_release_id == manifest['release_id'],
          'Runtime configuration does not select the current recipe release')
    service = household.target_service()
    check(service.rule_version == 'hpa-dri8-mvp-0.3.0', 'T03 rule was changed unexpectedly')
    rule_hash = hashlib.sha256(Path(settings.nutrition_rules_path).read_bytes()).hexdigest()
    engine = create_engine(settings.database_url)
    require_v3(engine.url)
    try:
        with engine.connect() as connection:
            connection.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
            check(connection.scalar(text('SHOW transaction_read_only')) == 'on', 'Transaction is not read-only')
            check(connection.scalar(text('SELECT current_database()')) == 'meal_planner_1102_v3',
                  'Unexpected database identity')
            head = connection.scalar(text('SELECT version_num FROM alembic_version'))
            config = Config(str(ROOT / 'alembic.ini'))
            config.set_main_option('script_location', str(ROOT / 'migrations'))
            check(ScriptDirectory.from_config(config).get_heads() == [head], 'Database schema is not at code head')
            before = source_digests(connection)
            baseline = json.loads((ROOT / 'docs/1102_V3/T02/database-verification.json').read_text(encoding='utf-8'))
            check(all(before[t] == baseline['t01_digests'][t] for t in T01_TABLES),
                  'T01 content differs from the verified latest recipe release')
            release = connection.execute(text(
                'SELECT release_id,source_sha256 FROM recipe_data_releases WHERE active')).mappings().one()
            check(release['release_id'] == manifest['release_id'] and release['source_sha256'] == workbook_hash,
                  'Installed active recipe release does not match the selected workbook')
            for table, count in manifest['row_counts'].items():
                check(table in T01_TABLES, 'Unexpected source table in recipe manifest')
                check(connection.scalar(text(f'SELECT count(*) FROM "{table}"')) == count,
                      'Installed recipe counts differ from the selected workbook release')
            special = [dict(r) for r in connection.execute(text(
                "SELECT recipe_id,original_servings,servings_basis,is_eligible FROM recipes "
                "WHERE recipe_id IN ('R0252','R0254') ORDER BY recipe_id")).mappings()]
            check(len(special) == 2 and all(r['is_eligible'] for r in special), 'Special recipes are not selectable')
            check(special[0]['original_servings'] == Decimal('950') and special[0]['servings_basis'] == 'portions'
                  and special[1]['original_servings'] == Decimal('1') and special[1]['servings_basis'] == 'batch',
                  'Special recipe portion/batch meaning changed')
            on, members = synthetic_members()
            adult, child = members.values()
            with Session(bind=connection) as db:
                check(ingredients.unknown_ids(db, ['FOOD001993']) == [], 'Latest T01 food dictionary is unavailable')
                fields = {k: getattr(adult, k) for k in (
                    'birth_date', 'equation_sex', 'weight_kg', 'activity_category', 'goal', 'portion')}
                fields.update(allergen_ingredient_ids=['FOOD001993'], avoided_ingredient_ids=[])
                check(household.validate_member(db, fields, today=on)['allergen_ingredient_ids'] == ['FOOD001993'],
                      'Member validation does not accept the current T01 dictionary')
            daily = household.daily_target(adult, on=on)
            check(Decimal(daily['daily_reference']['energy_kcal']) == Decimal('1942.8591')
                  and Decimal(daily['daily_reference']['protein_g']) == Decimal('77'), 'T03 daily target changed')
            check(daily['rule_config_sha256'] == rule_hash and not daily['approved_for_adoption'],
                  'T03 source version or adoption status changed')
            draft = meal_request.MealRequestDraft(meal_date=on, meal_type='dinner',
                participants=[{'member_id': adult.id, 'bento': True}, {'member_id': child.id}])
            preview = meal_request.target_preview(draft, members)
            aggregate = service.aggregate_meal_targets(meal_request.target_participations(draft, members))
            expected = {'energy_kcal': Decimal('2043.53067828'), 'protein_g': Decimal('78.694')}
            check(preview['status'] == 'available' and Decimal(preview['total_n']) == Decimal('3.3')
                  and {k: Decimal(v) for k, v in preview['target'].items()} == expected,
                  'T03 questionnaire/bento targets changed')
            check(preview['target'] == aggregate['target'] and len(aggregate['participations']) == 3,
                  'T03 participation bridge differs from questionnaire preview')
            check(source_digests(connection) == before, 'Source data changed during verification')
            result = {
                'checked_at_taipei': datetime.now(ZoneInfo('Asia/Taipei')).isoformat(),
                'database': 'meal_planner_1102_v3', 'port': 55435,
                'head': head,
                'transaction_read_only': True, 'database_write_operations': 0,
                'recipe_release_id': manifest['release_id'], 'workbook_sha256': workbook_hash,
                'recipe_count': manifest['row_counts']['recipes'], 'special_recipes': special,
                'rule_version': service.rule_version, 'rule_sha256': rule_hash,
                't03_reads_recipe_data': False, 'recipe_role': 'T01 dictionary validation at T07 boundary; T04/T12 recipe comparison',
                'synthetic_daily': daily['daily_reference'], 'synthetic_total_n': preview['total_n'],
                'synthetic_meal_target': preview['target'], 'latest_food_dictionary_validation': 'PASS',
                't01_matches_verified_t02_baseline': True, 't01_t02_digests_unchanged': True,
                'source_digests': before, 'architecture_and_rules': 'preserved',
                'schema_migration_required': False, 'g03_acceptance': 'NOT_RUN',
            }
    finally:
        engine.dispose()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report', type=Path, default=ROOT / 'docs/1102_V3/T03/verification.json')
    args = parser.parse_args()
    result = verify()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + '\n', encoding='utf-8')
    print('PASS: latest T01 release and T03 0.3.0; read-only V3 verification; no schema/data writes')


if __name__ == '__main__':
    main()
