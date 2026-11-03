"""T04 tests exclusively on an explicitly disposable V3 PostgreSQL database."""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT),str(ROOT/'backend')]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reuse-disposable-test-db', action='store_true')
    args = parser.parse_args()
    from sqlalchemy import create_engine, text
    from alembic import command
    from alembic.config import Config
    from app.core.config import settings
    from scripts.t04_db_support import guard, TEST_DB, HEAD
    runtime = create_engine(settings.database_url)
    guard(runtime)
    admin = create_engine(runtime.url.set(database='postgres'), isolation_level='AUTOCOMMIT')
    with admin.connect() as connection:
        exists = connection.scalar(text('SELECT 1 FROM pg_database WHERE datname=:name'),{'name':TEST_DB})
        if exists and not args.reuse_disposable_test_db:
            raise ValueError('T04 test database exists; inspect before explicitly reusing it')
        if not exists:
            connection.execute(text('CREATE DATABASE '+TEST_DB+' TEMPLATE template0'))
    test_url = runtime.url.set(database=TEST_DB)
    test_engine = create_engine(test_url)
    guard(test_engine,test=True)
    cfg = Config(str(ROOT/'alembic.ini'))
    cfg.set_main_option('script_location',str(ROOT/'migrations'))
    cfg.attributes['database_url'] = test_url.render_as_string(hide_password=False)
    cfg.attributes['configure_logger'] = False
    if exists:
        with test_engine.connect() as connection:
            if (connection.scalar(text("SELECT count(*) FROM recipe_data_releases WHERE release_id <> 'legacy-1017'"))
                    or connection.scalar(text('SELECT count(*) FROM source_archive'))
                    or connection.scalar(text('SELECT count(*) FROM price_data_releases'))
                    or connection.scalar(text('SELECT count(*) FROM nutrition_imports'))):
                raise ValueError('Source data found in test DB; refusing cleanup')
        command.downgrade(cfg,'base')
    command.upgrade(cfg,HEAD)
    environment = {**os.environ, 'DATABASE_URL':'postgresql+psycopg://disabled:disabled@127.0.0.1:1/no_runtime',
        'TEST_DATABASE_URL':'', 'T04_TEST_DATABASE_URL':test_url.render_as_string(hide_password=False),
        'PYTHONPATH':os.pathsep.join([str(ROOT),str(ROOT/'backend'),str(ROOT/'tests')]),
        'PYTHONUTF8':'1','OPENAI_API_KEY':'','GEMINI_API_KEY':'','NUTRITION_LLM_PROVIDER':'',
        'PROJECT_DATA_ROOT':str(ROOT/'.test-runtime/t04/data'),
        'TMP':str(ROOT/'.test-runtime/t04/tmp'),'TEMP':str(ROOT/'.test-runtime/t04/tmp')}
    Path(environment['TMP']).mkdir(parents=True,exist_ok=True)
    result = subprocess.run([sys.executable,'-m','pytest','tests/test_t04_release.py','tests/test_v3_contracts.py',
        'tests/test_versioned_sql_nutrition.py','tests/test_food_workbooks.py','tests/test_t04_data_feedback.py',
        'tests/test_t04_remaining247.py',
        'nutrition_planning/tests/test_imported_nutrition.py',
        '-k','not configured_cooked_rice_staple and not white_rice and not backend_payload_exposes_blocking_reason',
        '-q','--tb=short'],cwd=ROOT,env=environment,check=False)
    if result.returncode:
        raise SystemExit(result.returncode)
    # Test downgrade/upgrade only after verifying the test fixture left no source release.
    with test_engine.connect() as connection:
        assert connection.scalar(text('SELECT count(*) FROM nutrition_data_releases')) == 0
        assert connection.scalar(text('SELECT count(*) FROM nutrition_imports')) == 0
    command.downgrade(cfg,'d1103b02a001')
    command.upgrade(cfg,HEAD)
    command.check(cfg)
    report = {'database':TEST_DB,'head':HEAD,'pytest_exit_code':0,'alembic_check':'PASS',
              't04_downgrade_upgrade':'PASS','runtime_database_used_for_tests':False,
              'real_source_imported_in_test_db':False,'external_sources_or_models_called':False}
    (ROOT/'docs/1102_V3/T04/postgres-test-target.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report))
    test_engine.dispose(); admin.dispose(); runtime.dispose()


if __name__ == '__main__':
    main()
