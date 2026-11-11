"""Local T11 wording uses fake HTTP here, never external models or databases."""
import json
import httpx
import pytest

from app.core.config import settings
from app.services import generation, knowledge_explanation as ke
from app.services.local_knowledge_explanation import (
    LocalExplanationUnavailable, OllamaKnowledgeResponder,
)

REC = {'knowledge_id': 'K1', 'source_version': 'v1', 'ingredient_id': 'FOOD000001',
       'ingredient_name': '青菜', 'fact_zh': '青菜含膳食纖維。',
       'nutrition_fact': '青菜含膳食纖維。', 'evidence_quote': '青菜含膳食纖維。',
       'excerpt': '青菜含膳食纖維。', 'source_url': 'https://example.test/k1',
       'conditions': '一般食用量', 'rights': 'Synthetic unit fixture',
       'status': 'approved', 'evidence_type': '食材直接衛教'}
SNAP = {'status': 'available', 'records': [REC],
        'by_ingredient': {'FOOD000001': 'available'}, 'index_version': 'test-index'}


def never_paid(**kwargs):
    raise AssertionError('Local knowledge must not call paid provider')


def local_response(payload, *, done=True, done_reason='stop'):
    return httpx.Response(200, json={'done': done, 'done_reason': done_reason,
        'message': {'content': json.dumps(payload, ensure_ascii=False)}})


def responder_for(payload):
    return OllamaKnowledgeResponder('http://ollama:11434', 'qwen3.5:9b-q4_K_M',
        transport=httpx.MockTransport(lambda request: local_response(payload)))


def test_http_contract_has_schema_thinking_disabled_and_token_limit():
    calls = []
    def handle(request):
        assert str(request.url) == 'http://ollama:11434/api/chat'
        calls.append(json.loads(request.content))
        return local_response({'tip_zh': '青菜含膳食纖維。', 'citations': ['K1']})
    local = OllamaKnowledgeResponder('http://ollama:11434', 'local-model', transport=httpx.MockTransport(handle))
    result = local.respond(instructions='rules', input_text='sources', schema={'type': 'object'}, max_output_tokens=600)
    assert result.status == 'completed'
    assert calls[0]['think'] is False and calls[0]['stream'] is False
    assert calls[0]['format'] == {'type': 'object'}
    assert calls[0]['options']['num_predict'] == 600
    assert calls[0]['messages'][0] == {'role': 'system', 'content': 'rules'}


@pytest.mark.parametrize('response', [httpx.Response(503), httpx.Response(200, text='bad'),
    local_response({}, done=False), local_response({}, done_reason='length')])
def test_failed_local_output_has_no_success_or_provider_fallback(response):
    local = OllamaKnowledgeResponder('http://ollama:11434', 'm', transport=httpx.MockTransport(lambda request: response))
    with pytest.raises(LocalExplanationUnavailable):
        local.respond(instructions='', input_text='', schema={}, max_output_tokens=20)


def test_preview_validates_citations_without_paid_call():
    local = responder_for({'tip_zh': '青菜含膳食纖維。', 'citations': ['K1']})
    result = ke.explain_snapshot(never_paid, ke.OllamaExplainer(local), SNAP)
    assert result['status'] == 'available'
    assert result['items'][0]['provider'] == 'ollama'
    assert result['items'][0]['citations'][0]['source_url'] == REC['source_url']


def test_preview_drops_invented_citation():
    local = responder_for({'tip_zh': '青菜含膳食纖維。', 'citations': ['not-in-snapshot']})
    result = ke.explain_snapshot(never_paid, ke.OllamaExplainer(local), SNAP)
    assert not result['items'] and result['dropped']


def test_preview_does_not_expand_from_legacy_fact_or_unverified_excerpt():
    old = {**REC, 'knowledge_id':'legacy', 'nutrition_fact':None, 'evidence_quote':None}
    approved = {**REC, 'fact_zh':'青菜含膳食纖維，有助降低血糖。'}
    seen = []
    class Explainer:
        model = 'test-model'
        def respond(self, input_text):
            seen.append(json.loads(input_text))
            return json.dumps({'tip_zh':'青菜含膳食纖維。','citations':['K1']},ensure_ascii=False), {}
    result = ke.explain_snapshot(lambda **kw: kw['call']()[0], Explainer(),
                                {**SNAP,'records':[old, approved]})
    assert result['status'] == 'available'
    assert len(seen[0]['knowledge']) == 1
    assert seen[0]['knowledge'][0]['fact_zh'] == REC['nutrition_fact']


@pytest.mark.parametrize('citation_id,expected', [('K1', 'available'), ('invented', 'not_found')])
def test_formal_local_keeps_grounding_and_source_citations(monkeypatch, citation_id, expected):
    from app.services import local_knowledge_explanation as local_module
    local = responder_for({'items': [{'ingredient_id': 'FOOD000001', 'explanation': '青菜含膳食纖維。',
        'citations': [{'knowledge_id': citation_id, 'source_version': 'v1', 'ingredient_id': 'FOOD000001'}]}]})
    monkeypatch.setattr(settings, 'knowledge_explain_provider', 'ollama')
    monkeypatch.setattr(local_module, 'configured_local_responder', lambda: local)
    result = generation._explain(never_paid, None, SNAP, {'FOOD000001': '青菜'}, lambda stage: None)
    assert result['status'] == expected
    if expected == 'available':
        assert result['items'][0]['citations'][0]['source_url'] == REC['source_url']
        metadata = generation.explanation_evidence(SNAP, result)
        assert metadata['explanation_provider'] == 'ollama'
        assert metadata['explanation_model'] == local.model
    else:
        assert not result['items']


def test_local_opt_in_without_url_never_uses_configured_cloud_key(monkeypatch):
    monkeypatch.setattr(settings, 'knowledge_explain_provider', 'ollama')
    monkeypatch.setattr(settings, 'knowledge_ollama_url', None)
    monkeypatch.setattr(settings, 'openai_api_key', 'must-not-be-used')
    assert ke.default_explainer() is None
    result = generation._explain(never_paid, object(), SNAP, {}, lambda stage: None)
    assert result['reason'] == 'EXPLANATION_NOT_CONFIGURED'


@pytest.mark.parametrize('missing', ['nutrition_fact', 'evidence_quote'])
def test_formal_local_missing_nutrition_evidence_does_not_generate(monkeypatch, missing):
    from app.services import local_knowledge_explanation as local_module

    class MustNotGenerate:
        def respond(self, **kwargs):
            pytest.fail('Missing nutrition evidence must never be repaired by generation')

    row = {k: v for k, v in REC.items() if k != missing}
    snapshot = {**SNAP, 'records': [row]}
    monkeypatch.setattr(settings, 'knowledge_explain_provider', 'ollama')
    monkeypatch.setattr(local_module, 'configured_local_responder', MustNotGenerate)
    result = generation._explain(never_paid, None, snapshot, {}, lambda stage: None)
    assert result == {'status': 'not_found', 'items': [], 'dropped': [
        {'ingredient_id': 'FOOD000001', 'reason': 'NO_NUTRITION_FACT'}]}
