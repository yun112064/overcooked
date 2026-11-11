"""Provision one full-capability acceptance login, exclusively in the V2 sandbox.

Set ACCEPTANCE_ADMIN_LOGIN and ACCEPTANCE_ADMIN_PASSWORD in the local process.
Secrets travel to docker exec over stdin, never arguments or console output.
Existing self-grant, review and publication restrictions remain intact.
"""
import json
import os
from pathlib import Path
import subprocess
from contextlib import contextmanager
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / '.test-runtime/1111_update_V2'
PROJECT = 'overcooked-1111_update_v2'
BACKEND = PROJECT + '-backend-1'
DATABASE = 'meal_planner_1102_v3_1111_update_v2'
ORIGIN = 'http://127.0.0.1:8242'


def docker(*args, payload=None):
    result = subprocess.run(['docker', *args], input=payload, text=True,
                            capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError('Guarded Docker operation failed; output suppressed to protect credentials')
    return result.stdout.strip()


def call(client, method, path, **kwargs):
    response = client.request(method, path, **kwargs)
    if response.status_code not in (200, 201):
        raise RuntimeError(f'{method} {path} failed ({response.status_code})')
    return response.json()


@contextmanager
def client(account):
    result = httpx.Client(base_url=ORIGIN + '/api', headers={'Origin': ORIGIN},
                          timeout=30, trust_env=False)
    try:
        call(result, 'POST', '/auth/login', json=account)
        yield result
    finally:
        result.close()


def grants(actor, user):
    return call(actor, 'GET', '/admin/v1/role-grants', params={'user_id': user})


def grant(actor, user, role):
    current = grants(actor, user)
    existing = next((item for item in current['items'] if item['role'] == role), None)
    if existing:
        if set(existing['domain_scope']) != set(current['domains']) or existing['expires_at']:
            raise RuntimeError('Existing role has a different scope or expiry; refusing replacement')
        return existing['grant_id']
    result = call(actor, 'POST', '/admin/v1/role-grants',
                  headers={'Idempotency-Key': uuid4().hex}, json={
                      'user_id': user, 'role': role, 'domain_scope': current['domains'],
                      'expires_at': None, 'expected_access_revision': current['access_revision'],
                      'reason': 'V2 isolated single-account acceptance provisioning'})
    return result['resource_id']


def main():
    login = os.environ['ACCEPTANCE_ADMIN_LOGIN'].strip().lower()
    password = os.environ['ACCEPTANCE_ADMIN_PASSWORD']
    if not login or not password or len(login) > 254 or len(password) > 128:
        raise RuntimeError('Invalid local acceptance credential')
    label = docker('inspect', '--format', '{{index .Config.Labels "overcooked.scope"}}', PROJECT + '-db-1')
    if label != 'isolated-1111_update_v2':
        raise RuntimeError('V2 scope mismatch')
    actual = docker('exec', BACKEND, 'python', '-c',
                    "from app.db.session import engine; from sqlalchemy import text; "
                    "c=engine.connect(); print(c.scalar(text('select current_database()'))); c.close()")
    if actual != DATABASE:
        raise RuntimeError('V2 database mismatch')
    path = STATE / 'test-accounts.json'
    accounts = json.loads(path.read_text(encoding='utf-8'))
    policy = json.loads((ROOT / 'backend/app/management/contracts/permissions.json').read_text(encoding='utf-8'))
    with client(accounts['admin']) as admin, client(accounts['operator']) as operator:
        admin_id = call(admin, 'GET', '/auth/me')['user']['id']
        operator_id = call(operator, 'GET', '/auth/me')['user']['id']
        if admin_id == operator_id:
            raise RuntimeError('Two distinct actors required')
        temp = grant(admin, operator_id, 'access_admin')
        try:
            for role in policy['role_presets']:
                grant(operator, admin_id, role)
            caps = call(admin, 'GET', '/admin/v1/capabilities')
            expected = {p for values in policy['role_presets'].values() for p in values}
            if set(caps['roles']) != set(policy['role_presets']) or set(caps['permissions']) != expected:
                raise RuntimeError('Incomplete capabilities')
        finally:
            current = grants(admin, operator_id)
            call(admin, 'POST', f'/admin/v1/role-grants/{temp}/revoke',
                 headers={'Idempotency-Key': uuid4().hex}, json={
                     'expected_access_revision': current['access_revision'],
                     'reason': 'Remove temporary V2 acceptance provisioning access'})
        if any(g['role'] == 'access_admin' for g in grants(admin, operator_id)['items']):
            raise RuntimeError('Temporary operator access remains')
    # Only credentials/session state changes directly; all grants above use audited APIs.
    code = '''import json,sys,uuid
from sqlalchemy import select,delete,text
from app.db.session import SessionLocal
from app.db.models import User,UserSession
from app.services.identity import hash_password
p=json.load(sys.stdin)
with SessionLocal.begin() as db:
 assert db.scalar(text('select current_database()')) == 'meal_planner_1102_v3_1111_update_v2'
 user=db.get(User,uuid.UUID(p['id']))
 assert user and user.email == p['previous']
 other=db.scalar(select(User.id).where(User.email==p['login']))
 assert other is None or other == user.id
 user.email=p['login']; user.password_hash=hash_password(p['password'])
 db.execute(delete(UserSession).where(UserSession.user_id==user.id))
'''
    docker('exec', '-i', BACKEND, 'python', '-c', code,
           payload=json.dumps({'id': admin_id, 'previous': accounts['admin']['email'],
                               'login': login, 'password': password}))
    accounts['admin'] = {'email': login, 'password': password}
    # Persist before login verification, allowing recovery if HTTP is interrupted.
    path.write_text(json.dumps(accounts, indent=2), encoding='utf-8')
    with client(accounts['admin']) as admin:
        caps = call(admin, 'GET', '/admin/v1/capabilities')
        assert caps['user_id'] == admin_id
        assert set(caps['permissions']) == expected
    evidence = {'database': DATABASE, 'user_id': admin_id, 'roles': caps['roles'],
                'permissions': caps['permissions'], 'temporary_operator_access_removed': True,
                'sessions_revoked': True, 'same_identity_preserved': True}
    (STATE / 'acceptance-admin-evidence.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    print(f"V2 acceptance identity verified: {len(caps['roles'])} roles, {len(caps['permissions'])} permissions; temporary access removed.")


if __name__ == '__main__':
    main()
