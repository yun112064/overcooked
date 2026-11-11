"""Evidence preparation guards use synthetic paragraphs; no network or DB calls."""
import json
from types import SimpleNamespace

import pytest

from scripts import prepare_t11_evidence as prep


def test_default_is_offline_and_has_no_db(capsys):
    assert prep.main([]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report['dry_run'] and report['network_calls'] == 0
    assert not report['database_written'] and len(report['sources']) == 3


def test_data_root_refuses_repository():
    with pytest.raises(ValueError, match='outside'):
        prep.data_root(str(prep.ROOT / '.test-runtime' / 'unsafe-raw'))


def test_snapshot_never_overwritten(tmp_path):
    path = tmp_path / 'snapshot.json'
    prep.immutable_write(path, {'text': 'synthetic first'})
    prep.immutable_write(path, {'text': 'synthetic first'})
    with pytest.raises(ValueError, match='overwrite'):
        prep.immutable_write(path, {'text': 'changed'})


def test_explicit_quote_must_exist_in_current_paragraph():
    article = {'sections': [{'paragraphs': ['合成測試：大蒜含纖維。']} ]}
    selected = {'nutrition_fact': '大蒜含纖維。', 'evidence_quote': '大蒜含纖維。',
                'paragraph_contains': '大蒜', 'reason': 'Synthetic test only'}
    result = prep.literal_candidate(article, {}, selected)
    assert result['evidence_quote'] in result['excerpt']
    with pytest.raises(ValueError, match='exactly one'):
        prep.literal_candidate(article, {}, {**selected, 'evidence_quote': 'invented quotation'})


def test_bundle_hash_checked_before_reading_content(tmp_path):
    path = tmp_path / 'bundle.json'
    path.write_text('{}', encoding='utf-8')
    with pytest.raises(ValueError, match='hash mismatch'):
        prep.validate_bundle(tmp_path, path, 'a' * 64)


@pytest.mark.parametrize('dsn', [
    'postgresql://u:p@t11-db/meal_planner_1102_v3',
    'postgresql://u:p@production/' + prep.DB,
    'postgresql://u:p@localhost/' + prep.DB,
])
def test_apply_refuses_other_database_before_connecting(tmp_path, monkeypatch, dsn):
    monkeypatch.setattr(prep, 'validate_bundle', lambda *a: {'records': []})
    monkeypatch.setenv('ISOLATED_TEST_URL', dsn)
    args = SimpleNamespace(bundle='test', bundle_sha256='a' * 64, database_url_env='ISOLATED_TEST_URL')
    with pytest.raises(ValueError, match='exact'):
        prep.apply(tmp_path, args)
