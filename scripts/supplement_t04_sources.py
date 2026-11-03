"""Supplement source relationships/product evidence on V3, preserving original observations."""
import argparse
import hashlib
import json
import sys
from pathlib import Path
from collections import Counter

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'backend')]
from sqlalchemy import create_engine,select,text
from sqlalchemy.orm import Session
from app.models import IngredientAlias,RecipeIngredient,RecipeDataRelease
from app.models.nutrition import NutritionSource,NutritionDataRelease
from app.models.nutrition_candidates import NutritionSourceCandidate,NutritionProductSource
from app.services.t04_data_feedback import build_candidates,fingerprint,numeric,COLUMNS
from scripts.t04_db_support import guard,snapshot

TABLES={'nutrition_source_candidates','nutrition_product_sources'}
HEAD='d1103d04b001'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply',action='store_true')
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    from app.core.config import settings
    engine=create_engine(settings.database_url);guard(engine)
    legacy_path=ROOT/'nutrition_model/data/ingredient_master.json'
    legacy_bytes=legacy_path.read_bytes();legacy=json.loads(legacy_bytes)
    label_path=ROOT/'data/nutrition/t04-additions-20261103/amf-product-source.json'
    label=json.loads(label_path.read_text(encoding='utf-8'))
    image=(label_path.parent/label['label_file']).read_bytes()
    if hashlib.sha256(image).hexdigest()!=label['label_sha256']: raise ValueError('Product label evidence hash mismatch')
    if label['food_id']!='FOOD001993' or label['basis']!='per_100g_as_sold': raise ValueError('Unexpected product source')
    values={f:numeric(label['values'].get(f)) for f in COLUMNS.values()}
    if any(values[f] is None for f in ('energy_kcal','protein_g')): raise ValueError('Product label lacks required nutrients')
    with Session(engine) as db,db.begin() as transaction:
        if db.scalar(text('SELECT version_num FROM alembic_version'))!=HEAD: raise ValueError('Apply T04 supplementary schema first')
        db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended('overcooked:t04:sources',0))"))
        before=snapshot(db.connection(),exclude=TABLES)
        recipe=db.get(RecipeDataRelease,settings.generation_recipe_release_id)
        if not recipe or not recipe.active or recipe.source_sha256!=hashlib.sha256((ROOT/'data/recipe_dataset_1103_V3.xlsx').read_bytes()).hexdigest():
            raise ValueError('Current recipe source/version mismatch')
        publication=db.scalar(select(NutritionDataRelease).where(NutritionDataRelease.recipe_release_id==recipe.release_id,
            NutritionDataRelease.import_id==settings.generation_nutrition_import_id,
            NutritionDataRelease.mapping_version==settings.generation_food_mapping_version,NutritionDataRelease.active.is_(True)))
        if not publication: raise ValueError('Current nutrition release mismatch')
        used=set(db.scalars(select(RecipeIngredient.food_id).where(RecipeIngredient.recipe_release_id==recipe.release_id)))
        catalog=[dict(r) for r in db.execute(select(IngredientAlias.__table__).where(
            IngredientAlias.recipe_release_id==recipe.release_id,IngredientAlias.food_id.in_(used))).mappings()]
        sources=[dict(r) for r in db.execute(select(NutritionSource.__table__).where(
            NutritionSource.import_id==publication.import_id,NutritionSource.provider=='TFDA')).mappings()]
        rows=build_candidates(catalog,sources,legacy,hashlib.sha256(legacy_bytes).hexdigest(),recipe.release_id,publication.import_id)
        assert label['recipe_release_id']==recipe.release_id and label['food_id'] in used
        created=0
        for row in rows:
            existing=db.get(NutritionSourceCandidate,row['candidate_id'])
            if existing:
                if any(getattr(existing,k)!=v for k,v in row.items()): raise ValueError('Existing candidate differs')
            else: db.add(NutritionSourceCandidate(**row));created+=1
        product={'product_source_id':fingerprint(label),'recipe_release_id':recipe.release_id,'food_id':label['food_id'],
            'source_name':label['product_name'],'source_url':label['source_url'],
            'evidence_sha256':label['label_sha256'],'per_100g':values,'provenance':label}
        existing=db.get(NutritionProductSource,product['product_source_id'])
        product_created=existing is None
        if existing:
            if any(getattr(existing,k)!=v for k,v in product.items()): raise ValueError('Existing product source differs')
        else: db.add(NutritionProductSource(**product))
        db.flush()
        if snapshot(db.connection(),exclude=TABLES)!=before: raise ValueError('Original source/recipe/price/runtime contents changed')
        report={'recipe_release_id':recipe.release_id,'nutrition_import_id':publication.import_id,
            'candidate_count':len(rows),'candidate_food_ids':len({r['food_id'] for r in rows}),
            'match_kinds':dict(Counter(m['kind'] for r in rows for m in r['match_evidence']['matches'])),
            'created_candidates':created,'created_product_source':product_created,'product_source_id':product['product_source_id'],
            'product_values_per_100g':values,'all_candidates_pending_review':True,'original_tables_unchanged':True,
            'original_table_digests':before['table_digests'],'candidate_foods':sorted({r['food_id'] for r in rows}),
            'committed':args.apply}
        if not args.apply: transaction.rollback()
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in {'original_table_digests','candidate_foods'}},ensure_ascii=False))
    engine.dispose()


if __name__=='__main__': main()
