import json
from types import SimpleNamespace
from uuid import uuid4

from app.core.config import settings
from app.services.generation import FoodDictionary
from app.services import generation_diagnostics as audit


def test_identity_evidence_and_version_bound_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'project_data_root', tmp_path)
    foods = FoodDictionary([('FOOD001564', '雞胸肉', [], []),
                            ('FOOD001578', '雞腿肉', [], [])])
    job = SimpleNamespace(id=uuid4(), attempts=1)
    audit.record_failure(job, foods, [{'code': 'INGREDIENT_ID_MISMATCH',
        'ingredient': '雞胸肉', 'submitted_food_id': 'FOOD001578', 'dish_name': 'private prose'}])
    path = audit._path(job, 1)
    text = path.read_text(encoding='utf-8')
    assert 'private prose' not in text
    row = json.loads(text)
    assert row['errors'][0]['candidate_food_ids'] == ['FOOD001564']
    assert row['errors'][0]['submitted_food_id'] == 'FOOD001578'
    assert audit.retry_feedback(job, foods) == []
    job.attempts = 2
    assert audit.retry_feedback(job, foods)[0]['candidate_names'] == ['雞胸肉']
    foods.names['FOOD001564'] = 'changed'
    assert audit.retry_feedback(job, foods) == []


def test_unknown_output_is_not_persisted_and_attempt_is_immutable(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'project_data_root', tmp_path)
    foods = FoodDictionary([('FOOD001564', '雞胸肉', [], [])])
    job = SimpleNamespace(id=uuid4(), attempts=1)
    audit.record_failure(job, foods, [{'code': 'INGREDIENT_UNKNOWN',
        'ingredient': 'secret arbitrary prose', 'submitted_food_id': 'Bearer SECRET'}])
    path = audit._path(job, 1)
    before = path.read_text(encoding='utf-8')
    assert 'SECRET' not in before and 'secret arbitrary prose' not in before
    audit.record_failure(job, foods, [{'code': 'INGREDIENT_UNKNOWN', 'ingredient': '雞胸肉'}])
    assert path.read_text(encoding='utf-8') == before
    job.attempts = 2
    path.write_text('{broken', encoding='utf-8')
    assert audit.retry_feedback(job, foods) == []


def test_retry_never_suggests_ambiguous_candidate(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'project_data_root', tmp_path)
    foods = FoodDictionary([('FOOD000691', '食鹽', ['鹽'], []),
                            ('FOOD001680', '鹽', [], [])])
    job = SimpleNamespace(id=uuid4(), attempts=1)
    audit.record_failure(job, foods, [{'code': 'INGREDIENT_AMBIGUOUS', 'ingredient': '鹽'}])
    job.attempts = 2
    feedback = audit.retry_feedback(job, foods)
    assert feedback[0]['candidate_names'] == ['食鹽']


def test_quantity_diagnostic_is_numeric_and_retryable(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, 'project_data_root', tmp_path)
    foods = FoodDictionary([('FOOD001564', '雞胸肉', [], [])])
    job = SimpleNamespace(id=uuid4(), attempts=1)
    audit.record_failure(job, foods, [{'code': 'INVALID_QUANTITY', 'ingredient': '雞胸肉', 'grams_per_n': 0}])
    assert json.loads(audit._path(job, 1).read_text(encoding='utf-8'))['errors'][0]['grams_per_n'] == 0
    job.attempts = 2
    assert '2000' in audit.retry_feedback(job, foods)[0]['quantity_rule']
