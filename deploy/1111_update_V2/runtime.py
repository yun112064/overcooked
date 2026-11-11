"""V2 isolated lifecycle. All sources are real; credentials stay outside Git."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from urllib.parse import quote, urlsplit, unquote

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / '.test-runtime/1111_update_V2'
PROJECT = 'overcooked-1111_update_v2'
DB = 'meal_planner_1102_v3_1111_update_v2'
DEPLOY = ROOT / 'deploy/1111_update_V2'

def run(args, **kwargs):
    return subprocess.run(args, cwd=ROOT, check=True, **kwargs)

def read_env(path):
    result = {}
    if path.exists():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            if '=' in line and not line.startswith('#'):
                key, value = line.split('=', 1)
                result[key] = json.loads(value) if value.startswith('"') else value
    return result

def prepare():
    STATE.mkdir(parents=True, exist_ok=True)
    values = read_env(STATE / 'runtime.env')
    for key in ('E2E_DB_PASSWORD', 'E2E_SIM_KEY', 'INFERENCE_TOKEN', 'KNOWLEDGE_SERVICE_TOKEN',
                'ASSISTANT_SERVICE_TOKEN', 'T11_DB_PASSWORD', 'ASSISTANT_DB_PASSWORD'):
        values.setdefault(key, secrets.token_hex(24))
    values.setdefault('APP_DATABASE_URL', 'postgresql+psycopg://not_installed@db/' + DB)
    values.update(read_env(STATE / 'secrets.env'))
    (STATE / 'runtime.env').write_text(''.join(k+'='+json.dumps(v)+'\n' for k,v in values.items()), encoding='utf-8')
    return values

def compose(*args, **kwargs):
    command = ['docker', 'compose', '--project-name', PROJECT, '--env-file', str(STATE/'runtime.env'),
               '-f', str(DEPLOY/'compose.yaml'), '-f', str(DEPLOY/'services/compose.yaml')]
    local = STATE/'compose.local.yaml'
    if local.exists():
        command += ['-f', str(local)]
    return run(command+list(args), **kwargs)

def guard():
    label=run(['docker','inspect','--format','{{index .Config.Labels "overcooked.scope"}}',PROJECT+'-db-1'],capture_output=True,text=True).stdout.strip()
    if label != 'isolated-1111_update_v2':
        raise RuntimeError('V2 database scope mismatch')

def validate_business_env(values):
    """An env file is a credential artifact, not proof of installation scope."""
    for key in ('DATABASE_URL', 'CRAWLER_DATABASE_URL', 'MANAGEMENT_WORKER_DATABASE_URL'):
        if key not in values and key != 'DATABASE_URL':
            continue
        try:
            parsed = urlsplit(values[key])
            valid = (parsed.scheme in ('postgresql', 'postgresql+psycopg')
                     and parsed.hostname == 'db' and parsed.port in (None, 5432)
                     and unquote(parsed.path) == '/' + DB
                     and not parsed.query and not parsed.fragment)
        except (KeyError, ValueError):
            valid = False
        if not valid:
            raise RuntimeError('Installed business credential target does not match isolated V2 scope')

def install():
    installed=STATE/'business.env'
    fresh = not installed.exists()
    if not fresh:
        validate_business_env(read_env(installed))
    values=prepare()
    compose('build','backend','frontend','sim-supermarket','admin-dashboard')
    compose('up','-d','--wait','db')
    guard()
    if fresh:
        environment=dict(os.environ, INSTALL_DATABASE_ADMIN_URL='postgresql+psycopg://postgres:'+quote(values['E2E_DB_PASSWORD'])+'@db:5432/postgres')
        compose('run','--rm','--no-deps','-w','/app','-e','INSTALL_DATABASE_ADMIN_URL',
                '-v',str(STATE).replace('\\','/')+':/app/.test-runtime','backend',
                'python','scripts/install_real_nutrition.py','--database',DB,'--output-env','/app/.test-runtime/business.env',env=environment)
    installed_values=read_env(installed)
    validate_business_env(installed_values)
    values['APP_DATABASE_URL']=installed_values['DATABASE_URL']
    for key in ('CRAWLER_DATABASE_URL','MANAGEMENT_WORKER_DATABASE_URL'):
        if key in installed_values:
            values[key]=installed_values[key]
    (STATE/'runtime.env').write_text(''.join(k+'='+json.dumps(v)+'\n' for k,v in values.items()),encoding='utf-8')
    # Verify the actual app connection as well as the file and container label.
    # A compose override must not silently redirect the verification elsewhere.
    compose('run','--rm','--no-deps','-w','/app','backend','python','-c',
            "from app.db.session import engine; from sqlalchemy import text; "
            "c=engine.connect(); c.execute(text('SET TRANSACTION READ ONLY')); "
            "assert c.scalar(text('SELECT current_database()')) == " + repr(DB) + "; c.close()")
    verification=['run','--rm','--no-deps','-w','/app','backend','python','scripts/verify_portable_nutrition.py']
    if fresh:
        verification.append('--require-empty-runtime')
    compose(*verification)
    (STATE/'installed.json').write_text(json.dumps({'database':DB,'source':'release_data/nutrition-v1','schema':'m1111simorder1'}),encoding='utf-8')
    print('V2 fresh business data initialized and verified.' if fresh else
          'V2 existing source data verified; accounts, orders and other activity preserved.')

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','install','start-core','start','stop','verify'])
    args=parser.parse_args()
    if args.action=='prepare':
        prepare()
    elif args.action=='install':
        install()
    elif args.action=='stop':
        compose('stop')
    else:
        if not (STATE/'installed.json').exists():
            raise RuntimeError('Run install before starting V2.')
        marker=json.loads((STATE/'installed.json').read_text(encoding='utf-8'))
        if not isinstance(marker, dict) or marker.get('database') != DB:
            raise RuntimeError('Installed database marker does not match isolated V2 scope')
        values=prepare()
        validate_business_env({**values, 'DATABASE_URL': values.get('APP_DATABASE_URL', '')})
        guard()
        if args.action=='start-core':
            compose('up','-d','--wait','db','backend','worker','frontend','sim-supermarket','admin-dashboard')
        elif args.action=='start':
            compose('up','-d','--wait')
        else:
            compose('ps')
            compose('exec','-T','backend','python','-m','app.core.schema_gate')

if __name__=='__main__':
    try:
        main()
    except subprocess.CalledProcessError:
        print('V2 operation failed; existing volumes preserved. No credentials are printed.',file=sys.stderr)
        raise SystemExit(1)
