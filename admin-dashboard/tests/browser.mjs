import assert from 'node:assert/strict'
import fs from 'node:fs'
import http from 'node:http'
import net from 'node:net'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { spawn } from 'node:child_process'
import { setTimeout as sleep } from 'node:timers/promises'

// All data is synthetic; no real API, crawler or model calls.
const root = fileURLToPath(new URL('../../', import.meta.url))
const work = path.join(root, '.test-runtime/admin-dashboard-browser')
fs.mkdirSync(work, { recursive: true })
const errors = [], requests = [], sessions = new Map()
let expired = false, logoutFails = false
const send = (response, data, status=200) => { response.writeHead(status, {'Content-Type':'application/json; charset=utf-8', 'Cache-Control':'no-store'}); response.end(JSON.stringify(data)) }
const denied = (response, status, message) => send(response, {error:{code:status===401?'UNAUTHENTICATED':'FORBIDDEN',message}},status)
const server = http.createServer(async (req,res) => {
  const url = new URL(req.url, 'http://localhost')
  const p = url.pathname
  requests.push({path:p,host:req.headers.host,cookie:req.headers.cookie})
  if (p === '/consumer-fixture') { res.setHeader('Set-Cookie', 'mp_session=consumer-only; HttpOnly; SameSite=Lax; Path=/'); res.setHeader('Content-Type','text/html'); res.end('<h1>Consumer fixture</h1>'); return }
  if (p.startsWith('/api/')) {
    const token = /mp_session=([^;]+)/.exec(req.headers.cookie??'')?.[1]
    if (p === '/api/auth/login') {
      let body=''; for await (const chunk of req) body+=chunk
      const data=JSON.parse(body)
      if(data.password!=='fixture-password') return denied(res,401,'Email 或密碼不正確。')
      const token='admin-'+Date.now(); sessions.set(token,data.email)
      res.setHeader('Set-Cookie',`mp_session=${token}; HttpOnly; SameSite=Lax; Path=/`)
      return send(res,{user:{id:'00000000-0000-4000-8000-000000000001',email:data.email},current_household:null})
    }
    if (p === '/api/auth/logout') {
      if(logoutFails) return send(res,{error:{code:'TEMPORARY',message:'fixture'}},503)
      sessions.delete(token);res.setHeader('Set-Cookie','mp_session=; Max-Age=0; Path=/');res.writeHead(204);res.end();return
    }
    const email=sessions.get(token)
    if(!email || expired) return denied(res,401,'請重新登入。')
    if(p==='/api/auth/me') return send(res,{user:{id:'00000000-0000-4000-8000-000000000001',email},current_household:null})
    if(p==='/api/admin/v1/capabilities') {
      if(email==='none@example.test') return denied(res,403,'目前帳號沒有執行此操作的管理權限。')
      const permissions=email==='viewer@example.test'?['management.enter','recipe.read']:['management.enter','recipe.read','recipe.edit','ingredient.read','ingredient.edit','price.read','price.edit','operations.read','registry.read','audit.read','access.read','access.manage']
      return send(res,{user_id:'00000000-0000-4000-8000-000000000001',permissions,readable_domains:email==='viewer@example.test'?['recipe']:['recipe','ingredient','price'],external_execution_enabled:false})
    }
    if(p.endsWith('/overview')) return send(res,{proposals:{recipe:{submitted:2,changes_requested:1}},failed_validation_tasks:0,observed_at:'2026-11-10T00:00:00Z'})
    if(p.endsWith('/metrics/summary')) return send(res,{recipe_count:null,pending_proposal_count:2,running_task_count:0,database_bytes:null,as_of:null})
    if(p.endsWith('/database-status')) return send(res,{status:'not_configured',query_latency_ms:null,client_connections:null,database_bytes:null,checked_at:null})
    if(p.endsWith('/data-types')) return send(res,{items:[{key:'history_snapshot',owner:'T13',phase1_action:'metadata_only',write_policy:'no_direct_write'}],next_cursor:null})
    if(p.endsWith('/schedule-configuration')) return send(res,{items:[],observed_at:null})
    return send(res,{items:[],next_cursor:null})
  }
  const dist=path.join(root,'admin-dashboard/dist')
  const target=path.join(dist,p==='/'?'index.html':p)
  if(!target.startsWith(dist)) {res.writeHead(404);res.end();return}
  const file=fs.existsSync(target)&&fs.statSync(target).isFile()?target:path.join(dist,'index.html')
  res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript':file.endsWith('.css')?'text/css':'text/html')
  res.end(fs.readFileSync(file))
})
const listen = instance => new Promise(resolve => instance.listen(0,'127.0.0.1',()=>resolve(instance.address().port)))
const port=await listen(server)
const BASE='http://admin.localhost:'+port
const reserved=net.createServer(); const debugPort=await listen(reserved);await new Promise(resolve=>reserved.close(resolve))
const chrome=spawn(process.env.CHROME_PATH ?? (process.platform === 'win32' ? 'C:/Program Files/Google/Chrome/Application/chrome.exe' : 'google-chrome'),[
  '--headless=new','--disable-background-networking','--disable-component-update','--disable-sync','--no-first-run','--no-default-browser-check',
  '--remote-debugging-address=127.0.0.1','--remote-debugging-port='+debugPort,'--user-data-dir='+path.join(work,'profile-'+Date.now()),
  '--proxy-server=http://127.0.0.1:1','--proxy-bypass-list=127.0.0.1;localhost;admin.localhost',
  '--host-resolver-rules=MAP admin.localhost 127.0.0.1, MAP localhost 127.0.0.1','--window-size=1440,1000','about:blank'
],{cwd:root,stdio:'ignore',windowsHide:true})
let ws, seq=0;const pending=new Map()
async function until(check,label) { const deadline=Date.now()+10000;while(Date.now()<deadline){const result=await check();if(result)return result;await sleep(100)}throw new Error('Timed out: '+label) }
function call(method,params={}){const id=++seq;return new Promise((resolve,reject)=>{pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}))})}
async function evaluate(fn,...args){const {result,exceptionDetails}=await call('Runtime.evaluate',{expression:'('+fn.toString()+')(...'+JSON.stringify(args)+')',returnByValue:true});if(exceptionDetails)throw new Error(exceptionDetails.text);return result.value}
const text=()=>evaluate(()=>document.body?.innerText??'')
const see=fragment=>until(async()=>(await text()).includes(fragment),fragment)
async function click(label){const point=await until(()=>evaluate(title=>{const el=[...document.querySelectorAll('button')].find(e=>e.textContent.trim()===title&&!e.disabled);if(!el)return null;el.scrollIntoView({block:'center'});const b=el.getBoundingClientRect();return[b.x+b.width/2,b.y+b.height/2]},label),'button '+label);for(const type of ['mousePressed','mouseReleased'])await call('Input.dispatchMouseEvent',{type,x:point[0],y:point[1],button:'left',clickCount:1})}
async function fill(type,value){await evaluate(type=>{const input=document.querySelector('input[type="'+type+'"]');input.focus();input.select()},type);await call('Input.insertText',{text:value})}
async function login(email='admin@example.test',password='fixture-password'){await see('管理員登入');await fill('email',email);await fill('password',password);await click('登入管理網站')}
const run=async(label,task)=>{await task();console.log('PASS '+label)}
try {
  const tab=await until(async()=>{try{return(await(await fetch('http://127.0.0.1:'+debugPort+'/json/list')).json()).find(tab=>tab.type==='page')}catch{return null}},'Chrome')
  ws=new WebSocket(tab.webSocketDebuggerUrl);ws.addEventListener('message',event=>{const m=JSON.parse(event.data);if(m.id){const next=pending.get(m.id);pending.delete(m.id);if(m.error)next?.reject(new Error(m.error.message));else next?.resolve(m.result)}if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails.exception?.description??m.params.exceptionDetails.text)})
  await new Promise(resolve=>ws.addEventListener('open',resolve,{once:true}));await call('Page.enable');await call('Runtime.enable');await call('Network.enable');await call('Network.setBlockedURLs',{urls:['https://*']});await sleep(750)
  await run('separate hostname does not send consumer cookie; dedicated login',async()=>{
    await call('Page.navigate',{url:'http://localhost:'+port+'/consumer-fixture'});await see('Consumer fixture')
    await call('Page.navigate',{url:BASE});await see('管理員登入');assert(!(await text()).includes('建立家庭'));assert(!(await text()).includes('註冊'))
    assert(!requests.filter(r=>r.host.startsWith('admin.localhost')).some(r=>r.cookie?.includes('consumer-only')))
  })
  await run('invalid password stays on login with error',async()=>{await login('admin@example.test','wrong-password');await see('Email 或密碼不正確。')})
  await run('authorized user without a household reaches all management areas',async()=>{
    await login();await see('失敗的提案驗證任務');assert(!(await text()).includes('家庭成員'))
    for(const label of ['食譜與 AI 候選','食材','價格管理']){await click(label);await see('提案與審核')}
    await click('資料庫與任務');await see('監控未接入');await see('連線')
    await click('排程中心');await see('排程設定來自目前 API 環境')
    await click('資料清冊');await see('不可直接修改')
    await click('操作紀錄');await see('沒有符合條件的紀錄')
    await click('角色權限');await see('既有帳號識別碼')
    await click('總覽');await see('失敗的提案驗證任務')
    fs.writeFileSync(path.join(work,'desktop.png'),Buffer.from((await call('Page.captureScreenshot',{format:'png'})).data,'base64'))
  })
  await run('mobile layout remains within viewport',async()=>{await call('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});await sleep(200);assert(await evaluate(()=>document.documentElement.scrollWidth<=innerWidth));fs.writeFileSync(path.join(work,'mobile.png'),Buffer.from((await call('Page.captureScreenshot',{format:'png'})).data,'base64'))})
  await run('failed logout stays signed in; success does not clear consumer session',async()=>{
    logoutFails=true;await click('登出');await see('登出未完成');logoutFails=false;await click('登出');await see('管理員登入')
    const cookies=await call('Network.getCookies',{urls:['http://localhost:'+port]});assert(cookies.cookies.some(c=>c.value==='consumer-only'))
  })
  await run('viewer sees only permitted recipe view and no editing',async()=>{
    await login('viewer@example.test');await see('提案與審核');const t=await text();assert(!t.includes('價格管理'));assert(!t.includes('角色權限'));assert(!t.includes('新增草稿'));await click('登出');await see('管理員登入')
  })
  await run('ungranted user cannot enter management workspace',async()=>{await login('none@example.test');await see('目前帳號沒有執行此操作的管理權限');assert(!(await text()).includes('價格管理'));await click('登出');await see('管理員登入')})
  await run('expired management session returns to login and clears workspace',async()=>{
    await login();await see('失敗的提案驗證任務');expired=true;await click('食材');await see('管理員登入');assert(!(await text()).includes('Overcooked 管理中心'));expired=false
  })
  assert.deepEqual(errors,[])
  console.log('8 browser scenarios passed (synthetic HTTP fixtures, no production API).')
} catch(error) {console.error('Browser failure:',await text(),errors);throw error} finally {if(ws?.readyState===1)ws.close();chrome.kill();server.closeAllConnections();await new Promise(resolve=>server.close(resolve))}
