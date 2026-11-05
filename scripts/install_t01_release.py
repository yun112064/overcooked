"""Install the explicitly selected T01 workbook into the dedicated V3 database."""
from pathlib import Path
import sys
import argparse
import hashlib
import json
import re
from decimal import Decimal, ROUND_HALF_EVEN
from dataclasses import replace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT)]
from sqlalchemy import create_engine, select, delete, func, text
from sqlalchemy.orm import Session
from app.models.recipe import RecipeDataRelease, SourceArchive, Recipe, RecipeStep, RecipeIngredient, IngredientAlias, Allergen
from app.services.recipe_import import normalize_source_name, parse_recipe_workbook

MODELS = [SourceArchive, IngredientAlias, Allergen, Recipe, RecipeStep, RecipeIngredient]


def verify_rows(session, bundle):
    for model in MODELS:
        key = list(model.__table__.primary_key.columns)[0].name
        actual = {getattr(r,key):r for r in session.scalars(select(model))}
        for expected in getattr(bundle,model.__tablename__):
            row = actual.get(expected[key])
            if row is None:
                raise ValueError('Missing imported row: ' + str(expected[key]))
            for field,value in expected.items():
                scale = getattr(model.__table__.c[field].type,'scale',None)
                if isinstance(value,Decimal) and scale is not None:
                    value = value.quantize(Decimal(1).scaleb(-scale),rounding=ROUND_HALF_EVEN)
                if getattr(row,field) != value:
                    raise ValueError('Imported value mismatch: '+model.__tablename__+'.'+str(expected[key])+'.'+field)


def prepare(path, expected_hash):
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected_hash:
        raise ValueError('Workbook changed: expected hash does not match')
    bundle = parse_recipe_workbook(path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected_hash:
        raise ValueError('Workbook changed while parsing')
    names = [normalize_source_name(r['recipe_name']) for r in bundle.recipes]
    if len(names) != len(set(names)):
        raise ValueError('Duplicate normalized recipe names require selection')
    release = 'recipe-1103-v3-' + expected_hash[:12]
    for field in ('source_archive','ingredient_aliases','allergens','recipes','recipe_steps','recipe_ingredients'):
        for row in getattr(bundle, field):
            row['recipe_release_id'] = release
            if 'review_status' in row:
                row['review_status'] = 'user_selected'
    for row in bundle.recipes:
        match = re.match(r'\s*(\d+(?:\.\d+)?)', row['servings_raw'])
        if not match or Decimal(match[1]) <= 0:
            raise ValueError('Invalid serving basis: ' + row['recipe_id'])
        row['original_servings'] = Decimal(match[1])
        row['servings_basis'] = 'batch' if '批' in row['servings_raw'] else 'portions'
        row['servings_status'] = 'user_confirmed'
        row['is_eligible'] = True
        row['quality_status'] = 'user_selected'
        # Selection and structural verification do not invent license or nutrition approvals.
        row['quality_issues'] = sorted(set(row['quality_issues']) | {'formal_source_acceptance_not_certified'})
    for row in bundle.recipe_ingredients:
        grams = row['source_weight_g']
        if grams is None or not grams.is_finite() or grams <= 0:
            raise ValueError('Missing positive workbook grams: ' + row['recipe_ingredient_id'])
        row['weight_g'] = grams.quantize(Decimal('0.000001'), rounding=ROUND_HALF_EVEN)
        row['weight_status'] = 'user_confirmed_workbook'
        row['weight_basis'] = 'pre_cooking'
        row['weight_evidence'] = {'workbook_sha256':expected_hash,
            'source_weight_g':str(grams), 'basis':'whole_source_recipe_before_cooking',
            'confirmed_by':'user', 'decision_date':'2026-11-03',
            'source_nutrients_basis':'workbook_line_values_not_per_100g'}
        row['is_required'] = True
        row['quality_issues'] = [i for i in row['quality_issues'] if i != 'source_weight_requires_conversion_evidence']
    return bundle, release


def install(session, bundle, release, source_hash):
    if session.bind.dialect.name == 'postgresql':
        if session.scalar(text('select current_database()')) != 'meal_planner_1102_v3':
            raise ValueError('Writes are restricted to meal_planner_1102_v3')
        session.execute(text("select pg_advisory_xact_lock(hashtextextended('overcooked:t01:release',0))"))
    existing = session.get(RecipeDataRelease, release)
    counts = {m.__tablename__:len(getattr(bundle,m.__tablename__)) for m in MODELS}
    if existing:
        actual = {m.__tablename__:session.scalar(select(func.count()).select_from(m)) for m in MODELS}
        if existing.source_sha256 != source_hash or actual != counts or not existing.active:
            raise ValueError('Existing release differs from the expected contents')
        verify_rows(session,bundle)
        return {'status':'unchanged','release_id':release,'counts':actual}
    if any(session.scalar(select(func.count()).select_from(m)) for m in MODELS):
        raise ValueError('Dedicated V3 database is not empty; refusing unscoped replacement')
    # Only remove the empty migration placeholder, never a V1 database or populated release.
    placeholders = session.scalars(select(RecipeDataRelease)).all()
    if any(r.release_id != 'legacy-1017' or r.active for r in placeholders):
        raise ValueError('Unexpected existing release; refusing replacement')
    for r in placeholders:
        session.delete(r)
    session.flush()
    session.add(RecipeDataRelease(release_id=release,dataset_version='1103_V3',source_sha256=source_hash,
        schema_version='t01-xlsx-release-v1',source_kind='xlsx_import',
        source_uri='data/recipe_dataset_1103_V3.xlsx',row_counts=counts,active=False))
    session.flush()
    for model in MODELS:
        session.add_all(model(**r) for r in getattr(bundle,model.__tablename__))
        session.flush()
    session.get(RecipeDataRelease,release).active = True
    session.flush()
    actual = {m.__tablename__:session.scalar(select(func.count()).select_from(m)) for m in MODELS}
    if actual != counts:
        raise ValueError('Imported row counts do not match source')
    verify_rows(session,bundle)
    return {'status':'installed','release_id':release,'counts':actual}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workbook',type=Path,required=True)
    parser.add_argument('--sha256',required=True)
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    from app.core.config import settings
    engine=create_engine(settings.database_url)
    if engine.url.get_backend_name()!='postgresql' or engine.url.database!='meal_planner_1102_v3':
        raise ValueError('Only the dedicated V3 PostgreSQL database is authorized')
    bundle,release=prepare(args.workbook,args.sha256)
    with Session(engine) as session, session.begin():
        result=install(session,bundle,release,args.sha256)
    result['workbook_sha256']=args.sha256
    args.report.parent.mkdir(parents=True,exist_ok=True)
    args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result,ensure_ascii=True))


if __name__=='__main__':
    main()
