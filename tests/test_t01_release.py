"""T01 release transaction and source-contract regression tests."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import hashlib
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select, func
from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError

from scripts.install_t01_release import prepare, install, MODELS
from app.db.base import Base
from app.models.recipe import RecipeDataRelease, Recipe, RecipeIngredient

SOURCE = Path(__file__).resolve().parents[1] / 'data/recipe_dataset_1103_V3.xlsx'


@pytest.fixture
def release():
    digest = hashlib.sha256(SOURCE.read_bytes()).hexdigest()
    bundle, identity = prepare(SOURCE, digest)
    return bundle, identity, digest


@pytest.fixture
def engine():
    engine = create_engine('sqlite://')
    @event.listens_for(engine, 'connect')
    def foreign_keys(connection, _):
        connection.execute('PRAGMA foreign_keys=ON')
    Base.metadata.create_all(engine, tables=[RecipeDataRelease.__table__,*[m.__table__ for m in MODELS]])
    yield engine
    engine.dispose()


def test_changed_workbook_hash_is_rejected():
    with pytest.raises(ValueError, match='hash'):
        prepare(SOURCE, '0'*64)


def test_all_workbook_grams_and_special_bases_are_preserved(release):
    bundle, _, _ = release
    assert all(r['weight_g'] is not None and abs(r['weight_g']-r['source_weight_g']) <= Decimal('0.0000005')
               and r['weight_basis']=='pre_cooking' for r in bundle.recipe_ingredients)
    by_id={r['recipe_id']:r for r in bundle.recipes}
    assert by_id['R0252']['original_servings']==950 and by_id['R0252']['is_eligible']
    assert by_id['R0254']['original_servings']==1 and by_id['R0254']['servings_basis']=='batch'
    assert by_id['R0254']['is_eligible']
    assert all(r['rights_status']=='unknown' for r in bundle.source_archive)


def test_install_is_idempotent_and_has_one_active_release(engine, release):
    bundle, identity, digest=release
    with Session(engine) as db, db.begin():
        assert install(db,bundle,identity,digest)['status']=='installed'
    with Session(engine) as db, db.begin():
        assert install(db,bundle,identity,digest)['status']=='unchanged'
        assert db.scalar(select(func.count()).select_from(Recipe))==293
        assert db.scalar(select(func.count()).select_from(RecipeDataRelease).where(RecipeDataRelease.active))==1


def test_foreign_key_failure_rolls_back_the_whole_release(engine, release):
    bundle, identity, digest=release
    lines=deepcopy(bundle.recipe_ingredients)
    lines[-1]['food_id']='FOOD_DOES_NOT_EXIST'
    bad=replace(bundle,recipe_ingredients=lines)
    with pytest.raises(IntegrityError):
        with Session(engine) as db, db.begin():
            install(db,bad,identity,digest)
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Recipe))==0
        assert db.scalar(select(func.count()).select_from(RecipeDataRelease))==0
