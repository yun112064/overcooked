"""Read-only source readiness verification for current recipes after supplementation."""
from collections import Counter,defaultdict
import argparse
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'backend')]
from sqlalchemy import create_engine,select,text
from sqlalchemy.orm import Session
from app.models import Recipe
from app.models.nutrition_candidates import NutritionSourceCandidate,NutritionProductSource
from app.services.t04_data_feedback import recipe_feedback,COLUMNS
from scripts.t04_db_support import guard,snapshot,HEAD


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--report-prefix',default='supplement-current')
    args=parser.parse_args()
    if Path(args.report_prefix).name!=args.report_prefix: raise ValueError('Report prefix must be a filename')
    from app.core.config import settings
    engine=create_engine(settings.database_url);guard(engine)
    folder=ROOT/'docs/1102_V3/T04'
    before=json.loads((folder/'supplement-before.json').read_text())
    with Session(engine) as db:
        db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        initial=snapshot(db.connection())
        assert db.scalar(text('SELECT version_num FROM alembic_version'))==HEAD
        assert all(initial['table_digests'][t]==h for t,h in before['table_digests'].items() if t!='alembic_version')
        ids=list(db.scalars(select(Recipe.recipe_id).where(Recipe.recipe_release_id==settings.generation_recipe_release_id).order_by(Recipe.recipe_id)))
        foods={};uses=defaultdict(set);recipe_stats=Counter()
        for offset in range(0,len(ids),10):
            result=recipe_feedback(db,ids[offset:offset+10],recipe_release_id=settings.generation_recipe_release_id,
                import_id=settings.generation_nutrition_import_id,mapping_version=settings.generation_food_mapping_version)
            assert result['adoptable'] is False and result['nutrition_totals'] is None
            byrecipe=defaultdict(list)
            for line in result['ingredients']:
                sources=[s for s in line['sources'] if s['provider'] in {'TFDA','USDA','MANUFACTURER_LABEL'}]
                complete=[s for s in sources if s['included_in_original_aggregate'] is not False and all(s['per_100g'][f] is not None for f in ('energy_kcal','protein_g'))]
                foods[line['food_id']]={'food_id':line['food_id'],'name':line['name'],'status':line['status'],
                    'sources':[{'source_id':s['source_id'],'name':s['name'],'provider':s['provider'],'state':s['state'],
                        'included_in_original_aggregate':s['included_in_original_aggregate'],
                        'missing_fields':[f for f in COLUMNS.values() if s['per_100g'][f] is None]} for s in sources],
                    'required_source_count':sum(not s.get('confirmation_required',False) for s in complete),
                    'candidate_source_count':len(complete)}
                uses[line['food_id']].add(line['recipe_id']);byrecipe[line['recipe_id']].append(line['required_values_available'])
            for lines in byrecipe.values(): recipe_stats['all_ingredients_have_candidate' if all(lines) else 'missing_required_source']+=1
        for fid,row in foods.items(): row['recipe_ids']=sorted(uses[fid])
        amf=recipe_feedback(db,['R0207'],recipe_release_id=settings.generation_recipe_release_id,
            import_id=settings.generation_nutrition_import_id,mapping_version=settings.generation_food_mapping_version)
        amf_lines=[r for r in amf['ingredients'] if r['food_id']=='FOOD001993']
        assert len(amf_lines)==2
        assert {r['whole_recipe_pre_cooking_grams'] for r in amf_lines}=={'52.500000','66.000000'}
        for line in amf_lines:
            assert len(line['sources'])==1
            s=line['sources'][0];assert s['provider']=='MANUFACTURER_LABEL'
            assert s['per_100g']['protein_g']=='0.01' and s['per_100g']['dietary_fiber_g'] is None
        candidates=list(db.scalars(select(NutritionSourceCandidate)))
        assert candidates and all(r.review_status=='pending_review' for r in candidates)
        assert initial==snapshot(db.connection())
        report={'head':HEAD,'food_count':len(foods),'recipe_count':len(ids),
            'food_statuses':dict(Counter(r['status'] for r in foods.values())),
            'recipe_candidate_coverage':dict(recipe_stats),'additional_candidates':len(candidates),
            'product_sources':initial['table_counts']['nutrition_product_sources'],
            'all_candidates_pending':True,'formal_nutrition_totals_calculated':False,
            'all_original_tables_unchanged':True,'read_only_check_unchanged':True,
            'foods':[foods[k] for k in sorted(foods)]}
        (folder/(args.report_prefix+'-verification.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        (folder/(args.report_prefix+'-amf-feedback.json')).write_text(json.dumps(amf,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
        print(json.dumps({k:v for k,v in report.items() if k!='foods'},ensure_ascii=False))


if __name__=='__main__': main()
