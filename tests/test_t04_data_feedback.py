from copy import deepcopy
from datetime import datetime,timezone
from types import SimpleNamespace
from unittest.mock import MagicMock,patch
import uuid

import pytest
from sqlalchemy import create_engine,select
from sqlalchemy.orm import Session
from app.db.base import Base
from app.models.recipe import RecipeDataRelease,SourceArchive,IngredientAlias,Recipe,RecipeIngredient,RecipeStep,Allergen
from app.models.nutrition import NutritionImport,NutritionSource,FoodNutrition,NutritionSourceLink,NutritionDataRelease
from app.models.nutrition_candidates import NutritionSourceCandidate,NutritionProductSource
from app.services.t04_data_feedback import build_candidates,recipe_feedback,fingerprint,COLUMNS,numeric


def test_exact_alias_matches_stay_pending_and_do_not_fuzzy_match():
    foods=[{'food_id':'F1','canonical_name':'蒜頭','aliases':['大蒜']},
           {'food_id':'F2','canonical_name':'蒜苗','aliases':[]}]
    source={'source_id':'s1','provider':'TFDA','import_id':'i1','source_record_id':'E1',
            'raw_data':{'中文原名':'大蒜平均值'}}
    rows=build_candidates(foods,[source],[],'a'*64,'r1','i1')
    assert len(rows)==1 and rows[0]['food_id']=='F1'
    assert rows[0]['review_status']=='pending_review'
    assert not rows[0]['match_evidence']['automatic_approval']
    assert rows==build_candidates(foods,[source],[],'a'*64,'r1','i1')


@pytest.mark.parametrize('value,expected',[(0,'0'),(None,None),('',None),('NaN',None),(-1,None),('0.01','0.01')])
def test_numeric_unknown_zero_and_label_values(value,expected):
    assert numeric(value)==expected


@pytest.fixture
def db():
    e=create_engine('sqlite://')
    models=[RecipeDataRelease,SourceArchive,IngredientAlias,Recipe,RecipeIngredient,RecipeStep,Allergen,
            NutritionImport,NutritionSource,FoodNutrition,NutritionSourceLink,NutritionDataRelease,
            NutritionSourceCandidate,NutritionProductSource]
    Base.metadata.create_all(e,tables=[m.__table__ for m in models])
    with Session(e) as s:
        s.add(RecipeDataRelease(release_id='r1',dataset_version='test',source_sha256='a'*64,
            schema_version='test',source_kind='xlsx_import',active=True,row_counts={}))
        s.add(SourceArchive(raw_id='raw1',recipe_release_id='r1',source_name='test',source_url='https://example.invalid',
            source_recipe_id='R1',recipe_name_raw='合成菜',servings_raw='2人份',ingredients_raw='測試',steps_raw='測試',
            tags_raw='',content_hash='b'*64,import_batch_id='test'))
        s.add(IngredientAlias(food_id='F1',recipe_release_id='r1',canonical_name='合成食材',aliases=[]))
        s.add(Recipe(recipe_id='R1',recipe_release_id='r1',raw_id='raw1',standard_dish_id='D1',recipe_name='合成菜',
            normalized_name='合成菜',servings_raw='2人份',original_servings=2,servings_basis='portions',
            servings_status='user_confirmed',category='主菜',meal_type='dinner',cuisine='test',source_name='test',
            source_url='https://example.invalid'))
        s.add(RecipeIngredient(recipe_ingredient_id='L1',recipe_release_id='r1',recipe_id='R1',sequence=1,
            raw_text='合成食材100g',canonical_name='合成食材',food_id='F1',quantity_status='test',unit_status='test',
            weight_g=100,weight_status='user_confirmed_workbook',weight_basis='pre_cooking',weight_evidence={}))
        s.add(NutritionImport(import_id='i1',source_files={},report={},imported_at=datetime.now(timezone.utc)))
        s.add(NutritionDataRelease(release_id='n1',import_id='i1',recipe_release_id='r1',recipe_source_sha256='a'*64,
            mapping_version='m1',manifest_sha256='b'*64,source_manifest={},row_counts={},
            usage_status='observations_only_pending_review',active=True,imported_at=datetime.now(timezone.utc)))
        s.flush()
        yield s
    e.dispose()


def feedback(db):
    return recipe_feedback(db,['R1'],recipe_release_id='r1',import_id='i1',mapping_version='m1')


def test_missing_source_never_becomes_zero_or_adoptable(db):
    r=feedback(db)
    assert r['ingredients'][0]['status']=='source_missing'
    assert r['nutrition_totals'] is None and not r['adoptable']
    assert r['ingredients'][0]['whole_recipe_pre_cooking_grams']=='100.000000'


def test_source_candidate_flows_to_feedback_without_calculating_totals(db):
    source=NutritionSource(source_id='s1',import_id='i1',source_key='TFDA_TEST',provider='TFDA',source_record_id='T1',
        source_url='https://example.invalid',source_sheet='test',source_row=1,
        raw_data={'中文原名':'合成食材','納入平均':'是','熟製狀態':'未熟製','熱量(kcal/100g)':100,'蛋白質(g/100g)':2,'糖(g/100g)':0})
    db.add(source);db.flush()
    record={c.name:getattr(source,c.name) for c in NutritionSource.__table__.columns}
    candidate=build_candidates([{'food_id':'F1','canonical_name':'合成食材','aliases':[]}],[record],[],'b'*64,'r1','i1')[0]
    db.add(NutritionSourceCandidate(**candidate));db.flush()
    r=feedback(db);line=r['ingredients'][0]
    assert line['status']=='source_available_pending_review' and line['nutrition_totals'] is None
    assert line['sources'][0]['per_100g']['sugars_g']=='0'
    assert line['sources'][0]['per_100g']['dietary_fiber_g'] is None
    source.raw_data={**source.raw_data,'蛋白質(g/100g)':999};db.flush()
    with pytest.raises(ValueError,match='NUTRITION_SOURCE_CHANGED'): feedback(db)


def test_product_label_keeps_fiber_unknown_and_is_not_cooking_output(db):
    label={'values':{k:None for k in COLUMNS.values()},'label_sha256':'c'*64,'source_url':'https://example.invalid/product'}
    label['values'].update(energy_kcal='880',protein_g='0.01',fat_g='99.9',sugars_g='0')
    db.add(NutritionProductSource(product_source_id=fingerprint(label),recipe_release_id='r1',food_id='F1',
        source_name='合成產品',source_url=label['source_url'],evidence_sha256='c'*64,per_100g=label['values'],provenance=label))
    db.flush();r=feedback(db)
    assert not r['adoptable'] and r['nutrition_totals'] is None
    s=r['ingredients'][0]['sources'][0]
    assert s['provider']=='MANUFACTURER_LABEL' and s['per_100g']['protein_g']=='0.01'
    assert s['per_100g']['dietary_fiber_g'] is None


def test_foreign_recipe_or_version_is_rejected(db):
    with pytest.raises(ValueError,match='RECIPE_NUTRITION_RELEASE_MISMATCH'):
        recipe_feedback(db,['OTHER'],recipe_release_id='r1',import_id='i1',mapping_version='m1')
    with pytest.raises(ValueError,match='NUTRITION_VERSION_REQUIRED'):
        recipe_feedback(db,['R1'],recipe_release_id='r1',import_id='i1',mapping_version='wrong')


def test_data_feedback_requires_authentication():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as client:
        response=client.post('/meal-requests/00000000-0000-0000-0000-000000000001/t04-data',
            json={'request_version':1,'recipe_ids':['R1']},headers={'Origin':'http://localhost:8080'})
    assert response.status_code==401


@pytest.mark.parametrize('owned,version,code',[(False,1,404),(True,2,409),(True,1,200)])
def test_endpoint_checks_household_ownership_and_revision(owned,version,code):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api import t04_data
    from app.api.errors import register_error_handlers
    app=FastAPI();register_error_handlers(app);app.include_router(t04_data.router)
    fake=MagicMock();fake.scalar.return_value=SimpleNamespace(version=1) if owned else None
    app.dependency_overrides[t04_data.current_household_id]=lambda:uuid.UUID(int=1)
    app.dependency_overrides[t04_data.verify_origin]=lambda:None
    app.dependency_overrides[t04_data.get_db]=lambda:fake
    with patch.object(t04_data,'recipe_feedback',return_value={'adoptable':False,'nutrition_totals':None}) as service:
        with TestClient(app) as client:
            r=client.post('/meal-requests/00000000-0000-0000-0000-000000000002/t04-data',json={'request_version':version,'recipe_ids':['R1']})
    assert r.status_code==code
    if code!=200: service.assert_not_called()
    else: assert r.json()['nutrition_totals'] is None
