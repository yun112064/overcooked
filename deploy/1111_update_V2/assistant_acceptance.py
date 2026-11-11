import json,time,httpx
from pathlib import Path
p=Path('.test-runtime/1111_update_V2');a=json.loads((p/'test-accounts.json').read_text())['user'];report=[]
with httpx.Client(base_url='http://localhost:8240/api',headers={'Origin':'http://localhost:8240'},timeout=650,trust_env=False) as c:
 c.post('/auth/login',json=a).raise_for_status()
 for q in ['火星藍色水晶果有哪些營養？','水蓮有哪些營養特點？請簡短說明。']:
  start=time.monotonic();events=[]
  with c.stream('POST','/assistant/chat',json={'message':q,'history':[],'engine':'local'}) as r:
   r.raise_for_status();event=None
   for line in r.iter_lines():
    if line.startswith('event: '):event=line[7:]
    elif line.startswith('data: ') and event:
     data=json.loads(line[6:]);events.append({'event':event,'data':data})
     print(event,json.dumps(data,ensure_ascii=True)[:450],flush=True)
  report.append({'question':q,'elapsed_s':round(time.monotonic()-start,2),'events':events});(p/'assistant-bounded-real.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
  assert any(e['event']=='done' for e in events) and not any(e['event']=='error' for e in events)
