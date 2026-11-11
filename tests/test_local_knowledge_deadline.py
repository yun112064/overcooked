"""Bound optional local knowledge without database writes or actual models."""
import threading
from types import SimpleNamespace

import httpx
import pytest

from app.services import generation, knowledge, knowledge_deadline as kd
from app.services import local_knowledge_explanation as local
from app.core.config import settings
from test_local_knowledge_explanation import SNAP, never_paid


def test_configured_420_seconds_is_capped_to_30():
    assert kd.local_timeout(420) == 30


def test_remaining_job_budget_reserves_result_save_time(monkeypatch):
    monkeypatch.setattr(kd.time, 'monotonic', lambda: 270)
    with kd.job_deadline_scope(300):
        assert kd.local_timeout(420) == 20
    assert kd.local_timeout(420) == 30


def test_optional_section_shares_45_second_budget(monkeypatch):
    clock = [100]
    monkeypatch.setattr(kd.time, 'monotonic', lambda: clock[0])
    with kd.optional_knowledge_scope():
        assert kd.local_timeout(420) == 30
        clock[0] += 29
        assert kd.local_timeout(420) == 16
        clock[0] += 16
        with pytest.raises(kd.LocalKnowledgeTimeout):
            kd.local_timeout(420)


def test_exhausted_job_budget_does_not_call_http(monkeypatch):
    monkeypatch.setattr(kd.time, 'monotonic', lambda: 292)
    monkeypatch.setattr(knowledge.httpx, 'post', lambda *a, **k: pytest.fail('No HTTP after budget exhaustion'))
    client = knowledge.HttpKnowledgeClient('http://unused', SimpleNamespace(headers=lambda: {}), 420, provider='ollama')
    with kd.job_deadline_scope(300):
        result = knowledge.retrieve_knowledge(final_food_ids=['FOOD000039'], query_text='test',
                                              client=client, embedder=None, paid_call=never_paid)
    assert result['status'] == 'unavailable' and result['reason'] == 'TIMEOUT'


def test_slow_local_call_returns_without_waiting_for_late_output(monkeypatch):
    monkeypatch.setattr(kd, '_local_slot', threading.BoundedSemaphore(1))
    release, finished = threading.Event(), threading.Event()
    def slow():
        try:
            release.wait(2)
            return 'late content must not be accepted'
        finally:
            finished.set()
    try:
        with pytest.raises(kd.LocalKnowledgeTimeout, match='TIMEOUT'):
            kd.bounded_local_call(slow, 0.01)
        assert not finished.is_set()
        with pytest.raises(kd.LocalKnowledgeTimeout, match='BUSY'):
            kd.bounded_local_call(lambda: 'another call', 0.01)
    finally:
        release.set()
        assert finished.wait(2)


def test_local_explanation_timeout_preserves_retrieved_snapshot(monkeypatch):
    calls = []
    def timed_out(request):
        calls.append(request.extensions['timeout']['read'])
        raise httpx.ReadTimeout('simulated busy local model')
    responder = local.OllamaKnowledgeResponder('http://local.test', 'qwen-local', timeout=420,
                                               transport=httpx.MockTransport(timed_out))
    monkeypatch.setattr(settings, 'knowledge_explain_provider', 'ollama')
    monkeypatch.setattr(local, 'configured_local_responder', lambda: responder)
    monkeypatch.setattr(knowledge, 'retrieve_knowledge', lambda **kwargs: SNAP)
    monkeypatch.setattr(generation.knowledge_queue, 'record_missing', lambda *a: None)
    snapshot, explanation = generation.knowledge_section(
        final_food_ids=['FOOD000001'], names={}, paid_call=never_paid, responder=object(),
        knowledge_client=object(), embedder=None, stage=lambda stage: None, factory=None)
    assert snapshot is SNAP
    assert explanation['status'] == 'unavailable'
    assert explanation['reason'] == 'EXPLANATION_UNAVAILABLE'
    assert calls == [30]


def test_worker_passes_actual_attempt_deadline_and_resets_it(monkeypatch):
    from app.jobs import worker
    clock = [100]
    monkeypatch.setattr(kd.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(worker.jobs, 'claim', lambda *a: object())
    class Timer:
        def __init__(self, *a): pass
        def start(self): pass
        def cancel(self): pass
    monkeypatch.setattr(worker.threading, 'Timer', Timer)
    def run(*args):
        clock[0] = 389
        assert kd.local_timeout(420) == 1  # 400 deadline, last 10 reserved.
        return 'done'
    monkeypatch.setattr(worker, '_run', run)
    assert worker.run_once(None, 'owner', timeout=300) == 'done'
    assert kd.local_timeout(420) == 30


@pytest.mark.parametrize('value', [0, -1, float('nan'), float('inf')])
def test_invalid_local_timeouts_fail_closed(value):
    with pytest.raises(kd.LocalKnowledgeTimeout):
        kd.local_timeout(value)
