"""HTTP-only T16 real-data acceptance on the explicitly isolated V2 entry point.

Creates labelled acceptance questionnaires/events. Does not modify source data,
invoke Docker, publish policies, import fixtures, or bypass price exceptions.
"""
import argparse
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / '.test-runtime/1111_update_V2'
DISH = '\u7c21\u55ae\u849c\u982d\u96de(\u96fb\u934b\u7248)'


def check(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=True, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def call(client, method, path, *, status=200, **kwargs):
    response = client.request(method, path, **kwargs)
    check(response.status_code == status,
          f'{method} {path}: expected {status}, got {response.status_code}: {response.text[:600]}')
    return response.json()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://localhost:8240')
    parser.add_argument('--accounts', type=Path, default=STATE / 'test-accounts.json')
    parser.add_argument('--flow', type=Path, help='Existing successful real-flow JSON becomes day one; no source/job copying.')
    parser.add_argument('--day-one', type=date.fromisoformat, help='Defaults to Taipei yesterday when no --flow is supplied.')
    parser.add_argument('--output', type=Path, default=STATE / 't16-acceptance.json')
    parser.add_argument('--timeout', type=int, default=900)
    parser.add_argument('--resume', type=Path, help='Resume a report after both day-two result versions already exist; submits no generation jobs.')
    parser.add_argument('--recheck-snapshots', type=Path,
                        help='Read snapshots from a previous report and compare hashes after an independently performed update.')
    args = parser.parse_args()
    check(not args.resume or args.flow is not None, '--resume requires --flow and never creates a new day-one job.')
    origin = args.base_url.rstrip('/')
    parsed = urlsplit(origin)
    check(parsed.scheme == 'http' and parsed.hostname in {'localhost', '127.0.0.1'}
          and parsed.port == 8240, 'This acceptance script is restricted to isolated V2 port 8240.')
    accounts = json.loads(args.accounts.read_text(encoding='utf-8'))
    report = {'scenario': '1111_update_V2 T16 acceptance', 'test_profile': True,
        'synthetic_food_data': False, 'base_url': origin, 'checks': {}, 'plans': [],
        'limitations': ['No source publication/update is performed by this script; snapshot re-read alone is not proof of update invariance.',
                        'Does not exercise duplicate dish identity across two distinct adopted meals on the same prior date.']}
    if args.resume:
        report = json.loads(args.resume.read_text(encoding='utf-8'))

    def save():
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    with httpx.Client(base_url=origin + '/api', headers={'Origin': origin},
                      trust_env=False, timeout=180) as client:
        call(client, 'POST', '/auth/login', json=accounts['user'])
        if args.recheck_snapshots:
            previous = json.loads(args.recheck_snapshots.read_text(encoding='utf-8'))
            for item in previous['plans']:
                detail = call(client, 'GET', '/meal-plans/' + item['id'])
                check(digest(detail['snapshot']) == item['snapshot_sha256'], 'Adopted snapshot changed: ' + item['id'])
                report['plans'].append(item)
            report['checks']['saved_snapshot_hashes_unchanged'] = True
            report['source_update_evidence'] = 'Update must be evidenced separately; this is only the HTTP snapshot comparison.'
            save()
            print(json.dumps({'ok': True, 'report': str(args.output), 'scope': 'snapshot re-read only'}))
            return
        members = call(client, 'GET', '/members')['members']
        check(bool(members), 'Run setup_accounts.py first to create labelled acceptance members.')
        form = {'meal_type': 'dinner', 'mode': 'questionnaire', 'budget': '1000',
            'staple': '\u4e0d\u9700\u8981\u4e3b\u98df', 'dish_count': 1,
            'specified_dishes': [DISH], 'available_ingredient_ids': ['FOOD001578'],
            'participants': [{'member_id': members[0]['id'], 'dining': True}]}

        def create(day, *, specified=True):
            payload = {**form, 'meal_date': day.isoformat(), 'specified_dishes': [DISH] if specified else []}
            draft = call(client, 'POST', '/meal-requests', status=201, json=payload)
            report.setdefault('request_ids', []).append(draft['id'])
            save()
            return draft

        def generate(draft):
            path = '/meal-requests/' + draft['id']
            preview = call(client, 'POST', path + '/nutrition-preview', json={'recipe_ids': ['R0036']})
            check(preview.get('status') == 'nutrition_passed',
                  'Stop before generation: real R0036 nutrition preview is not feasible.')
            selection = call(client, 'GET', path + '/recipes')
            check(any(d.get('recipe_id') == 'R0036' for d in selection.get('selected', [])),
                  'Stop before generation: R0036 is not selected; do not intentionally request AI fallback.')
            job = call(client, 'POST', path + '/submit', status=202,
                       headers={'Idempotency-Key': 't16-submit-' + uuid4().hex},
                       json={'version': draft['version']})['job']
            report.setdefault('jobs', []).append(job)
            save()
            deadline = time.monotonic() + args.timeout
            while job['status'] not in {'succeeded', 'failed', 'cancelled'} and time.monotonic() < deadline:
                time.sleep(2)
                job = call(client, 'GET', '/jobs/' + job['id'])
            report['jobs'] = [item for item in report['jobs'] if item['id'] != job['id']] + [job]
            save()
            check(job['status'] == 'succeeded', 'Real generation did not succeed: ' + json.dumps(job, ensure_ascii=True)[:700])
            check((job.get('result_summary') or {}).get('mode') == 'database_menu',
                  'Unexpected AI fallback; stop and report instead of continuing acceptance.')
            return job

        def body(draft, job):
            return {'meal_request_id': draft['id'], 'request_version': draft['version'],
                    'result_version': job['result_version']}

        def record_plan(plan_id):
            detail = call(client, 'GET', '/meal-plans/' + plan_id)
            check(any(d.get('recipe_id') == 'R0036' for d in detail['dishes']), 'Expected real R0036 adoption.')
            report['plans'] = [item for item in report['plans'] if item['id'] != plan_id]
            report['plans'].append({'id': plan_id, 'meal_date': detail['meal_date'],
                'snapshot_sha256': digest(detail['snapshot']), 'snapshot_versions': detail.get('versions'),
                'recipe_sources': detail['snapshot'].get('recipe_sources'), 'prices': detail.get('prices')})
            save()
            return detail

        def confirmation_events(plan_id):
            admin_origin = 'http://127.0.0.1:8242'
            with httpx.Client(base_url=admin_origin + '/api', headers={'Origin': admin_origin},
                              trust_env=False, timeout=30) as admin:
                call(admin, 'POST', '/auth/login', json=accounts['operator'])
                flows = call(admin, 'GET', '/admin/v1/meal-confirmation-flows')['items']
                matches = [flow for flow in flows if flow['trace_id'] == plan_id]
                check(len(matches) == 1, 'Admin must expose one confirmation flow for the adopted plan.')
                return matches[0]['events']

        if args.flow:
            flow = json.loads(args.flow.read_text(encoding='utf-8'))
            check(flow.get('synthetic_food_data') is False and flow['job']['status'] == 'succeeded', 'Flow must contain successful real data.')
            first_draft, first_job = flow['draft'], flow['job']
            first_day = date.fromisoformat(first_draft['draft']['meal_date'])
            check(args.day_one in (None, first_day), '--day-one must match supplied flow.')
            first_id = flow['adopted']['meal_plan']['id']
        else:
            first_day = args.day_one or datetime.now(ZoneInfo('Asia/Taipei')).date() - timedelta(days=1)
            first_draft = create(first_day)
            first_job = generate(first_draft)
            first_id = call(client, 'POST', '/meal-plans', status=201,
                headers={'Idempotency-Key': 't16-day1-' + uuid4().hex}, json=body(first_draft, first_job))['meal_plan']['id']
        record_plan(first_id)
        second_day = first_day + timedelta(days=1)
        report['days'] = [first_day.isoformat(), second_day.isoformat()]

        # Observe exclusion before today's explicit exception is introduced.
        ordinary = (call(client, 'GET', '/meal-requests/' + report['request_ids'][0])
                    if args.resume else create(second_day, specified=False))
        retrieval = call(client, 'GET', '/meal-requests/' + ordinary['id'] + '/recipes')
        context = retrieval.get('previous_day') or {}
        check(any(r['id'] == first_id for r in context.get('records', [])), 'Yesterday adopted plan missing from retrieval history.')
        check(not any(d.get('recipe_id') == 'R0036' for d in retrieval.get('selected', [])), 'Yesterday recipe repeated without explicit request.')
        check(bool(context.get('excluded_dishes')), 'No previous-day exclusions recorded.')
        report['checks']['previous_day_excluded_without_specification'] = {'request_id': ordinary['id'], 'context': context}

        today = (call(client, 'GET', '/meal-requests/' + report['request_ids'][-1])
                 if args.resume else create(second_day))
        specified = call(client, 'GET', '/meal-requests/' + today['id'] + '/recipes')
        check(any(d.get('recipe_id') == 'R0036' for d in specified.get('selected', [])), 'Explicit requested dish exception did not select R0036.')
        report['checks']['explicit_specified_exception'] = specified.get('previous_day')
        if args.resume:
            observed_jobs = call(client, 'GET', '/jobs', params={'meal_request_id': today['id']})['jobs']
            report['jobs'] = observed_jobs
            original_job = next(j for j in observed_jobs if j['input_version'] == 1 and j['status'] == 'succeeded')
            job = next(j for j in observed_jobs if j['input_version'] == today['version'] and j['status'] == 'succeeded')
            check(today['version'] > 1 and job['result_summary']['mode'] == 'database_menu', 'Resume requires both real database-menu result versions.')
            revised = today
            today = {**today, 'version': original_job['input_version']}
            save()
        else:
            original_job = generate(today)
        # A meaningful questionnaire edit must invalidate an already generated version.
        changed = {**today['draft'], 'budget': '1001'}
        if not args.resume:
            revised = call(client, 'PUT', '/meal-requests/' + today['id'],
                           json={'version': today['version'], 'draft': changed})
        stale = client.post('/meal-plans', json=body(today, original_job),
                            headers={'Idempotency-Key': 't16-stale-' + uuid4().hex})
        check(stale.status_code == 409 and stale.json()['error']['code'] == 'VERSION_CONFLICT',
              'Old generated result was not refused after questionnaire revision: ' + stale.text[:300])
        report['checks']['stale_result_rejected'] = stale.json()
        if not args.resume:
            job = generate(revised)
        report['checks']['t15_result_versions'] = call(client, 'GET', '/meal-requests/' + today['id'] + '/results')
        key = 't16-day2-' + uuid4().hex
        adopt_body = body(revised, job)
        adopted = call(client, 'POST', '/meal-plans', status=201,
                       json=adopt_body, headers={'Idempotency-Key': key})
        today_id = adopted['meal_plan']['id']
        before = call(client, 'GET', '/meal-plans', params={'date': second_day.isoformat()})
        events_before = confirmation_events(today_id)
        replay = call(client, 'POST', '/meal-plans', json=adopt_body, headers={'Idempotency-Key': key})
        after = call(client, 'GET', '/meal-plans', params={'date': second_day.isoformat()})
        check(replay['meal_plan']['id'] == today_id and replay['created'] is False and before == after,
              'Idempotent replay produced a different plan or history.')
        check(confirmation_events(today_id) == events_before, 'Idempotent replay created additional confirmation events.')
        report['checks']['idempotency_confirmation_events'] = events_before
        report['checks']['idempotency_same_plan_and_history'] = True
        record_plan(today_id)
        dates = {r['meal_date'] for r in call(client, 'GET', '/meal-plans')['dates']}
        check(set(report['days']) <= dates, 'Both adopted dates must appear in history.')
        trend = call(client, 'GET', '/meal-plans/trend', params={'days': 2, 'until': second_day.isoformat()})
        check(set(report['days']) <= {r['meal_date'] for r in trend['items']}, 'Two-day trend missing a date.')
        report['checks']['two_day_history_and_trend'] = trend

        # Operator uses a different acceptance household; app-layer ownership must reject access.
        with httpx.Client(base_url=origin + '/api', headers={'Origin': origin}, trust_env=False, timeout=30) as outsider:
            call(outsider, 'POST', '/auth/login', json=accounts['operator'])
            if not call(outsider, 'GET', '/household')['current_household_id']:
                call(outsider, 'POST', '/household', status=201, json={'name': 'T16 cross-household acceptance'})
            for path in ('/meal-plans/' + today_id, '/meal-requests/' + today['id'], '/jobs/' + job['id']):
                check(outsider.get(path).status_code == 404, 'Cross-household access was not hidden: ' + path)
            foreign_adopt = outsider.post('/meal-plans', json=adopt_body,
                                          headers={'Idempotency-Key': 't16-foreign-' + uuid4().hex})
            check(foreign_adopt.status_code == 404, 'Cross-household adoption was not refused.')
        report['checks']['cross_household_read_and_adopt_refused'] = True
        for item in report['plans']:
            current = call(client, 'GET', '/meal-plans/' + item['id'])
            check(digest(current['snapshot']) == item['snapshot_sha256'], 'Saved snapshot changed during acceptance.')
        report['checks']['snapshot_readback_unchanged'] = True
        report['completed'] = True
        save()
        print(json.dumps({'ok': True, 'report': str(args.output), 'plans': [p['id'] for p in report['plans']]}))


if __name__ == '__main__':
    main()
