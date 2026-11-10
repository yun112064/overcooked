"""Checkout grants use synthetic SQLite and never contact the real store or V3."""
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.dialects.sqlite.base import SQLiteTypeCompiler
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool
from sqlalchemy.schema import CreateTable

from app.api import sim_market
from app.api.deps import current_auth, current_household_id
from app.api.errors import register_error_handlers
from app.core.config import settings
from app.db.models import User, UserSession, Household, HouseholdMembership, AdoptedMealPlan
from app.db.session import get_db
from app.services import market_handoff

ORIGIN = 'http://localhost:5174'


@pytest.fixture
def checkout_env(monkeypatch):
    monkeypatch.setattr(settings, 'sim_market_url', ORIGIN)
    monkeypatch.setattr(settings, 'sim_market_handoff_key', 'test-only-key-not-for-deployment-123456789')
    monkeypatch.setattr(SQLiteTypeCompiler, 'visit_JSONB', SQLiteTypeCompiler.visit_JSON, raising=False)
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    user, household, plan_id = uuid4(), uuid4(), uuid4()
    session_hash = 'a' * 64
    with Session(engine) as db, db.begin():
        for model in (User, Household, HouseholdMembership, UserSession, AdoptedMealPlan):
            ddl = str(CreateTable(model.__table__).compile(dialect=engine.dialect)).replace('::jsonb', '')
            db.connection().exec_driver_sql(ddl)
        db.add(User(id=user, email='synthetic@example.invalid', password_hash='fixture'))
        db.add(Household(id=household, name='Private household'))
        db.add(HouseholdMembership(user_id=user, household_id=household))
        db.add(UserSession(token_hash=session_hash, user_id=user, household_id=household,
                           expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
        db.add(AdoptedMealPlan(id=plan_id, household_id=household, meal_date=date(2026, 11, 10),
                              meal_type='dinner', mode='database_menu', status='adopted', version=1,
                              dishes=[{'dish_name': '合成牛奶料理', 'private_note': 'not exported'}],
                              snapshot={'nutrition': {'private_member': 123}, 'request': {'budget': 200},
                                        'market_shortages': {'recipe_release_id': 'release-test', 'items': [
                                            {'food_id': 'milk', 'name': '牛奶', 'shortage_grams': None,
                                             'shortage_ml': '100/3'}], 'unquantified_items': []}}))
    app = FastAPI()
    register_error_handlers(app)
    app.include_router(sim_market.router)
    def session():
        with Session(engine) as db:
            yield db
    app.dependency_overrides[get_db] = session
    app.dependency_overrides[current_auth] = lambda: SimpleNamespace(
        user_id=user, session=SimpleNamespace(token_hash=session_hash))
    app.dependency_overrides[current_household_id] = lambda: household
    monkeypatch.setattr(sim_market, 'get_catalog', lambda db: {'recipe_release_id': 'release-test', 'products': [
        {'food_id': 'milk', 'name': '牛奶', 'category': '豆蛋乳品', 'package_grams': None,
         'package_ml': 500, 'price_cents': 15000}]})
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, app=app, engine=engine, plan_id=plan_id,
                              user=user, household=household, session_hash=session_hash)
    engine.dispose()


def issue(env):
    return env.client.post('/sim-market/handoffs', json={'plan_id': str(env.plan_id)},
                           headers={'origin': 'http://localhost:8080'})


def grant(env):
    response = issue(env)
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    url = response.json()['checkout_url']
    assert url.startswith(ORIGIN + '/checkout#handoff=') and '?' not in url
    return url.split('#handoff=')[1]


def redeem(env, token):
    return env.client.post('/sim-market/checkout', json={'token': token}, headers={'origin': ORIGIN})


def test_grant_exports_only_shopping_data_and_preserves_snapshot(checkout_env):
    env = checkout_env
    with Session(env.engine) as db:
        before = json.dumps(db.get(AdoptedMealPlan, env.plan_id).snapshot, sort_keys=True)
    response = redeem(env, grant(env))
    assert response.status_code == 200, response.text
    assert response.headers['cache-control'] == 'no-store'
    body = response.json()
    assert body['plan']['snapshot']['market_shortages']['items'][0]['shortage_ml'] == '100/3'
    assert body['catalog']['products'][0]['price_cents'] == 15000
    assert body['plan']['dishes'] == [{'dish_name': '合成牛奶料理'}]
    assert not any(word in response.text for word in ['nutrition', 'private_member', 'private_note', 'budget',
                                                     'Private household', env.session_hash, str(env.household)])
    with Session(env.engine) as db:
        assert json.dumps(db.get(AdoptedMealPlan, env.plan_id).snapshot, sort_keys=True) == before


@pytest.mark.parametrize('action', ['expired', 'tampered', 'logout', 'switched', 'removed_member', 'superseded'])
def test_checkout_rechecks_grant_and_authorization(checkout_env, monkeypatch, action):
    env = checkout_env
    token = grant(env)
    if action == 'expired':
        current = market_handoff.time.time()
        monkeypatch.setattr(market_handoff.time, 'time', lambda: current + 601)
    elif action == 'tampered':
        token = token[:-1] + ('0' if token[-1] != '0' else '1')
    else:
        with Session(env.engine) as db, db.begin():
            if action == 'logout': db.delete(db.get(UserSession, env.session_hash))
            elif action == 'switched': db.get(UserSession, env.session_hash).household_id = uuid4()
            elif action == 'removed_member': db.query(HouseholdMembership).delete()
            else: db.get(AdoptedMealPlan, env.plan_id).status = 'superseded'
    assert redeem(env, token).status_code in (409, 410)


def test_issue_requires_login_household_and_trusted_origin(checkout_env):
    env = checkout_env
    assert env.client.post('/sim-market/handoffs', json={'plan_id': str(env.plan_id)}).status_code == 403
    env.app.dependency_overrides[current_household_id] = uuid4
    assert issue(env).status_code == 404
    env.app.dependency_overrides.pop(current_auth)
    assert issue(env).status_code == 401


@pytest.mark.parametrize('token', ['bad', 'a.b', 'a.' + 'é' * 64, 'W10.' + 'a' * 64])
def test_invalid_tokens_fail_without_server_error(checkout_env, token):
    assert redeem(checkout_env, token).status_code == 410


def test_checkout_rejects_other_origin(checkout_env):
    env = checkout_env
    token = grant(env)
    assert env.client.post('/sim-market/checkout', json={'token': token},
                           headers={'origin': 'https://untrusted.invalid'}).status_code == 403


def test_missing_configuration_and_snapshot_are_explicit(checkout_env, monkeypatch):
    env = checkout_env
    with Session(env.engine) as db, db.begin(): db.get(AdoptedMealPlan, env.plan_id).snapshot = {}
    assert issue(env).status_code == 409
    monkeypatch.setattr(settings, 'sim_market_handoff_key', None)
    assert issue(env).status_code == 503


def test_generated_snapshot_keeps_release_and_unknown_quantity(checkout_env):
    env = checkout_env
    with Session(env.engine) as db, db.begin():
        db.get(AdoptedMealPlan, env.plan_id).snapshot = {'result': {
            'dishes': [{'recipe_release_id': 'original-release'}],
            'calculation': {'ingredients': [{'food_id': 'milk', 'name': '牛奶'}]},
            'cost': {'status': 'computed', 'lines': [{'food_id': 'milk', 'shortage_grams': None,
                     'shortage_ml': '500.000001', 'price_status': 'unknown'}], 'unquantified_food_ids': ['unknown']}}}
    body = redeem(env, grant(env)).json()['plan']
    assert body['snapshot']['result']['dishes'] == [{'recipe_release_id': 'original-release'}]
    assert body['prices']['items'][0]['shortage_ml'] == '500.000001'
    assert body['prices']['unquantified_items'][0]['food_id'] == 'unknown'
