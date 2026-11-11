"""One real AI-menu request through HTTP and T10; never call the provider directly."""
from setup_accounts import *
import argparse

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--scenario', choices=['matching', 'different-stock'], default='matching')
args = parser.parse_args()

report_path = HERE / ('ai-flow-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '.json')
form = {
    'meal_date': datetime.now(ZoneInfo('Asia/Taipei')).date().isoformat(),
    'meal_type': 'dinner', 'mode': 'questionnaire', 'budget': '1000',
    'staple': '不需要主食', 'dish_count': 1,
    'specified_dishes': ['蒜香清蒸雞胸肉'],
    'available_ingredient_ids': [('FOOD001564' if args.scenario == 'matching' else 'FOOD001578'), 'FOOD000039'],
    'participants': [{'member_id': members[0]['id'], 'dining': True}],
}
draft = call(main, 'POST', '/meal-requests', code=201, json=form)
report = {'synthetic_food_data': False, 'test_profile': True, 'scenario': args.scenario, 'draft': draft}

def save():
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

save()
job = call(main, 'POST', '/meal-requests/' + draft['id'] + '/submit', code=202,
           json={'version': draft['version']}, headers={'Idempotency-Key': uuid4().hex})['job']
previous = None
deadline = time.monotonic() + 720
while time.monotonic() < deadline:
    job = call(main, 'GET', '/jobs/' + job['id'])
    report['job'] = job
    save()
    stage = (job['status'], job.get('stage'), job.get('error_code'))
    if stage != previous:
        print('JOB', job['id'], *stage, flush=True)
        previous = stage
    if job['status'] in {'succeeded', 'failed', 'cancelled'}:
        break
    time.sleep(2)

query = '''import json,sys,uuid
from sqlalchemy import select,text
from app.db.session import SessionLocal
from app.db.models import UsageReservation
with SessionLocal() as db:
 db.execute(text('SET TRANSACTION READ ONLY'))
 assert db.scalar(text('select current_database()')) == 'meal_planner_1102_v3_1111_update_v2'
 rows=db.scalars(select(UsageReservation).where(UsageReservation.job_id==uuid.UUID(sys.argv[1]))).all()
 print(json.dumps([{'purpose':r.purpose,'provider':r.provider,'model':r.model,'status':r.status,'reserved_twd':str(r.reserved_twd),'actual_twd':str(r.actual_twd),'usage':r.usage} for r in rows]))
'''
report['usage'] = json.loads(subprocess.check_output(
    ['docker', 'exec', BACKEND, 'python', '-c', query, job['id']], text=True))
save()
print('REPORT', report_path.name, 'USAGE', json.dumps(report['usage']), flush=True)
if not any(r['purpose'] == 'menu_generation' and r['status'] == 'settled' for r in report['usage']):
    raise SystemExit('No settled real menu-generation call; not accepted.')
if job['status'] != 'succeeded':
    raise SystemExit('Provider billing verified but complete generation failed; inspect report.')
report['result'] = call(main, 'GET', '/meal-requests/' + draft['id'] + '/results/' + str(job['result_version']))
save()
ingredients = [i for d in report['result']['dishes'] for i in d['ingredients']]
assert any(i['food_id'] == 'FOOD001564' for i in ingredients), 'Requested chicken breast missing'
assert not any(i['food_id'] == 'FOOD001578' for i in ingredients), 'Chicken leg substituted for breast'
assert all(i['food_id'] == 'FOOD001564' for i in ingredients if i['name'] == '雞胸肉')
report['identity_verified'] = True
save()
print('IDENTITY passed; ELIGIBILITY', json.dumps(report['result']['eligibility'], ensure_ascii=True), flush=True)
