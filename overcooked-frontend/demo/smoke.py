"""Exercise the isolated environment via its real HTTP API, never production."""
from datetime import date, timedelta
import time
import uuid
import httpx

with httpx.Client(base_url='http://127.0.0.1:8000', headers={'Origin': 'http://localhost:5173'}, timeout=20) as c:
    def get(path):
        r = c.get(path); assert r.status_code == 200, (path, r.status_code, r.text)
        return r.json()
    def post(path, body, code=200, **kwargs):
        r = c.post(path, json=body, **kwargs); assert r.status_code == code, (path, r.status_code, r.text)
        return r.json() if r.content else None
    health = c.get('/health/db')
    assert health.headers.get('X-Overcooked-Environment') == 'synthetic-ui-demo'
    assert health.status_code == 200
    post('/auth/login', {'email': 'demo@example.test', 'password': 'DemoLocal2026!'})
    assert get('/auth/me')['current_household']
    members = get('/members')['members']
    draft = {'mode': 'questionnaire', 'meal_date': str(date.today() - timedelta(days=1)), 'meal_type': 'dinner',
        'budget': '500', 'staple': '不需要主食', 'dish_count': 3, 'taste_preferences': '',
        'specified_dishes': ['番茄炒蛋', '蒜炒高麗菜', '青江菜豆腐湯'],
        'participants': [{'member_id': m['id'], 'dining': True, 'bento': False, 'bento_meal': 'lunch'} for m in members],
        'available_ingredient_ids': []}
    row = post('/meal-requests', draft, code=201)
    rid = row['id']
    catalog = get(f'/meal-requests/{rid}/recipe-catalog')
    assert len(catalog['dishes']) == 6
    recipes = get(f'/meal-requests/{rid}/recipes')['selected']
    preview = post(f'/meal-requests/{rid}/nutrition-preview', {'recipe_ids': [r['recipe_id'] for r in recipes]})
    assert preview['status'] == 'nutrition_passed'
    job = post(f'/meal-requests/{rid}/submit', {'version': row['version']}, code=202,
        headers={'Idempotency-Key': str(uuid.uuid4())})['job']
    for _ in range(30):
        job = get('/jobs/' + job['id'])
        if job['status'] in ('succeeded', 'failed'): break
        time.sleep(.5)
    assert job['status'] == 'succeeded', job
    summary = get(f'/meal-requests/{rid}/results')['results'][0]
    result = get(f'/meal-requests/{rid}/results/{summary["result_version"]}')
    assert result['eligibility']['adoptable'], result['eligibility']
    plan = post('/meal-plans', {'meal_request_id': rid, 'result_version': result['result_version'], 'request_version': row['version']}, code=201,
        headers={'Idempotency-Key': str(uuid.uuid4())})['meal_plan']
    history = get('/meal-plans?date=' + draft['meal_date'])
    assert any(item['id'] == plan['id'] for item in history['meal_plans'])
    print('PASS: real API login, cookie, members, draft, 6-recipe catalog, synthetic nutrition preview, worker generation, adoption and persisted history')
