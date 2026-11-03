"""Read-only verification of the installed T04 source release and preserved T01/T02."""
from collections import Counter
from decimal import Decimal
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT),str(ROOT/'backend')]

from sqlalchemy import create_engine, select, text, inspect, func
from sqlalchemy.orm import Session
from app.models import RecipeDataRelease, Recipe, IngredientAlias
from app.models.nutrition import (NutritionDataRelease, NutritionImport, FoodNutrition)
from app.services.nutrition_release import T04_TABLES, load_bundle, mapping_plan, verify_installed, recipe_import_matches
from app.services.nutrition_import import NUTRIENT_COLUMNS
from app.services.t04_database_nutrition import calculate_food_nutrition
from app.services.meal_calculation import versioned_sql_calculator, IngredientLine
from scripts.t04_db_support import guard, snapshot, HEAD


def main():
    from app.core.config import settings
    from dotenv import dotenv_values
    folder = ROOT/'docs/1102_V3/T04'
    selected = json.loads((folder/'source-selection.json').read_text(encoding='utf-8'))
    bundle = load_bundle(ROOT/'data/nutrition/t04-food1018-v3/manifest.json',selected['manifest_sha256'])
    before = json.loads((folder/'before.json').read_text(encoding='utf-8'))
    engine = create_engine(settings.database_url)
    guard(engine)
    with engine.connect() as connection:
        connection.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        assert connection.scalar(text('SELECT version_num FROM alembic_version')) == HEAD
        current = snapshot(connection)
        unchanged = {t: current['table_digests'].get(t) == value
                     for t,value in before['table_digests'].items()
                     if t not in T04_TABLES | {'alembic_version'}}
        assert all(unchanged.values()), 'A non-T04 source/runtime table changed'
        with Session(bind=connection) as session:
            manifest = bundle.manifest
            imported = session.get(NutritionImport,bundle.nutrition.import_id)
            release = session.get(RecipeDataRelease,manifest['recipe_release_id'])
            publication = session.get(NutritionDataRelease,manifest['release_id'])
            assert release and release.active and release.source_sha256 == manifest['recipe_source_sha256']
            assert publication and publication.active
            assert publication.source_manifest == manifest and publication.manifest_sha256 == bundle.manifest_sha256
            assert publication.import_id == imported.import_id and publication.mapping_version == manifest['mapping_version']
            assert publication.recipe_release_id == release.release_id and publication.recipe_source_sha256 == release.source_sha256
            assert recipe_import_matches(session,release,imported,mapping_version=manifest['mapping_version'])
            assert not recipe_import_matches(session,release,imported,mapping_version='not-the-selected-version')
            catalog = dict(session.execute(select(IngredientAlias.food_id,IngredientAlias.canonical_name).where(
                IngredientAlias.recipe_release_id == release.release_id)).all())
            mappings = mapping_plan(bundle,catalog)
            verify_installed(session,bundle,mappings)
            assert all(r['approval_status'] == 'pending_review' for r in mappings)
            assert publication.row_counts == {t:current['table_counts'][t] for t in publication.row_counts}
            missing = {field:session.scalar(select(func.count()).select_from(FoodNutrition).where(
                FoodNutrition.import_id == imported.import_id,getattr(FoodNutrition,field).is_(None)))
                for field in NUTRIENT_COLUMNS.values()}
            assert missing == bundle.nutrition.report['missing_nutrients']
            special = session.execute(select(Recipe.recipe_id,Recipe.original_servings,Recipe.servings_basis,Recipe.is_eligible)
                .where(Recipe.recipe_id.in_(['R0252','R0254']))).all()
            assert {(r.recipe_id,r.original_servings,r.servings_basis,r.is_eligible) for r in special} == {
                ('R0252',Decimal('950'),'portions',True),('R0254',Decimal('1'),'batch',True)}
            # Data access/scaling only: no cooking conversion or adoption approval.
            row = session.scalar(select(FoodNutrition).where(FoodNutrition.import_id == imported.import_id,
                FoodNutrition.food_id == 'FOOD000034',FoodNutrition.cooking_state == '未熟製'))
            scaled = calculate_food_nutrition(session,import_id=imported.import_id,food_id=row.food_id,
                cooking_state=row.cooking_state,cooking_method=row.cooking_method,weight_g=Decimal('100'),round_result=False)
            assert scaled['success'] and scaled['nutrition_unrounded']['energy_kcal'] == row.energy_kcal
            assert scaled['nutrition_unrounded']['protein_g'] == row.protein_g
            assert not scaled['cooking_conversion_applied']
            source_access = {'food_id':row.food_id,'weight_g':'100','energy_kcal':str(row.energy_kcal),
                'protein_g':str(row.protein_g),'cooking_conversion_applied':False,
                'meaning':'Source observation scaling only; not recipe cooking/adoption acceptance'}
            formal = versioned_sql_calculator(lambda:Session(bind=connection),recipe_release_id=release.release_id,
                nutrition_import_id=imported.import_id,mapping_version=manifest['mapping_version'])
            check = formal([(IngredientLine(food_id=row.food_id,name=catalog[row.food_id],
                grams_per_n=Decimal('100'),recipe_release_id=release.release_id,weight_basis='raw'),Decimal('100'))])[0]
            assert check.error and check.nutrients['energy_kcal'] is None and check.nutrients['protein_g'] is None
            pending = {'error':check.error,'energy_kcal':None,'protein_g':None,'legacy_fallback_used':False}
            for filename in ('.env','.env.dev'):
                config = dotenv_values(ROOT/filename)
                assert config['GENERATION_RECIPE_RELEASE_ID'] == release.release_id
                assert config['GENERATION_NUTRITION_IMPORT_ID'] == imported.import_id
                assert config['GENERATION_FOOD_MAPPING_VERSION'] == manifest['mapping_version']
        columns = inspect(connection).get_columns('nutrition_data_releases')
        schema = {'table':'nutrition_data_releases','columns':[{'name':c['name'],'type':str(c['type']),
            'nullable':c['nullable']} for c in columns], 'physical_tables':len(current['table_counts'])}
        connection.rollback()
    imported_result = json.loads((folder/'import-result.json').read_text(encoding='utf-8'))
    review = {'name_differences':imported_result['name_differences'],
              'recipe_food_ids_without_observations':imported_result['recipe_food_ids_without_observations'],
              'source_count_mismatches':imported_result['source_count_mismatches'],
              'all_candidate_mappings_require_review':True,'no_automatic_name_merges':True}
    result = {'database':'meal_planner_1102_v3','head':HEAD,'release_id':bundle.manifest['release_id'],
        'import_id':bundle.nutrition.import_id,'recipe_release_id':bundle.manifest['recipe_release_id'],
        **current,'non_t04_content_checks':unchanged,'t01_t02_runtime_contents_unchanged':True,
        'source_and_mapping_comparison':'PASS','name_difference_records':len(review['name_differences']),
        'recipe_food_ids':imported_result['recipe_food_ids'],
        'recipe_food_ids_with_observations':imported_result['recipe_food_ids_with_observations'],
        'recipe_food_ids_without_observations':review['recipe_food_ids_without_observations'],
        'candidate_mapping_count':len(mappings),'approved_mappings':0,'source_missing_nutrients':missing,
        'original_source_states':dict(Counter(r['cooking_state'] for r in bundle.nutrition.observations)),
        'historical_recipe_sources_imported_into_t01':False,'cooking_estimates_imported':False,
        'knowledge_imported':False,'local_config_pins':'PASS','read_only':True,
        'data_access_example':source_access,'formal_calculation_pending_review':pending,
        'special_recipe_portion_batch_semantics_preserved':True}
    for name,value in [('database-verification.json',result),('mapping-review.json',review),('schema.json',schema)]:
        (folder/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k not in {
        'table_digests','table_counts','non_t04_content_checks','original_source_states'}},ensure_ascii=True))
    engine.dispose()


if __name__ == '__main__':
    main()
