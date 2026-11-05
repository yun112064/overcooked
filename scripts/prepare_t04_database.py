"""Back up and migrate only the dedicated V3 runtime database for T04."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]

from sqlalchemy import create_engine, text
from alembic import command
from alembic.config import Config
from scripts.t04_db_support import guard, snapshot, HEAD


def main():
    from app.core.config import settings
    engine = create_engine(settings.database_url)
    guard(engine)
    folder = ROOT / 'docs/1102_V3/T04'
    folder.mkdir(parents=True, exist_ok=True)
    with engine.connect() as connection:
        connection.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        initial = snapshot(connection)
        current = connection.scalar(text('SELECT version_num FROM alembic_version'))
        connection.rollback()
    if current not in {'d1103b02a001', HEAD}:
        raise ValueError('Unexpected V3 schema version; refusing migration')
    baseline_path = folder/'before.json'
    if not baseline_path.exists():
        baseline_path.write_text(json.dumps({'database':'meal_planner_1102_v3', 'head':current, **initial}, indent=2)+'\n', encoding='utf-8')
    baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
    if any(initial['table_digests'].get(t) != value for t,value in baseline['table_digests'].items()
           if t != 'alembic_version'):
        raise ValueError('V3 contents changed since the T04 baseline was captured')
    backup = ROOT/'.test-runtime/t04/before.sql'
    backup.parent.mkdir(parents=True, exist_ok=True)
    metadata_path = folder/'backup.json'
    if backup.exists():
        metadata = json.loads(metadata_path.read_text(encoding='utf-8'))
        if hashlib.sha256(backup.read_bytes()).hexdigest() != metadata['sha256']:
            raise ValueError('Existing T04 backup hash differs')
    else:
        with backup.open('wb') as output:
            subprocess.run(['docker','exec','overcooked-1102-v3-db','pg_dump','-U','postgres',
                            '-d','meal_planner_1102_v3','--no-owner','--no-privileges'], stdout=output, check=True)
        metadata = {'database':'meal_planner_1102_v3', 'path':'.test-runtime/t04/before.sql',
                    'bytes':backup.stat().st_size, 'sha256':hashlib.sha256(backup.read_bytes()).hexdigest()}
        metadata_path.write_text(json.dumps(metadata, indent=2)+'\n', encoding='utf-8')
    cfg = Config(str(ROOT/'alembic.ini'))
    cfg.set_main_option('script_location',str(ROOT/'migrations'))
    cfg.attributes['database_url'] = engine.url.render_as_string(hide_password=False)
    cfg.attributes['configure_logger'] = False
    command.upgrade(cfg, HEAD)
    command.check(cfg)
    with engine.connect() as connection:
        after = snapshot(connection)
        assert connection.scalar(text('SELECT version_num FROM alembic_version')) == HEAD
    if any(after['table_digests'].get(t) != value for t,value in baseline['table_digests'].items()
           if t != 'alembic_version'):
        raise ValueError('Migration modified existing source/runtime data')
    result = {'database':'meal_planner_1102_v3','head':HEAD,'alembic_check':'PASS',
              'existing_table_contents_unchanged':True,'physical_tables':len(after['table_counts'])}
    (folder/'migration-result.json').write_text(json.dumps(result,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(result))
    engine.dispose()


if __name__ == '__main__':
    main()
