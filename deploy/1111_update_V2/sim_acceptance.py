from setup_accounts import *
result=json.loads((HERE/'real-flow.json').read_text(encoding='utf-8'))
plan=result['adopted']['meal_plan']['id']
handoff=call(main,'POST','/sim-market/handoffs',json={'plan_id':plan});token=parse_qs(urlsplit(handoff['checkout_url']).fragment)['handoff'][0]
with httpx.Client(base_url=MARKET,headers={'Origin':MARKET},trust_env=False,timeout=60) as market:
 cart=call(market,'POST','/market-api/checkout',json={'token':token})
 assert cart['catalog']['real_checkout'] is True
 product=next(p for p in cart['catalog']['products'] if p['food_id']=='FOOD000039')
 body={'token':token,'quantities':{'FOOD000039':1},'quote_ids':{'FOOD000039':product['quote_snapshot_id']}}
 receipt=call(market,'POST','/market-api/orders',json=body)
 assert call(market,'POST','/market-api/orders',json=body)==receipt
 reopened=call(market,'POST','/market-api/checkout',json={'token':token});assert reopened['order']==receipt
flows=call(admin,'GET','/admin/v1/meal-confirmation-flows')['items']
flow=next(f for f in flows if f['trace_id']==plan)
assert sum(e['stage']=='sim_checkout_confirmed' for e in flow['events'])==1
report={'synthetic_food_or_prices':False,'plan_id':plan,'receipt':receipt,'admin_flow':flow,'idempotent':True,'reopen_persisted':True}
(HERE/'sim-real-flow.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print('REAL SIM',receipt['order_id'],'cents',receipt['package_total_cents'],'stages',[e['stage'] for e in flow['events']],flush=True)
