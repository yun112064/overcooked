"""Read-only export of all recipe occurrences for the 101 unresolved identities."""
from collections import Counter,defaultdict
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'backend')]
from sqlalchemy import create_engine,select,text,func
from sqlalchemy.orm import Session
from app.models import Recipe,RecipeIngredient
from app.models.nutrition import NutritionSource,NutritionFoodMapping
from app.services.meal_calculation import IngredientLine,versioned_sql_calculator
from app.services.t04_data_feedback import fingerprint
from scripts.t04_db_support import guard,snapshot


def main():
    from app.core.config import settings
    from dotenv import dotenv_values
    folder=ROOT/'docs/1102_V3/T04'
    source=folder/'remaining247-result.json';report=json.loads(source.read_text(encoding='utf-8'))
    items=[r for r in report['items'] if r['feedback_status']=='source_candidate_needs_confirmation']
    assert len(items)==len({r['food_id'] for r in items})==101
    assert settings.generation_recipe_release_id==report['recipe_release_id']
    assert settings.generation_nutrition_import_id==report['nutrition_import_id']
    engine=create_engine(settings.database_url);guard(engine)
    with engine.connect() as c:
        c.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        before=snapshot(c)
        with Session(bind=c) as db:
            recipes={r.recipe_id:r for r in db.scalars(select(Recipe).where(Recipe.recipe_release_id==report['recipe_release_id']))}
            all_lines=list(db.scalars(select(RecipeIngredient).where(RecipeIngredient.recipe_release_id==report['recipe_release_id']).order_by(RecipeIngredient.recipe_id,RecipeIngredient.sequence)))
            uses=defaultdict(list)
            for r in all_lines:
                recipe=recipes[r.recipe_id]
                uses[r.food_id].append({'recipe_id':r.recipe_id,'recipe_name':recipe.recipe_name,
                    'line_id':r.recipe_ingredient_id,'sequence':str(r.sequence),'raw_text':r.raw_text,
                    'source_url':recipe.source_url,'servings_raw':recipe.servings_raw,
                    'servings_basis':recipe.servings_basis,'whole_recipe_weight_g':str(r.weight_g) if r.weight_g is not None else None})
            for number,item in enumerate(items,1):
                item['number']=number;item['recipe_originals']=uses[item['food_id']]
                assert {r['recipe_id'] for r in item['recipe_originals']}==set(item['recipe_ids'])
                item['user_response']=None;item['response_scope']=None
                for s in item['new_source_candidates']:
                    actual=db.get(NutritionSource,s['source_id'])
                    assert actual and actual.import_id==report['nutrition_import_id']
                    assert (actual.raw_data.get('中文原名') or actual.raw_data.get('來源原文名稱'))==s['name']
            # Formal source gate only: actual whole-recipe pre-cooking masses,
            # not per-person totals or cooked-meal acceptance. All pending rows
            # fail before conversion; the READ ONLY transaction prevents writes.
            pairs=[(IngredientLine(r.food_id,r.canonical_name,None,recipe_ingredient_id=r.recipe_ingredient_id,
                weight_basis='raw',recipe_release_id=r.recipe_release_id),Decimal(r.weight_g)) for r in all_lines if r.weight_g is not None]
            calc=versioned_sql_calculator(lambda:Session(bind=c),recipe_release_id=report['recipe_release_id'],
                nutrition_import_id=report['nutrition_import_id'],mapping_version=settings.generation_food_mapping_version)
            results=calc(pairs)
            blocked=Counter(r.error for r in results)
            recipe_outcomes=defaultdict(list)
            for (line,_),result in zip(pairs,results):
                recipe_outcomes[next(r.recipe_id for r in all_lines if r.recipe_ingredient_id==line.recipe_ingredient_id)].append(result.error)
            review_statuses=dict(db.execute(select(NutritionFoodMapping.approval_status,func.count()).where(
                NutritionFoodMapping.recipe_release_id==report['recipe_release_id'],
                NutritionFoodMapping.import_id==report['nutrition_import_id'],
                NutritionFoodMapping.mapping_version==settings.generation_food_mapping_version).group_by(NutritionFoodMapping.approval_status)).all())
            formal={'recipe_count':len(recipes),'ingredient_lines':len(all_lines),'lines_checked':len(results),
                'line_results':dict(blocked),'recipes_with_all_source_gates_passed':sum(all(e is None for e in errors) for errors in recipe_outcomes.values()),
                'mapping_review_status_counts':review_statuses,
                'meaning':'Formal raw source gate only, not cooked nutrition or per-person calculation; no AI/model calls'}
            assert before==snapshot(c)
    pins=['GENERATION_RECIPE_RELEASE_ID','GENERATION_NUTRITION_IMPORT_ID','GENERATION_FOOD_MAPPING_VERSION']
    configs={name:{k:dotenv_values(ROOT/name).get(k) for k in pins} for name in ['.env','.env.dev']}
    resolved=subprocess.run(['docker','compose','config','--format','json'],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',check=True)
    compose=json.loads(resolved.stdout)
    configured={name:{k:compose['services'][name].get('environment',{}).get(k) for k in pins} for name in ['backend','worker']}
    assert configs['.env']==configs['.env.dev']
    assert all(v==configs['.env'] for v in configured.values())
    payload={'date':'2026-11-04','recipe_release_id':report['recipe_release_id'],
        'nutrition_import_id':report['nutrition_import_id'],'mapping_version':settings.generation_food_mapping_version,
        'source_report_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'items':items,
        'item_count':len(items),'recipe_occurrence_count':sum(len(r['recipe_originals']) for r in items),
        'all_38_tables_unchanged':True,'runtime_table_counts':before['table_counts'],'database_transaction':'REPEATABLE READ READ ONLY',
        'formal_source_gate_audit':formal,'configuration_pins':configs,'compose_service_pins':configured,
        'normal_startup_imports_nutrition':False,'normal_startup_inspection_only':True,
        'fresh_install_execution':'NOT_RUN; only existing V3 and entrypoint/config code inspected',
        'automatic_merge':False,'automatic_approval':False,'automatic_nutrient_fill':False}
    (folder/'confirm101-data.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in payload.items() if k not in {'items','configuration_pins','compose_service_pins'}},ensure_ascii=False))


if __name__=='__main__':main()
