"""T04 營養來源回饋（DEMO 版）：未知值保持 None、不計算總量、未設定版本時在查詢前回 422。"""
import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://placeholder:unused@127.0.0.1:9/not_used")

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import uuid

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.recipe import RecipeDataRelease, SourceArchive, IngredientAlias, Recipe, RecipeIngredient
from app.models.nutrition import NutritionImport, NutritionSource, FoodNutrition, NutritionSourceLink, NutritionDataRelease
from app.models.nutrition_candidates import NutritionSourceCandidate, NutritionProductSource
from app.services.t04_data_feedback import build_candidates, recipe_feedback, fingerprint, COLUMNS, numeric


def test_exact_alias_matches_stay_pending_and_do_not_fuzzy_match():
    foods = [{'food_id': 'F1', 'canonical_name': '蒜頭', 'aliases': ['大蒜']},
             {'food_id': 'F2', 'canonical_name': '蒜苗', 'aliases': []}]
    source = {'source_id': 's1', 'provider': 'TFDA', 'import_id': 'i1', 'source_record_id': 'E1',
              'raw_data': {'中文原名': '大蒜平均值'}}
    rows = build_candidates(foods, [source], [], 'a' * 64, 'r1', 'i1')
    assert len(rows) == 1 and rows[0]['food_id'] == 'F1'
    assert rows[0]['review_status'] == 'pending_review'
    assert not rows[0]['match_evidence']['automatic_approval']


@pytest.mark.parametrize('value,expected', [(0, '0'), (None, None), ('', None), ('NaN', None), (-1, None), ('0.01', '0.01')])
def test_numeric_unknown_zero_and_label_values(value, expected):
    assert numeric(value) == expected


MODELS = [RecipeDataRelease, SourceArchive, IngredientAlias, Recipe, RecipeIngredient,
          NutritionImport, NutritionSource, FoodNutrition, NutritionSourceLink, NutritionDataRelease,
          NutritionSourceCandidate, NutritionProductSource]


@pytest.fixture
def db():
    e = create_engine('sqlite://')
    Base.metadata.create_all(e, tables=[m.__table__ for m in MODELS])
    with Session(e) as s:
        # 合成資料：服務本身仍要求發布版本為 active（見 P10 回報）
        s.add(RecipeDataRelease(release_id='r1', dataset_version='test', source_sha256='a' * 64,
            schema_version='test', source_kind='xlsx_import', active=True, row_counts={}))
        s.add(SourceArchive(raw_id='raw1', source_name='test', source_url='https://example.invalid',
            source_recipe_id='R1', recipe_name_raw='合成菜', servings_raw='2人份', ingredients_raw='測試',
            steps_raw='測試', tags_raw='', content_hash='b' * 64, import_batch_id='test'))
        s.add(IngredientAlias(food_id='F1', canonical_name='合成食材', aliases=[]))
        s.add(Recipe(recipe_id='R1', raw_id='raw1', standard_dish_id='D1', recipe_name='合成菜',
            normalized_name='合成菜', servings_raw='2人份', original_servings=2, servings_status='parsed',
            category='主菜', meal_type='dinner', cuisine='test', source_name='test',
            source_url='https://example.invalid'))
        s.add(RecipeIngredient(recipe_ingredient_id='L1', recipe_id='R1', sequence=1,
            raw_text='合成食材100g', canonical_name='合成食材', food_id='F1', quantity_status='test',
            unit_status='test', weight_g=100, weight_status='test'))
        s.add(NutritionImport(import_id='i1', source_files={}, report={}, imported_at=datetime.now(timezone.utc)))
        s.add(NutritionDataRelease(release_id='n1', import_id='i1', recipe_release_id='r1', recipe_source_sha256='a' * 64,
            mapping_version='m1', manifest_sha256='b' * 64, source_manifest={}, row_counts={},
            usage_status='observations_only_pending_review', active=True, imported_at=datetime.now(timezone.utc)))
        s.flush()
        yield s
    e.dispose()


def feedback(db):
    return recipe_feedback(db, ['R1'], recipe_release_id='r1', import_id='i1', mapping_version='m1')


def test_missing_source_never_becomes_zero_or_adoptable_and_carries_demo_provenance(db):
    r = feedback(db)
    line = r['ingredients'][0]
    assert line['status'] == 'source_missing'
    assert r['nutrition_totals'] is None and line['nutrition_totals'] is None and not r['adoptable']
    assert r['recipe_source'] == 'recipe_dataset_1027' and r['binding'] == 'food_id_only'
    assert r['versions']['recipe_release_id'] == 'r1'
    assert r['versions']['recipe_release_binding'] == 'evidence_only'
    assert line['weight_basis'] is None
    assert line['weight_semantics'] == 'workbook_weight_g_unverified'
    assert line['whole_recipe_pre_cooking_grams'] == '100.000000'
    assert r['recipes'][0]['servings_basis'] == 'portions'


def test_source_candidate_keeps_unknown_per100_values_none(db):
    source = NutritionSource(source_id='s1', import_id='i1', source_key='TFDA_TEST', provider='TFDA', source_record_id='T1',
        source_url='https://example.invalid', source_sheet='test', source_row=1,
        raw_data={'中文原名': '合成食材', '納入平均': '是', '熟製狀態': '未熟製', '熱量(kcal/100g)': 100, '蛋白質(g/100g)': 2, '糖(g/100g)': 0})
    db.add(source); db.flush()
    record = {c.name: getattr(source, c.name) for c in NutritionSource.__table__.columns}
    candidate = build_candidates([{'food_id': 'F1', 'canonical_name': '合成食材', 'aliases': []}], [record], [], 'b' * 64, 'r1', 'i1')[0]
    db.add(NutritionSourceCandidate(**candidate)); db.flush()
    r = feedback(db); line = r['ingredients'][0]
    assert line['status'] == 'source_available_pending_review' and line['nutrition_totals'] is None
    per100 = line['sources'][0]['per_100g']
    assert per100['sugars_g'] == '0'
    assert per100['dietary_fiber_g'] is None and per100['fat_g'] is None and per100['sodium_mg'] is None
    source.raw_data = {**source.raw_data, '蛋白質(g/100g)': 999}; db.flush()
    with pytest.raises(ValueError, match='NUTRITION_SOURCE_CHANGED'): feedback(db)


def test_product_label_keeps_fiber_unknown(db):
    label = {'values': {k: None for k in COLUMNS.values()}, 'label_sha256': 'c' * 64, 'source_url': 'https://example.invalid/product'}
    label['values'].update(energy_kcal='880', protein_g='0.01', fat_g='99.9', sugars_g='0')
    db.add(NutritionProductSource(product_source_id=fingerprint(label), recipe_release_id='r1', food_id='F1',
        source_name='合成產品', source_url=label['source_url'], evidence_sha256='c' * 64, per_100g=label['values'], provenance=label))
    db.flush(); r = feedback(db)
    assert not r['adoptable'] and r['nutrition_totals'] is None
    s = r['ingredients'][0]['sources'][0]
    assert s['provider'] == 'MANUFACTURER_LABEL' and s['per_100g']['dietary_fiber_g'] is None


def test_foreign_recipe_version_or_inactive_release_is_rejected(db):
    with pytest.raises(ValueError, match='RECIPE_NUTRITION_RELEASE_MISMATCH'):
        recipe_feedback(db, ['OTHER'], recipe_release_id='r1', import_id='i1', mapping_version='m1')
    with pytest.raises(ValueError, match='NUTRITION_VERSION_REQUIRED'):
        recipe_feedback(db, ['R1'], recipe_release_id='r1', import_id='i1', mapping_version='wrong')
    # 載入器以 active=False 寫入 bundle 的食譜發布版本，服務因而拒絕（需使用者決定綁定）
    db.get(RecipeDataRelease, 'r1').active = False; db.flush()
    with pytest.raises(ValueError, match='RECIPE_NUTRITION_RELEASE_MISMATCH'): feedback(db)


def _app(db_dependency):
    from fastapi import FastAPI
    from app.api import t04_data
    from app.api.errors import register_error_handlers
    app = FastAPI(); register_error_handlers(app); app.include_router(t04_data.router)
    app.dependency_overrides[t04_data.current_household_id] = lambda: uuid.UUID(int=1)
    app.dependency_overrides[t04_data.verify_origin] = lambda: None
    app.dependency_overrides[t04_data.get_db] = db_dependency
    return app, t04_data


@pytest.mark.parametrize('unset', [None, ''])
def test_unset_versions_answer_422_before_any_query(monkeypatch, unset):
    from fastapi.testclient import TestClient
    from app.core.config import settings
    for key in ('generation_recipe_release_id', 'generation_nutrition_import_id', 'generation_food_mapping_version'):
        monkeypatch.setattr(settings, key, unset)
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    executed = []
    event.listen(engine, 'before_cursor_execute', lambda *args: executed.append(args[2]))
    session = Session(engine)
    app, t04_data = _app(lambda: session)
    with patch.object(t04_data, 'recipe_feedback') as service, TestClient(app) as client:
        r = client.post('/meal-requests/00000000-0000-0000-0000-000000000002/t04-data',
                        json={'request_version': 1, 'recipe_ids': ['R1']})
    session.close(); engine.dispose()
    assert r.status_code == 422 and r.json()['error']['code'] == 'NUTRITION_VERSION_REQUIRED'
    assert executed == []
    service.assert_not_called()


@pytest.mark.parametrize('owned,version,code', [(False, 1, 404), (True, 2, 409), (True, 1, 200)])
def test_endpoint_checks_household_ownership_and_revision(monkeypatch, owned, version, code):
    from fastapi.testclient import TestClient
    from app.core.config import settings
    monkeypatch.setattr(settings, 'generation_recipe_release_id', 'r1')
    monkeypatch.setattr(settings, 'generation_nutrition_import_id', 'i1')
    monkeypatch.setattr(settings, 'generation_food_mapping_version', 'm1')
    fake = MagicMock(); fake.scalar.return_value = SimpleNamespace(version=1) if owned else None
    app, t04_data = _app(lambda: fake)
    with patch.object(t04_data, 'recipe_feedback', return_value={'adoptable': False, 'nutrition_totals': None}) as service:
        with TestClient(app) as client:
            r = client.post('/meal-requests/00000000-0000-0000-0000-000000000002/t04-data',
                            json={'request_version': version, 'recipe_ids': ['R1']})
    assert r.status_code == code
    if code != 200: service.assert_not_called()
    else: assert r.json()['nutrition_totals'] is None
