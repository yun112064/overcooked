"""T02 credentials must not replace the application's job-session engine."""
import pytest
from sqlalchemy import create_engine, text

from app import pricing_scheduler as pricing


def test_crawler_url_writes_only_crawler_database(tmp_path, monkeypatch):
    job_url = f"sqlite:///{(tmp_path / 'jobs.sqlite').as_posix()}"
    crawler_url = f"sqlite:///{(tmp_path / 'prices.sqlite').as_posix()}"
    job_engine = create_engine(job_url)
    with job_engine.begin() as conn:
        conn.execute(text('CREATE TABLE sentinel (value TEXT)'))
        conn.execute(text("INSERT INTO sentinel VALUES ('jobs unchanged')"))
    monkeypatch.setenv('DATABASE_URL', job_url)
    monkeypatch.setenv('CRAWLER_DATABASE_URL', crawler_url)
    service = pricing.create_service(pricing.SchedulerSettings.from_env(),
                                     expected_recipe_release_id='pinned-release')
    try:
        assert str(service.repository.engine.url) == crawler_url
        assert service.repository.expected_recipe_release_id == 'pinned-release'
        with service.repository.engine.begin() as conn:
            conn.execute(text('CREATE TABLE crawler_marker (value TEXT)'))
        with job_engine.connect() as conn:
            assert conn.execute(text('SELECT value FROM sentinel')).scalar_one() == 'jobs unchanged'
            assert conn.execute(text("SELECT count(*) FROM sqlite_master WHERE name='crawler_marker'")).scalar_one() == 0
    finally:
        service.repository.engine.dispose()
        job_engine.dispose()


def test_explicit_url_overrides_crawler_environment(monkeypatch):
    monkeypatch.setenv('CRAWLER_DATABASE_URL', 'invalid-url-must-not-be-used')
    repository = pricing.create_repository(price_database_url='sqlite://')
    assert str(repository.engine.url) == 'sqlite://'
    repository.engine.dispose()


def test_unconfigured_preserves_repository_default_without_creating_engine(monkeypatch):
    monkeypatch.delenv('CRAWLER_DATABASE_URL', raising=False)
    captured = {}
    def repository(**kwargs):
        captured.update(kwargs)
        return captured
    def forbidden_engine(*args, **kwargs):
        pytest.fail('must delegate to existing repository default')
    monkeypatch.setattr(pricing, 'PricingRepository', repository)
    monkeypatch.setattr(pricing, 'create_engine', forbidden_engine)
    assert pricing.create_repository(expected_recipe_release_id='release') == {
        'expected_recipe_release_id': 'release'}


@pytest.mark.parametrize('value', ['', '  '])
def test_empty_crawler_url_fails_closed(monkeypatch, value):
    monkeypatch.setenv('CRAWLER_DATABASE_URL', value)
    with pytest.raises(ValueError, match='must not be empty'):
        pricing.create_repository()
