"""Lifecycle orchestration tests: no Docker, source import or real database."""
import importlib.util
import json
from pathlib import Path
import pytest


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    path = Path(__file__).resolve().parents[1] / 'deploy/1111_update_V2/runtime.py'
    spec = importlib.util.spec_from_file_location('v2_install_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, 'STATE', tmp_path)
    monkeypatch.setattr(module, 'prepare', lambda: {'E2E_DB_PASSWORD': 'synthetic'})
    monkeypatch.setattr(module, 'guard', lambda: None)
    return module


def write_env(runtime, url=None):
    url = url or 'postgresql+psycopg://app:synthetic@db:5432/' + runtime.DB
    (runtime.STATE / 'business.env').write_text('DATABASE_URL=' + json.dumps(url), encoding='utf-8')


def test_repeat_install_preserves_activity_and_does_not_import(runtime, monkeypatch):
    write_env(runtime)
    calls = []
    monkeypatch.setattr(runtime, 'compose', lambda *args, **kwargs: calls.append(args))
    runtime.install()
    assert not any('scripts/install_real_nutrition.py' in c for c in calls)
    verify = next(c for c in calls if 'scripts/verify_portable_nutrition.py' in c)
    assert '--require-empty-runtime' not in verify
    assert any('SELECT current_database()' in str(c) and 'READ ONLY' in str(c) for c in calls)
    assert not any(any(x in c for x in ('down', 'rm', 'volume', 'prune')) for c in calls)


def test_fresh_install_requires_empty_runtime(runtime, monkeypatch):
    calls = []
    def compose(*args, **kwargs):
        calls.append(args)
        if 'scripts/install_real_nutrition.py' in args:
            write_env(runtime)
    monkeypatch.setattr(runtime, 'compose', compose)
    runtime.install()
    assert sum('scripts/install_real_nutrition.py' in c for c in calls) == 1
    assert '--require-empty-runtime' in next(c for c in calls if 'scripts/verify_portable_nutrition.py' in c)


@pytest.mark.parametrize('url', [
    'postgresql://app:synthetic@db/production',
    'postgresql://app:synthetic@external/meal_planner_1102_v3_1111_update_v2',
    'postgresql://app:synthetic@db/meal_planner_1102_v3_1111_update_v2?host=external',
])
def test_wrong_existing_marker_target_stops_before_docker(runtime, monkeypatch, url):
    write_env(runtime, url)
    monkeypatch.setattr(runtime, 'compose', lambda *a, **k: pytest.fail('must stop before Docker'))
    with pytest.raises(RuntimeError, match='target'):
        runtime.install()


@pytest.mark.parametrize('action', ['start', 'start-core', 'verify'])
@pytest.mark.parametrize('url', ['malformed', 'postgresql://app@db/production'])
def test_runtime_actions_reject_unsafe_effective_url_before_docker(runtime, monkeypatch, action, url):
    (runtime.STATE / 'installed.json').write_text(json.dumps({'database': runtime.DB}), encoding='utf-8')
    monkeypatch.setattr(runtime, 'prepare', lambda: {'APP_DATABASE_URL': url})
    monkeypatch.setattr(runtime, 'guard', lambda: pytest.fail('must stop before Docker inspection'))
    monkeypatch.setattr(runtime, 'compose', lambda *a, **k: pytest.fail('must stop before Docker'))
    monkeypatch.setattr('sys.argv', ['runtime.py', action])
    with pytest.raises(RuntimeError, match='target'):
        runtime.main()


def test_wrong_installed_marker_refuses_before_prepare(runtime, monkeypatch):
    (runtime.STATE / 'installed.json').write_text(json.dumps({'database': 'production'}), encoding='utf-8')
    monkeypatch.setattr(runtime, 'prepare', lambda: pytest.fail('must reject marker first'))
    monkeypatch.setattr('sys.argv', ['runtime.py', 'start'])
    with pytest.raises(RuntimeError, match='marker'):
        runtime.main()


@pytest.mark.parametrize('key', ['CRAWLER_DATABASE_URL', 'MANAGEMENT_WORKER_DATABASE_URL'])
def test_runtime_auxiliary_credentials_must_remain_isolated(runtime, monkeypatch, key):
    (runtime.STATE / 'installed.json').write_text(json.dumps({'database': runtime.DB}), encoding='utf-8')
    monkeypatch.setattr(runtime, 'prepare', lambda: {
        'APP_DATABASE_URL': 'postgresql://app@db/' + runtime.DB,
        key: 'postgresql://privileged@db/production'})
    monkeypatch.setattr(runtime, 'guard', lambda: pytest.fail('must reject before Docker'))
    monkeypatch.setattr('sys.argv', ['runtime.py', 'verify'])
    with pytest.raises(RuntimeError, match='target'):
        runtime.main()
