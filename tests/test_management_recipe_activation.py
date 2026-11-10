"""T01 publication through real HTTP routes and the generation worker on disposable PostgreSQL."""
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest
import sqlalchemy as sa
from fastapi import Request
from fastapi.testclient import TestClient

from app.core.config import settings
from app.db.models import AdoptedMealPlan, ConversionFactorEstimate, GenerationResult
from app.db.session import get_db
from app.main import app
from app.management.active_recipe_catalog import BASELINE
from app.management.models import tables
from app.models import RecipeDataRelease, RecipeStep
from app.models.nutrition import FoodNutrition, NutritionFoodMapping, NutritionImport, NutritionSource, NutritionSourceLink, T04CookingReceipt
from db_support import ORIGIN, TEST_DATABASE_URL
from generation_support import GenerationTestCase, PASSWORD
from test_t11_recipe_retrieval import CABBAGE, GARLIC

DB_NAME = 'meal_planner_1102_v3_recipe_activation_test'
MARKER = 'overcooked:disposable:recipe-activation'
API = '/admin/v1'
BINDING = API + '/bindings/recipe_catalog'
NUTRITION_IMPORT = 'synthetic-recipe-activation-nutrition'
MAPPING_VERSION = 'synthetic-recipe-activation-mapping'


class RecipeActivationTest(GenerationTestCase):
    @classmethod
    def setUpClass(cls):
        url = sa.engine.make_url(TEST_DATABASE_URL)
        if url.get_backend_name() != 'postgresql' or url.host != '127.0.0.1' or url.database != DB_NAME:
            pytest.skip('Use a dedicated loopback recipe activation database')
        engine = sa.create_engine(url)
        try:
            with engine.connect() as db:
                assert db.scalar(sa.text('SELECT current_database()')) == DB_NAME
                assert db.scalar(sa.text(
                    "SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()"
                )) == MARKER
        finally:
            engine.dispose()
        super().setUpClass()

    def test_review_publish_activate_generate_and_rollback(self):
        old_override = app.dependency_overrides[get_db]
        self.addCleanup(app.dependency_overrides.__setitem__, get_db, old_override)
        with self.factory() as db, db.begin():
            db.add(RecipeStep(step_id='R02-S1', recipe_id='R02', step_no=1, instruction_clean='將高麗菜炒熟。'))
            db.execute(sa.text('CREATE ROLE recipe_activation_app NOLOGIN'))
            db.execute(sa.text('GRANT USAGE ON SCHEMA public TO recipe_activation_app'))
            db.execute(sa.text('GRANT SELECT ON ALL TABLES IN SCHEMA public TO recipe_activation_app'))
            for name in tables:
                db.execute(sa.text(f'GRANT INSERT ON management_{name} TO recipe_activation_app'))
            for name in ('access_state', 'active_bindings', 'commands', 'proposals', 'tasks'):
                db.execute(sa.text(f'GRANT UPDATE ON management_{name} TO recipe_activation_app'))
            self.seed_nutrition(db)

        def scoped_db(request: Request):
            with self.factory() as db:
                if request.url.path.startswith(API + '/'):
                    db.execute(sa.text('SET LOCAL ROLE recipe_activation_app'))
                yield db

        app.dependency_overrides[get_db] = scoped_db
        with patch.multiple(settings, management_enabled=True, management_recipe_catalog_enabled=True,
                            generation_recipe_release_id='legacy-1017',
                            generation_nutrition_import_id=NUTRITION_IMPORT, generation_food_mapping_version=MAPPING_VERSION):
            editor = self.admin_client('editor', ['recipe_editor'])
            reviewer = self.admin_client('reviewer', ['recipe_reviewer', 'source_reviewer'])
            publisher = self.admin_client('publisher', ['publisher'])

            old_result = self.query_and_generate('清炒高麗菜', 'R02', '將高麗菜炒熟。', '50')
            old_snapshot = old_result.payload
            first = self.publish(editor, reviewer, publisher, '家常蒜香高麗菜', '120', '將高麗菜與大蒜拌炒至熟。')
            self.query_and_generate('清炒高麗菜', 'R02', '將高麗菜炒熟。', '50')

            plan = self.plan(publisher, first, 1)
            command = self.activation_body(plan, first)
            assert self.post(editor, BINDING + '/activate', command, 403)['error']['code'] == 'FORBIDDEN'
            assert self.post(publisher, BINDING + '/activate', command)['revision'] == 2
            first_result = self.query_and_generate('家常蒜香高麗菜', 't01v:', '將高麗菜與大蒜拌炒至熟。', '60', first)

            second = self.publish(editor, reviewer, publisher, '清香蒜炒高麗菜', '140', '將高麗菜炒熟後拌入大蒜。', base=first)
            assert self.post(publisher, BINDING + '/activate', command, 409)['error']['code'] == 'BINDING_CHANGED'
            assert self.post(publisher, BINDING + '/activate', self.activation_body(self.plan(publisher, second, 2), second))['revision'] == 3
            second_result = self.query_and_generate('清香蒜炒高麗菜', 't01v:', '將高麗菜炒熟後拌入大蒜。', '70', second)
            assert first_result.payload['dishes'][0]['recipe_id'] != second_result.payload['dishes'][0]['recipe_id']
            adopted = self.post(self.client, '/meal-plans', dict(
                meal_request_id=self.request['id'], request_version=self.request['version'],
                result_version=second_result.result_version), 201)
            with self.factory() as db:
                adopted_id = sa.select(AdoptedMealPlan.id).where(AdoptedMealPlan.meal_request_id == self.request['id'])
                adopted_id = db.scalar(adopted_id)
                adopted_snapshot = db.get(AdoptedMealPlan, adopted_id).snapshot
            assert adopted['created']
            source, = adopted_snapshot['recipe_sources'].values()
            assert source['recipe_publication_id'] == second
            feed_path = API + '/meal-confirmation-flows'
            assert self.client.get(feed_path).status_code == 403
            feed = publisher.get(feed_path)
            assert feed.status_code == 200, feed.text
            assert feed.headers['cache-control'] == 'no-store'
            trace, = feed.json()['items']
            assert set(trace) == {'trace_id', 'mode', 'events'}
            assert trace['trace_id'] == str(adopted_id)
            assert [event['stage'] for event in trace['events']][:5] == [
                'confirmed', 'snapshot_saved', 'history_saved', 'ingredients_saved', 'nutrition_saved',
            ]
            assert all(set(event) == {'sequence', 'stage', 'occurred_at'} for event in trace['events'])
            self.post(self.client, '/meal-plans', dict(
                meal_request_id=self.request['id'], request_version=self.request['version'],
                result_version=second_result.result_version), 200)
            assert publisher.get(feed_path).json()['items'] == [trace]

            self.rollback(publisher, first, 3)
            restored = self.query_and_generate('家常蒜香高麗菜', 't01v:', '將高麗菜與大蒜拌炒至熟。', '60', first)
            assert restored.payload['dishes'][0]['recipe_id'] == first_result.payload['dishes'][0]['recipe_id']
            self.rollback(publisher, BASELINE, 4)
            self.query_and_generate('清炒高麗菜', 'R02', '將高麗菜炒熟。', '50')
            with self.factory() as db:
                assert db.get(GenerationResult, old_result.id).payload == old_snapshot
                assert db.get(GenerationResult, second_result.id).payload == second_result.payload
                assert db.get(AdoptedMealPlan, adopted_id).snapshot == adopted_snapshot
                receipts = db.scalars(sa.select(T04CookingReceipt).where(T04CookingReceipt.food_id == CABBAGE)).all()
                by_recipe = {row.receipt['context'].get('recipe_version_id'): row for row in receipts}
                first_receipt = by_recipe[first_result.payload['dishes'][0]['recipe_id']]
                second_receipt = by_recipe[second_result.payload['dishes'][0]['recipe_id']]
                assert first_receipt.recipe_ingredient_id != second_receipt.recipe_ingredient_id
                assert (first_receipt.weight_g, second_receipt.weight_g) == (Decimal(60), Decimal(70))
                assert first_receipt.receipt['context']['recipe_publication_id'] == first
                assert second_receipt.receipt['context']['recipe_publication_id'] == second

    @staticmethod
    def seed_nutrition(db):
        from app.services.meal_calculation import normalize_cooking_method
        from app.management.service import now

        release = db.get(RecipeDataRelease, 'legacy-1017')
        db.add(NutritionImport(import_id=NUTRITION_IMPORT, source_files={'recipes': {'sha256': release.source_sha256}},
                               report={'fixture': 'isolated T01 integration test'}, imported_at=now()))
        db.flush()
        for index, (food, name) in enumerate(((CABBAGE, '高麗菜'), (GARLIC, '大蒜')), 1):
            source, nutrition, sample = f'fixture-source-{index}', f'fixture-nutrition-{index}', f'fixture-sample-{index}'
            db.add(NutritionSource(source_id=source, import_id=NUTRITION_IMPORT, source_key=source,
                                   provider='TFDA', source_record_id=sample, source_url='https://example.invalid/fixture',
                                   source_sheet='synthetic', source_row=index, raw_data={
                                       '來源資料ID': sample, '熟製狀態': '未熟製', '納入平均': '是',
                                       '熱量(kcal/100g)': 400, '蛋白質(g/100g)': 40, '脂肪(g/100g)': 10,
                                       '碳水化合物(g/100g)': 35, '糖(g/100g)': 2,
                                       '鈉(mg/100g)': 25, '膳食纖維(g/100g)': None,
                                   }))
            db.add(FoodNutrition(nutrition_id=nutrition, import_id=NUTRITION_IMPORT, food_id=food, source_row=index,
                                 standard_name=name, cooking_state='未熟製', calculation_method='synthetic',
                                 energy_kcal=400, protein_g=40, fat_g=10, carbohydrate_g=35, sugars_g=2,
                                 sodium_mg=25, dietary_fiber_g=None, nutrition_complete=False,
                                 quality_issues=['missing:dietary_fiber_g'], raw_data={}))
            db.flush()
            db.add(NutritionSourceLink(nutrition_id=nutrition, source_id=source, included_in_calculation=True))
            db.add(NutritionFoodMapping(mapping_id=f'fixture-mapping-{index}', import_id=NUTRITION_IMPORT,
                                        recipe_release_id=release.release_id, food_id=food, nutrition_id=nutrition,
                                        source_id=source, cooking_state='未熟製', approval_status='approved',
                                        mapping_version=MAPPING_VERSION, evidence={'fixture': True}))
            db.add(ConversionFactorEstimate(
                kind='cooking', food_id=food, factor_key=normalize_cooking_method('炒'),
                yield_factor=1, energy_retention=1, protein_retention=1, source='human', status='approved',
                assumptions=['isolated synthetic calculation'], input_snapshot={
                    'tfda_raw': {'sample_id': sample}, 'recipe_release_id': release.release_id,
                    'nutrition_import_id': NUTRITION_IMPORT, 'mapping_version': MAPPING_VERSION,
                    'retention': {key: 1 for key in ('fat_g', 'carbohydrate_g', 'sugars_g', 'sodium_mg')},
                }))

    def admin_client(self, name, roles):
        client = TestClient(app, base_url='https://testserver', headers={'Origin': ORIGIN})
        self.addCleanup(client.close)
        account = self.post(client, '/auth/register', {'email': f'{name}@example.test', 'password': PASSWORD}, 201)
        actor = account['user']['id']
        with self.factory() as db, db.begin():
            from app.management.service import now
            db.execute(tables['role_grants'].insert(), [
                dict(id=uuid4().hex, user_id=actor, role=role, domain_scope=['recipe'],
                     access_revision=1, created_by=actor, created_at=now()) for role in roles
            ])
        return client

    def post(self, client, url, body, status=200):
        response = client.post(url, json=body, headers={'Idempotency-Key': uuid4().hex})
        assert response.status_code == status, response.text
        return response.json()

    def publish(self, editor, reviewer, publisher, name, cabbage, step, base=None):
        context = editor.get(API + '/draft-context', params={'domain': 'recipe'})
        assert context.status_code == 200, context.text
        pins = context.json()
        assert pins['base_publication_id'] == base and pins['legacy_recipe_release_id'] == 'legacy-1017'
        evidence = editor.post(API + '/artifacts', data={'domain': 'recipe', 'source_kind': 'manual_entry', 'reason': 'synthetic'},
                               files={'file': ('evidence.txt', b'synthetic reviewed evidence', 'text/plain')},
                               headers={'Idempotency-Key': uuid4().hex})
        assert evidence.status_code == 201, evidence.text
        evidence = evidence.json()

        def ingredient(fid, label, grams):
            value = dict(status='declared', value=grams, unknown_reason=None, evidence_ids=[evidence['artifact_id']])
            return dict(line_id=fid, food_id=fid, ingredient_version_id=None, canonical_name=label, raw_text=label,
                        amount=value, unit='g', weight_g=value, weight_basis='whole_source_recipe_before_cooking')

        content = dict(logical_recipe_id='R02', name=name, standard_dish_id=None, aliases=[],
                       category='主菜', cuisine='台式', meal_type='晚餐', declared_allergen_ids=None,
                       servings=dict(count='2', basis='portions', raw_text='2 人份', portions_per_batch=None, evidence_ids=[]),
                       ingredients=[ingredient(CABBAGE, '高麗菜', cabbage),
                                    ingredient(GARLIC, '大蒜', str(200 - int(cabbage)))],
                       steps=[dict(step_no=1, instruction=step)],
                       sources=[dict(artifact_id=evidence['artifact_id'], artifact_sha256=evidence['sha256'],
                                     origin_kind='manual_entry', source_policy_version_id=None)])
        proposal = self.post(editor, API + '/proposals', dict(domain='recipe', content=content, dependency_pins=pins,
                                                             reason='synthetic revision'), 201)
        url = API + '/proposals/' + proposal['proposal_id']
        expected = dict(expected_revision=1, expected_content_hash=proposal['content_hash'], reason='synthetic review')
        receipt = self.post(editor, url + '/validations?run_now=true', expected, 202)
        validation = editor.get(API + '/tasks/' + receipt['task_id'] + '/result').json()
        assert validation['catalog_ready'], validation
        self.post(editor, url + '/submit', expected)
        approvals = {}
        for scope in ('content', 'source_rights'):
            decision = self.post(reviewer, url + '/reviews', dict(
                expected, scope=scope, verdict='approve', dependency_hash=proposal['dependency_hash'],
                validation_id=validation['validation_id']))
            approvals[scope + '_approval_id'] = decision['resource_id']
        publication = self.post(publisher, API + '/publications', dict(
            domain='recipe', reason='synthetic publication', base_publication_id=base, proposal_refs=[dict(
                proposal_id=proposal['proposal_id'], revision=1, content_hash=proposal['content_hash'],
                validation_id=validation['validation_id'], **approvals)]), 201)
        return publication['publication_id']

    def plan(self, actor, publication, revision):
        plan = self.post(actor, BINDING + '/plans', dict(publication_id=publication,
                                                        expected_binding_revision=revision, reason='test plan'))
        assert plan['can_activate'], plan
        return plan

    @staticmethod
    def activation_body(plan, target, rollback=False):
        return {('target_publication_id' if rollback else 'publication_id'): target,
                'plan_id': plan['plan_id'], 'plan_hash': plan['plan_hash'], 'reason': 'test switch',
                'expected_binding_revision': plan['expected_binding_revision']}

    def rollback(self, actor, target, revision):
        plan = self.plan(actor, target, revision)
        result = self.post(actor, BINDING + '/rollback', self.activation_body(plan, target, rollback=True))
        assert result['publication_id'] == (None if target == BASELINE else target)
        assert result['revision'] == revision + 1

    def query_and_generate(self, name, recipe_id, instruction, grams, publication=None):
        request = self.create_request(mode='picker', dish_count=1, specified_dishes=[name],
                                      available_ingredient_ids=[CABBAGE, GARLIC])
        path = '/meal-requests/' + request['id']
        catalog = self.client.get(path + '/recipe-catalog')
        assert catalog.status_code == 200, catalog.text
        names = {row['dish_name'] for row in catalog.json()['dishes']}
        assert names.intersection({'清炒高麗菜', '家常蒜香高麗菜', '清香蒜炒高麗菜'}) == {name}
        preview = self.client.get(path + '/recipes')
        assert preview.status_code == 200, preview.text
        selected = preview.json()['selected']
        assert len(selected) == 1 and selected[0]['recipe_name'] == name
        assert selected[0]['recipe_id'].startswith(recipe_id)
        if publication:
            stale = self.client.post(path + '/nutrition-preview', json={'recipe_ids': ['R02']})
            assert stale.json()['error']['code'] == 'RECIPE_PUBLICATION_CHANGED'
            nutrition = self.client.post(path + '/nutrition-preview', json={'recipe_ids': [selected[0]['recipe_id']]})
            assert nutrition.status_code == 200, nutrition.text
            assert nutrition.json()['dishes'][0]['steps'] == [instruction]

        job = self.submit(key=uuid4().hex)
        from app.jobs.worker import run_once
        from app.services import jobs
        from app.services.generation import make_generation_handler
        from datetime import timedelta

        with self.factory() as db, db.begin():
            db.execute(sa.text("UPDATE background_jobs SET available_at = :at WHERE status = 'queued'"),
                       {'at': jobs._now() - timedelta(seconds=1)})
        handler = make_generation_handler(responder=None, knowledge_client=None, embedder=None)
        assert run_once(self.factory, 'synthetic-recipe-worker', {'meal_generation': handler})
        assert self.job(job)['status'] == 'succeeded', self.job(job)
        with self.factory() as db:
            result = db.scalar(sa.select(GenerationResult).where(GenerationResult.job_id == job))
        assert result.mode == 'database_menu'
        dish, = result.payload['dishes']
        assert dish['recipe_id'] == selected[0]['recipe_id']
        assert dish['dish_name'] == name and dish['steps'] == [instruction]
        assert Decimal(dish['ingredients'][0]['grams_per_n']) == Decimal(grams)
        assert (dish.get('recipe_source') or {}).get('recipe_publication_id') == publication
        final = result.payload['calculation']['final']
        assert Decimal(final['energy_kcal']) == Decimal(400), result.payload['calculation']['block_reasons']
        assert final['other_nutrients']['dietary_fiber_g'] is None
        return result
