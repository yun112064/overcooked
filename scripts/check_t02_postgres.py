"""Run T02/T13 PostgreSQL checks exclusively in a disposable V3 test database."""
import json
import argparse
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT)]
TEST_DB = 'meal_planner_1102_v3_t02_test'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reuse-disposable-test-db', action='store_true')
    args = parser.parse_args()
    from sqlalchemy import create_engine, text
    from alembic import command
    from alembic.config import Config
    from app.core.config import settings

    runtime = create_engine(settings.database_url)
    if (runtime.url.database != 'meal_planner_1102_v3'
            or runtime.url.host != '127.0.0.1' or runtime.url.port != 55435):
        raise ValueError('Expected the dedicated local V3 server; refusing other targets')
    admin = create_engine(runtime.url.set(database='postgres'), isolation_level='AUTOCOMMIT')
    with admin.connect() as connection:
        exists = connection.scalar(text('SELECT 1 FROM pg_database WHERE datname=:name'), {'name': TEST_DB})
        if exists and not args.reuse_disposable_test_db:
            raise ValueError('Test database already exists; inspect it before authorizing reuse')
        if not exists:
            connection.execute(text('CREATE DATABASE ' + TEST_DB + ' TEMPLATE template0'))
    test_url = runtime.url.set(database=TEST_DB)
    cfg = Config(str(ROOT / 'alembic.ini'))
    cfg.set_main_option('script_location', str(ROOT / 'migrations'))
    cfg.attributes['database_url'] = test_url.render_as_string(hide_password=False)
    cfg.attributes['configure_logger'] = False
    if exists:
        test_engine = create_engine(test_url)
        with test_engine.connect() as connection:
            assert connection.scalar(text('SELECT current_database()')) == TEST_DB
            if (connection.scalar(text("SELECT count(*) FROM recipe_data_releases WHERE release_id <> 'legacy-1017'"))
                    or connection.scalar(text('SELECT count(*) FROM source_archive'))
                    or connection.scalar(text('SELECT count(*) FROM price_data_releases'))):
                raise ValueError('Test database contains imported source data; refusing cleanup')
        command.downgrade(cfg, 'base')
        test_engine.dispose()
    command.upgrade(cfg, 'head')
    environment = {**os.environ, 'DATABASE_URL': 'postgresql+psycopg://v3_disabled:v3_disabled@127.0.0.1:1/v3_no_database',
        'TEST_DATABASE_URL': test_url.render_as_string(hide_password=False),
        'PYTHONPATH': os.pathsep.join([str(ROOT / 'backend'), str(ROOT / 'tests'), str(ROOT)]),
        'PYTHONUTF8': '1', 'OPENAI_API_KEY': '', 'GEMINI_API_KEY': '', 'NUTRITION_LLM_PROVIDER': '',
        'PROJECT_DATA_ROOT': str(ROOT / '.test-runtime/t02/data'),
        'TMP': str(ROOT / '.test-runtime/t02/tmp'), 'TEMP': str(ROOT / '.test-runtime/t02/tmp')}
    Path(environment['TMP']).mkdir(parents=True, exist_ok=True)
    args = [sys.executable, '-m', 'pytest', 'tests/test_t02_release.py', 'tests/test_t02_pricing.py',
            'tests/test_t02_uni_prosperity.py', 'tests/test_t13_meal_cost.py', '-k', 'not SeasoningTests',
            '-q', '--tb=short']
    result = subprocess.run(args, cwd=ROOT, env=environment, check=False)
    if result.returncode:
        raise SystemExit(result.returncode)
    command.check(cfg)
    report = {'database': TEST_DB, 'runtime_database_used_for_tests': False, 'pytest_exit_code': 0,
              'alembic_check': 'PASS', 'source_sites_or_models_called': False,
              'test_cleanup_scope': 'only ' + TEST_DB}
    (ROOT / 'docs/1102_V3/T02/postgres-test-target.json').write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    runtime.dispose()
    admin.dispose()


if __name__ == '__main__':
    main()
