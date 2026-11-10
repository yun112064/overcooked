"""Exercise main's unmodified app.main API through the PR production Caddy frontend.
Only callable against the already provisioned, marker-verified disposable DB.
"""
from datetime import datetime
from zoneinfo import ZoneInfo
import httpx
from sqlalchemy import text
from app.db.session import engine

assert engine.url.database == 'meal_planner_1102_v3_ui_demo_test'
with engine.connect() as db:
    assert db.scalar(text("SELECT shobj_description(oid,'pg_database') FROM pg_database WHERE datname=current_database()")) == 'overcooked:disposable:ui-demo'

with httpx.Client(base_url='http://frontend', headers={'Origin':'http://localhost:8080'}, timeout=20) as c:
    def get(path):
        r=c.get('/api'+path); assert r.status_code==200,(path,r.status_code,r.text); return r.json()
    def post(path,body,code=200):
        r=c.post('/api'+path,json=body); assert r.status_code==code,(path,r.status_code,r.text); return r.json() if r.content else None
    health=c.get('/api/health/db')
    assert health.status_code==200
    assert 'X-Overcooked-Environment' not in health.headers, 'Synthetic API adapter must not be active'
    r=c.post('/api/auth/login',json={'email':'demo@example.test','password':'DemoLocal2026!'})
    assert r.status_code==200,r.text
    assert 'HttpOnly' in r.headers['set-cookie'] and 'Secure' in r.headers['set-cookie'] and 'SameSite=lax' in r.headers['set-cookie']
    # HTTPX intentionally does not send Secure cookies over HTTP; browser cookie
    # persistence is independently verified in Chrome on localhost:8080.
    c.headers['Cookie']='mp_session='+r.cookies['mp_session']
    assert get('/auth/me')['current_household']
    homes=get('/household')
    post('/household/select',{'household_id':homes['current_household_id']})
    options=get('/members/options')
    person=post('/members',{'display_name':'PR36隔離驗證','birth_date':'1996-01-01','equation_sex':'F','weight_kg':'55',
        'activity_category':'sedentary','goal':'maintain','portion':options['portions'][0],
        'allergen_ingredient_ids':[],'avoided_ingredient_ids':[]},201)
    change={'display_name':'PR36隔離驗證完成','birth_date':'1996-01-01','equation_sex':'F','weight_kg':'56',
        'activity_category':'sedentary','goal':'maintain','portion':options['portions'][0],
        'allergen_ingredient_ids':[],'avoided_ingredient_ids':[],'version':person['version']}
    updated=c.put('/api/members/'+person['id'],json=change)
    assert updated.status_code==200,updated.text
    assert c.put('/api/members/'+person['id'],json=change).status_code==409
    assert c.delete('/api/members/'+person['id']).status_code==204
    members=get('/members')['members']
    assert get('/ingredients?q=番茄')['ingredients']
    assert 'none_value' in get('/meal-requests/staple-options')
    draft={'mode':'questionnaire','meal_date':str(datetime.now(ZoneInfo('Asia/Taipei')).date()),'meal_type':'dinner','budget':'500',
        'staple':'不需要主食','dish_count':1,'taste_preferences':'','specified_dishes':['番茄炒蛋'],
        'participants':[{'member_id':members[0]['id'],'dining':True,'bento':False,'bento_meal':'lunch'}],
        'available_ingredient_ids':[]}
    row=post('/meal-requests',draft,201); rid=row['id']
    update={'version':row['version'],'draft':{**draft,'budget':'501'}}
    changed=c.put('/api/meal-requests/'+rid,json=update)
    assert changed.status_code==200 and changed.json()['version']==row['version']+1,changed.text
    assert c.put('/api/meal-requests/'+rid,json=update).status_code==409
    assert get('/meal-requests/'+rid)['id']==rid
    assert len(get('/meal-requests/'+rid+'/recipe-catalog')['dishes'])==6
    menu=get('/meal-requests/'+rid+'/recipes')
    selected=[r['recipe_id'] for r in menu['selected']]
    assert selected
    preview=c.post('/api/meal-requests/'+rid+'/nutrition-preview',json={'recipe_ids':selected})
    assert preview.status_code in (200,503), (preview.status_code,preview.text)
    if preview.status_code==200:
        assert preview.json()['adoption_blocked'] or preview.json()['status']!='nutrition_passed', 'An empty demo nutrition DB must not certify real nutrients'
    print('Actual main nutrition path:',preview.status_code,preview.json().get('error',{}).get('code',preview.json().get('status')))
    assert get('/meal-requests/'+rid+'/results')['results']==[]
    get('/meal-plans');get('/meal-plans/trend?days=7')
    assert c.get('/api/admin').status_code==404
    assert c.get('/api/internal/knowledge').status_code==404
    wrong=c.post('/api/auth/login',headers={'Origin':'http://not-allowed.invalid'},json={'email':'demo@example.test','password':'DemoLocal2026!'})
    assert wrong.status_code==403
    assert c.get('/plan').status_code==200
    print('PASS: production frontend /api proxy, original app.main, Secure/HttpOnly cookie contract, household, member CRUD, ingredient search, draft/version conflicts, recipe catalog/retrieval, history reads, SPA fallback and admin/internal boundary')
