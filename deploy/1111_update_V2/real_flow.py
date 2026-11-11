from setup_accounts import *
main.timeout=httpx.Timeout(180)
form={'meal_date':datetime.now(ZoneInfo('Asia/Taipei')).date().isoformat(),'meal_type':'dinner','mode':'questionnaire','budget':'1000','staple':'不需要主食','dish_count':1,'specified_dishes':['簡單蒜頭雞(電鍋版)'],'available_ingredient_ids':['FOOD001578'],'participants':[{'member_id':members[0]['id'],'dining':True}]}
draft=call(main,'POST','/meal-requests',code=201,json=form); path='/meal-requests/'+draft['id']
result={'synthetic_food_data':False,'test_profile':True,'scenario':'Chicken already available at home; purchase garlic using real current quote','draft':draft}
def save(): (HERE/'real-flow.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
save()
r=main.post(path+'/nutrition-preview',json={'recipe_ids':['R0036']});result['preview']={'status':r.status_code,'body':r.json()};save(); print('PREVIEW',r.status_code,r.text[:1500],flush=True)
job=call(main,'POST',path+'/submit',code=202,json={'version':draft['version']},headers={'Idempotency-Key':uuid4().hex})['job']
for n in range(360):
 job=call(main,'GET','/jobs/'+job['id'])
 if job['status'] in {'succeeded','failed','cancelled'}:break
 time.sleep(2)
result['job']=job;save();print('JOB',json.dumps(job,ensure_ascii=False)[:5000],flush=True)
if job['status']!='succeeded':raise SystemExit(2)
body={'meal_request_id':draft['id'],'request_version':draft['version'],'result_version':job['result_version']}; key=uuid4().hex
result['adopted']=call(main,'POST','/meal-plans',code=201,json=body,headers={'Idempotency-Key':key});save()
print('ADOPTED',result['adopted']['meal_plan']['id'],flush=True)
