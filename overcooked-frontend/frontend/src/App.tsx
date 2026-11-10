import { useCallback, useEffect, useRef, useState } from 'react'
import { CalendarDays, ChefHat, House, LogOut, Users } from 'lucide-react'
import type { Draft, Plan, Scenario, Session } from './domain/types.ts'
import type { MemberFields } from './domain/schemas.ts'
import { createDraft, PlannerError, validateDraft } from './domain/rules.ts'
import { scenarios } from './data/catalog.ts'
import { useAppData } from './hooks/useAppData.ts'
import { demoPlanner } from './services/planner.ts'
import { ErrorText, Modal } from './components/Common.tsx'
import { AuthPage, HomePage } from './pages/Landing.tsx'
import { FamilyPage, MemberPage } from './pages/Members.tsx'
import { ConditionsPage, IngredientsPage } from './pages/Planner.tsx'
import { ResultsPage } from './pages/Results.tsx'
import type { ResultPhase } from './pages/Results.tsx'
import { HistoryPage } from './pages/History.tsx'
import './App.css'

const sessionKey = 'today-eat-demo-session'
function readSession(): Session | null {
  try { const value: unknown = JSON.parse(sessionStorage.getItem(sessionKey) ?? 'null'); return value && typeof value === 'object' && 'email' in value && typeof value.email === 'string' ? { email: value.email } : null } catch { return null }
}
function readRoute() { return window.location.hash.slice(1) || '/home' }
export default function App() {
  const [scenario, setScenario] = useState<Scenario>('normal')
  const store = useAppData(scenario)
  const [session, setSession] = useState(readSession), [route, setRoute] = useState(readRoute)
  const [pendingForm, setPendingForm] = useState(false)
  const [pending, setPending] = useState<string | null>(null), [newDraftModal, setNewDraftModal] = useState(false)
  const [notice, setNotice] = useState(''), [plan, setPlan] = useState<Plan | null>(null), [phase, setPhase] = useState<ResultPhase>('idle'), [resultError, setResultError] = useState('')
  const pageDirty = useRef(false), generation = useRef<AbortController | null>(null)
  const household = store.data.households.find(item => item.id === store.data.activeHouseholdId)!
  const members = store.data.members.filter(member => member.householdId === household.id)
  const draft = store.data.drafts.find(item => item.householdId === household.id)
  const saved = { status: store.status, saveError: store.saveError, retry: () => { store.saveNow() } }
  const commitRoute = useCallback((target: string, replace = false) => {
    pageDirty.current = false
    if(target !== '/results' && generation.current) { generation.current.abort(); generation.current = null; setPhase('idle') }
    if(target === '/auth') { sessionStorage.removeItem(sessionKey); setSession(null) }
    if(replace) history.replaceState(null, '', `#${target}`); else if(window.location.hash !== `#${target}`) history.pushState(null, '', `#${target}`)
    setRoute(target); setNotice(''); window.scrollTo({ top: 0, behavior: 'instant' })
  }, [])
  function navigate(target: string) { if(store.status !== 'saved' || pageDirty.current) { setPendingForm(pageDirty.current); setPending(target); return }; commitRoute(target) }
  useEffect(() => {
    const changed = () => {
      const target = readRoute(); if(target === route) return
      if(!session) { commitRoute('/auth', true); return }
      if(store.status !== 'saved' || pageDirty.current) { history.replaceState(null, '', `#${route}`); setPendingForm(pageDirty.current); setPending(target); return }
      commitRoute(target, true)
    }
    const beforeUnload = (event: BeforeUnloadEvent) => { if(store.status !== 'saved' || pageDirty.current) { event.preventDefault(); event.returnValue = '' } }
    window.addEventListener('popstate', changed); window.addEventListener('hashchange', changed); window.addEventListener('beforeunload', beforeUnload)
    return () => { window.removeEventListener('popstate', changed); window.removeEventListener('hashchange', changed); window.removeEventListener('beforeunload', beforeUnload) }
  }, [route, session, store.status, commitRoute])
  useEffect(() => () => generation.current?.abort(), [])
  function patchDraft(fields: Partial<Draft>) {
    store.update(data => ({ ...data, drafts: data.drafts.map(item => item.householdId === household.id ? { ...item, ...fields, id: item.id, householdId: item.householdId, revision: item.revision + 1, updatedAt: new Date().toISOString() } : item) }))
    setPlan(null); setPhase('idle')
  }
  function startFresh() {
    if(!members.length) { navigate('/family'); setNotice('請先新增家庭成員。'); return }
    const next = createDraft(household.id, members)
    store.update(data => ({ ...data, drafts: [...data.drafts.filter(item => item.householdId !== household.id), next] })); setPlan(null); setPhase('idle'); setNewDraftModal(false); commitRoute('/plan/conditions')
  }
  function start() { if(draft) setNewDraftModal(true); else startFresh() }
  function selectHousehold(id: string) { store.update(data => ({ ...data, activeHouseholdId: id })); setPlan(null); setPhase('idle') }
  function selectPlanningHousehold(id: string) {
    store.update(data => ({ ...data, activeHouseholdId: id, drafts: data.drafts.some(item => item.householdId === id) ? data.drafts : [...data.drafts, createDraft(id, data.members)] })); setPlan(null); setPhase('idle')
  }
  function saveMember(fields: MemberFields): boolean {
    const id = route.split('/')[2], isNew = id === 'new'
    const member = { ...fields, householdId: household.id, id: isNew ? crypto.randomUUID() : id }
    const current = store.current.current
    const next = { ...current, members: isNew ? [...current.members, member] : current.members.map(person => person.id === id && person.householdId === household.id ? member : person), drafts: current.drafts.map(item => item.householdId === household.id ? { ...item, participation: isNew ? [...item.participation, { memberId: member.id, dining: true, bento: false, bentoMeal: 'lunch' as const }] : item.participation, revision: item.revision + 1, updatedAt: new Date().toISOString() } : item) }
    if(!store.commit(next)) return false
    setPlan(null); setPhase('idle'); commitRoute('/family'); return true
  }
  function moveStep(target: string) { if(store.saveNow()) commitRoute(target); else { setPendingForm(false); setPending(target) } }
  async function generate() {
    if(generation.current) return
    const current = store.current.current, request = current.drafts.find(item => item.householdId === current.activeHouseholdId)
    if(!request) { commitRoute('/home'); return }
    const errors = validateDraft(request, current.members)
    if(errors.length) { commitRoute('/plan/conditions'); setNotice(errors.join('\n')); return }
    if(!store.saveNow()) { setPendingForm(false); setPending('/plan/ingredients'); return }
    commitRoute('/results'); setPhase('loading'); setPlan(null); setResultError('')
    const controller = new AbortController(); generation.current = controller
    try {
      const next = await demoPlanner.generate(structuredClone(request), structuredClone(current.members), structuredClone(current.records), scenario, controller.signal)
      if(controller.signal.aborted) return
      setPlan(next); setPhase('ready')
    } catch(error) {
      if(controller.signal.aborted) return
      setResultError(error instanceof Error ? error.message : '暫時無法產生菜單。')
      setPhase(error instanceof PlannerError && error.kind !== 'service' ? 'no-solution' : 'service-error')
    } finally { if(generation.current === controller) generation.current = null }
  }
  function adopt(accepted: boolean): boolean {
    if(!plan) return false
    const current = store.current.current, request = current.drafts.find(item => item.id === plan.draftId)
    if(!request) return false
    try {
      const record = demoPlanner.adopt(plan, request, current.members, current.records, accepted)
      if(current.records.some(item => item.id === record.id)) return true
      return store.commit({ ...current, records: [...current.records, record] })
    } catch(error) { setNotice(error instanceof Error ? error.message : '無法保存這份菜單。'); return false }
  }
  function login(email: string) {
    try { sessionStorage.setItem(sessionKey, JSON.stringify({ email })); setSession({ email }); commitRoute('/home', true) }
    catch { setNotice('無法保存示範登入狀態，請確認瀏覽器允許工作階段儲存。') }
  }
  if(store.fatalError) return <main className="startup-error panel"><h1>無法讀取本機資料</h1><ErrorText>{store.fatalError}</ErrorText><button type="button" className="button" onClick={() => window.location.reload()}>重新載入</button></main>
  let content
  if(route === '/home' || route === '/auth') content = <HomePage draft={draft} start={start} resume={() => navigate('/plan/conditions')} history={() => navigate('/history')} />
  else if(route === '/family') content = <FamilyPage households={store.data.households} householdId={household.id} members={members} select={selectHousehold} create={name => { const id = crypto.randomUUID(); const ok = store.commit({ ...store.current.current, households: [...store.current.current.households, { id, name }], activeHouseholdId: id }); if(!ok) setNotice('建立家庭時保存失敗，請重試。'); return ok }} edit={id => navigate(`/members/${id ?? 'new'}`)} start={start} />
  else if(route.startsWith('/members/')) {
    const id = route.split('/')[2], member = members.find(item => item.id === id)
    content = id === 'new' || member ? <MemberPage key={route} member={member} save={saveMember} cancel={() => navigate('/family')} dirty={() => { pageDirty.current = true }} /> : <section className="panel empty-state"><h2>找不到這位成員</h2><button type="button" className="button" onClick={() => navigate('/family')}>返回家庭成員</button></section>
  } else if(route === '/plan/conditions' && draft) content = <ConditionsPage draft={draft} members={members} households={store.data.households} patch={patchDraft} selectHousehold={selectPlanningHousehold} next={() => moveStep('/plan/ingredients')} home={() => navigate('/home')} saved={saved} />
  else if(route === '/plan/ingredients' && draft) content = <IngredientsPage draft={draft} patch={patchDraft} back={() => moveStep('/plan/conditions')} generate={() => void generate()} scenario={scenario} saved={saved} />
  else if(route === '/results') content = <ResultsPage phase={phase} plan={plan} error={resultError} draft={draft} members={store.data.members} records={store.data.records} regenerate={() => void generate()} modify={() => draft ? navigate('/plan/conditions') : start()} confirm={adopt} />
  else if(route === '/history') content = <HistoryPage key={household.id} records={store.data.records.filter(record => record.plan.householdId === household.id)} home={() => navigate('/home')} />
  else content = <section className="panel empty-state"><h2>請先開始規劃</h2><button type="button" className="button primary" onClick={() => navigate('/home')}>返回首頁</button></section>
  return <><div className="demo-banner"><span><strong>示範模式</strong> 資料保存在此瀏覽器；登入、辨識、價格、營養與分餐皆使用示範服務。</span><label>預覽情境<select aria-label="預覽情境" value={scenario} onChange={event => setScenario(event.target.value as Scenario)}>{scenarios.map(item => <option key={item.value} value={item.value}>{item.label}</option>)}</select></label></div>{!session ? <><AuthPage onLogin={login} /><ErrorText>{notice}</ErrorText></> : <div className="app-shell"><header className="app-header"><button className="brand brand-button" type="button" onClick={() => navigate('/home')}><span className="brand-mark"><ChefHat size={24} /></span>今天吃啥</button><nav aria-label="主要導覽"><button type="button" aria-current={route === '/home' ? 'page' : undefined} onClick={() => navigate('/home')}><House size={17} />首頁</button><button type="button" aria-current={route === '/family' || route.startsWith('/members') ? 'page' : undefined} onClick={() => navigate('/family')}><Users size={17} />家庭成員</button><button type="button" aria-current={route === '/history' ? 'page' : undefined} onClick={() => navigate('/history')}><CalendarDays size={17} />已採用紀錄</button></nav><div className="account"><span>{household.name}</span><button type="button" onClick={() => navigate('/auth')}><LogOut size={16} />登出</button></div></header><main className={`main-content ${route === '/home' ? 'home-main' : ''}`}><ErrorText>{notice}</ErrorText>{content}<footer className="app-footer"><span>今天吃啥 · 家庭餐食規劃</span><span>示範資料，非個人營養建議</span></footer></main></div>}{pending && <Modal title="變更尚未保存" onClose={() => setPending(null)}><p>離開可能會遺失剛才填寫的內容。</p>{pendingForm && <p className="muted small">成員表單的修改請先留在此頁按「儲存」。</p>}<ErrorText>{store.saveError}</ErrorText><div className="modal-actions"><button type="button" className="button" onClick={() => setPending(null)}>留在此頁</button><button type="button" className="button primary" disabled={pendingForm} onClick={() => { if(!pageDirty.current && store.saveNow()) { const target = pending; setPending(null); commitRoute(target) } }}>重新保存並離開</button></div><button type="button" className="discard-button" onClick={() => { const target = pending; store.discard(); setPending(null); commitRoute(target) }}>放棄變更並離開</button></Modal>}{newDraftModal && <Modal title="目前已有一份草稿" onClose={() => setNewDraftModal(false)}><p>要繼續上次的規劃，或重新開始？</p><div className="modal-actions"><button type="button" className="button" onClick={() => { setNewDraftModal(false); navigate('/plan/conditions') }}>繼續草稿</button><button type="button" className="button primary" onClick={startFresh}>重新開始</button></div></Modal>}</>
}
