"""T11 最後小知識文字改由雲端生成：共用提示詞、驗證與比較腳本（不連網、不需資料庫）。"""
import json

import pytest

from knowledge_rag import explain

REC = dict(knowledge_id='K1', ingredient_id='FOOD000001', ingredient_name='大蒜',
           fact_zh='營養師指出食用大蒜有助提振食慾、代謝及減輕疲勞；一次吃太多可能造成腹瀉。',
           conditions='限一般食用量。', evidence_type='食材直接衛教／非人體試驗')
STUDY = dict(REC, knowledge_id='K2', fact_zh='規律吃辣椒，可能減輕飯後胰島素升高的程度。', evidence_type='人體隨機交叉試驗')


def check(tip, cites=('K1',), recs=(REC, STUDY)):
    return explain.validate({'tip_zh': tip, 'citations': list(cites)}, list(recs))


def test_grounded_tip_with_caution_is_accepted():
    assert check('大蒜有助提振食慾與代謝，但吃太多可能腹瀉。')['status'] == 'accepted'


@pytest.mark.parametrize('tip, cites, code', [
    ('大蒜有助穩定血糖。', ('K1',), 'KEYWORD_NOT_IN_KNOWLEDGE'),
    ('每天吃3瓣大蒜有助代謝。', ('K1',), 'NUMBER_NOT_IN_KNOWLEDGE'),
    ('大蒜保證提振食慾。', ('K1',), 'OVERCLAIM'),
    ('研究指出大蒜有助代謝。', ('K1',), 'STUDY_CLAIM_ON_NON_STUDY'),
    ('大蒜有助代謝。', ('K9',), 'CITATION_NOT_PROVIDED'),
    ('大蒜有助代謝。', (), 'NO_CITATION'),
])
def test_ungrounded_tip_is_rejected(tip, cites, code):
    assert code in check(tip, cites)['failures']


def test_negated_claims_are_not_overclaims():
    assert check('大蒜有助提振食慾，但效果沒有保證。')['status'] == 'accepted'
    assert check('大蒜有助代謝，未提供人體試驗。')['status'] == 'accepted'


def test_study_wording_allowed_for_study_records():
    assert check('研究指出規律吃辣椒，可能減輕飯後胰島素升高。', ('K2',))['status'] == 'accepted'


@pytest.mark.parametrize('tip', [
    '規律吃辣椒可能減輕飯後胰島素升高，但原因未明。',
    '規律吃辣椒可能減輕飯後胰島素升高，原因尚不清楚。',
    '規律吃辣椒可能減輕飯後胰島素升高，機制仍有待釐清。',
])
def test_unexplained_cause_wording_is_rejected(tip):
    """2026-11-03：小知識不可出現「原因不明」這類字眼。"""
    result = check(tip, ('K2',))
    assert result['status'] == 'rejected' and 'UNEXPLAINED_CAUSE' in result['failures']
    assert '原因不明' in explain.INSTRUCTIONS


@pytest.mark.parametrize('tip', [
    '大蒜有助提振食慾，約85公克要算進主食熱量。',
    '大蒜有助代謝，但要連同其他主食計算份量。',
    '大蒜可作主食代換，有助提振食慾。',
    '大蒜每次吃10g有助代謝。',
])
def test_portion_accounting_wording_is_rejected(tip):
    """2026-11-03：小知識不可出現「約85公克要算進主食熱量」這類份量計算提醒。"""
    assert 'PORTION_ACCOUNTING' in check(tip)['failures']
    assert '算進主食熱量' in explain.INSTRUCTIONS


def test_prompt_keeps_cautions_and_forbids_boilerplate():
    assert '不可只寫好處' in explain.INSTRUCTIONS and '制式聲明' in explain.INSTRUCTIONS
    assert explain.SCHEMA['required'] == ['tip_zh', 'citations']


def test_groups_cover_all_123_records_in_76_ingredients():
    from pathlib import Path
    recs = json.loads((Path(explain.__file__).parent / 'candidates/web_paper_2026-10-30.json').read_text(encoding='utf-8'))['records']
    groups = explain.ingredient_groups(recs)
    assert len(groups) == 76 and sum(len(g[2]) for g in groups) == 123 and max(len(g[2]) for g in groups) <= 3


def test_input_excludes_urls_and_parse_requires_schema():
    data = json.loads(explain.build_input('大蒜', [dict(REC, source_url='https://x')]))
    assert 'source_url' not in data['knowledge'][0]
    with pytest.raises(ValueError):
        explain.parse_output(json.dumps({'tip_zh': 'x'}))


def test_compare_report(tmp_path):
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location('b', Path(explain.__file__).parents[1] / 'scripts/t11_explain_benchmark.py')
    b = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(b)
    def row(model, ms, ok, first=False):
        return dict(ingredient_id='FOOD000001', ingredient_name='大蒜', manual=['m'], model=model, latency_ms=ms,
                    first_call=first, error=None, usage={'input_tokens': 700, 'output_tokens': 60},
                    cost_usd=b.cost(model, {'input_tokens': 700, 'output_tokens': 60}),
                    validation=dict(status='accepted' if ok else 'rejected', tip_zh='大蒜有助代謝。', citations=['K1'],
                                    failures=[] if ok else ['OVERCLAIM']))
    (tmp_path / 'c.jsonl').write_text(json.dumps(row('gpt-5.4-mini-2026-03-17', 1100, True), ensure_ascii=False) + '\n', encoding='utf-8')
    (tmp_path / 'l.jsonl').write_text(json.dumps(row('qwen3.5:9b-q4_K_M', 9000, False, True), ensure_ascii=False) + '\n', encoding='utf-8')
    b.main(['compare', '--run', f'雲端={tmp_path / "c.jsonl"}', '--run', f'地端={tmp_path / "l.jsonl"}', '--out', str(tmp_path / 'r')])
    s = json.loads((tmp_path / 'r/summary.json').read_text(encoding='utf-8'))
    assert s['雲端']['accepted'] == 1 and s['地端']['cold_ms'] == 9000 and s['地端']['cost_usd'] == '0'
    assert s['雲端']['cost_usd'] == '0.0008'  # 700×0.75＋60×4.50 per 1M
