"""Backup and migrate only V3 for additional source evidence; never replay imports."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT),str(ROOT/'backend')]
from sqlalchemy import create_engine,text
from alembic import command
from alembic.config import Config
from scripts.t04_db_support import guard,snapshot,HEAD


def main():
    from app.core.config import settings
    engine=create_engine(settings.database_url);guard(engine)
    folder=ROOT/'docs/1102_V3/T04'
    with engine.connect() as c:
        c.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'))
        initial=snapshot(c)
        current=c.scalar(text('SELECT version_num FROM alembic_version'))
    if current not in {'d1103c04a001',HEAD}: raise ValueError('Unexpected V3 schema')
    baseline=folder/'supplement-before.json'
    if not baseline.exists():
        if current!='d1103c04a001': raise ValueError('Missing pre-migration baseline')
        baseline.write_text(json.dumps({'head':current,**initial},indent=2)+'\n',encoding='utf-8')
    before=json.loads(baseline.read_text(encoding='utf-8'))
    if any(initial['table_digests'].get(t)!=h for t,h in before['table_digests'].items() if t!='alembic_version'):
        raise ValueError('Original table contents changed')
    backup=ROOT/'.test-runtime/t04-supplement/before.sql'
    backup.parent.mkdir(parents=True,exist_ok=True)
    meta=folder/'supplement-backup.json'
    if backup.exists():
        if hashlib.sha256(backup.read_bytes()).hexdigest()!=json.loads(meta.read_text())['sha256']:
            raise ValueError('Backup changed')
    else:
        with backup.open('wb') as output:
            subprocess.run(['docker','exec','overcooked-1102-v3-db','pg_dump','-U','postgres','-d',
                'meal_planner_1102_v3','--no-owner','--no-privileges'],stdout=output,check=True)
        meta.write_text(json.dumps({'database':'meal_planner_1102_v3','path':str(backup.relative_to(ROOT)),
            'sha256':hashlib.sha256(backup.read_bytes()).hexdigest(),'bytes':backup.stat().st_size},indent=2)+'\n')
    cfg=Config(str(ROOT/'alembic.ini'))
    cfg.set_main_option('script_location',str(ROOT/'migrations'))
    cfg.attributes.update(database_url=engine.url.render_as_string(hide_password=False),configure_logger=False)
    command.upgrade(cfg,HEAD);command.check(cfg)
    with engine.connect() as c: after=snapshot(c)
    assert all(after['table_digests'][t]==h for t,h in before['table_digests'].items() if t!='alembic_version')
    result={'head':HEAD,'database':'meal_planner_1102_v3','physical_tables':len(after['table_counts']),
        'original_tables_unchanged':True,'alembic_check':'PASS'}
    (folder/'supplement-migration.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result))


if __name__=='__main__': main()
