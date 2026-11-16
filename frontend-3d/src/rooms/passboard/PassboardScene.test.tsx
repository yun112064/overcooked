/**
 * 整個場景第一次畫出來的 HTML（沒有瀏覽器，不跑 effect、不發請求）。
 * 資料用開發用的假後端（dev/fake）產生，形狀和真後端一致；先把場景會查的東西放進快取，看畫面上寫了什麼。
 * 這裡只能確認「內容與狀態對不對」，位置、大小、互動要在瀏覽器裡看。
 */
import { zodResolver } from '@hookform/resolvers/zod'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { useForm } from 'react-hook-form'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { handle } from '../../../dev/fake/router.ts'
import { createState } from '../../../dev/fake/seed.ts'
import type { FakeState } from '../../../dev/fake/core.ts'
import type { Job, MealPlanSummary, MealRequest, MealRequestSummary, MealResultSummary, Member } from '../../api/types'
import { membersQuery, plansOnDateQuery } from '../../session/queries'
import { AdoptedMenu } from './AdoptedMenu'
import { CookingView } from './Confirm'
import { schema, toForm, type FormValues } from './draft'
import { layoutFor } from './layout'
import { Editor } from './PassboardScene'
import { StepMembers, StepPantry, StepTaste, StepWhen } from './Steps'
import type { Generation } from './useGeneration'

// 景深設定讀 localStorage；這裡沒有瀏覽器，換成固定值
vi.mock('../../depth/DepthProvider', () => ({ useDepth: () => ({ mode: 'static', setMode: () => {} }) }))

const PHONE = [390, 844] as const
const DESKTOP = [1440, 900] as const

let state: FakeState
let clock = 0

function call<T>(method: string, path: string, body: unknown = {}, query: Record<string, string> = {}): T {
  const res = handle(state, { method, path, query, headers: { 'idempotency-key': `test-key-${path}` }, body })
  expect(res.status, `${method} ${path} → ${JSON.stringify(res.body)}`).toBeLessThan(300)
  return res.body as T
}
const get = <T,>(path: string, query: Record<string, string> = {}) => call<T>('GET', path, {}, query)

function login(household: string) {
  call('POST', '/auth/login', { email: 'demo@example.com', password: 'password123' })
  const list = get<{ households: { id: string; name: string }[] }>('/household').households
  call('POST', '/household/select', { household_id: list.find((h) => h.name === household)?.id })
}

/** 種子資料裡某個狀態的問卷（問卷模式）。 */
function seeded(status: string): MealRequest {
  const list = get<{ meal_requests: MealRequestSummary[] }>('/meal-requests').meal_requests
  const hit = list.find((r) => r.status === status && r.mode === 'questionnaire')
  expect(hit, `種子資料沒有 ${status} 的問卷`).toBeDefined()
  return get<MealRequest>(`/meal-requests/${hit!.id}`)
}

/** 把場景會查的東西先放進快取（key 和場景裡用的一樣）。 */
function cacheFor(row: MealRequest): QueryClient {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  client.setQueryData(['me'], get('/auth/me'))
  client.setQueryData(membersQuery.queryKey, get<{ members: Member[] }>('/members').members)
  client.setQueryData(['staple-options'], get('/meal-requests/staple-options'))
  client.setQueryData(['team-status'], get('/health/team'))
  const jobs = get<{ jobs: Job[] }>('/jobs', { meal_request_id: row.id, kind: 'meal_generation' }).jobs
  client.setQueryData(['jobs', row.id], jobs[0] ?? null)
  if (['result_ready', 'awaiting_decision', 'confirmed'].includes(row.status)) {
    client.setQueryData(['meal-results', row.id], get<{ results: MealResultSummary[] }>(`/meal-requests/${row.id}/results`).results)
  }
  // 已採用：只讀保存的紀錄，不查即時菜單
  if (row.status === 'confirmed' && row.draft.meal_date) {
    const date = row.draft.meal_date
    client.setQueryData(plansOnDateQuery(date).queryKey, get<{ meal_plans: MealPlanSummary[] }>('/meal-plans', { date }).meal_plans)
    return client
  }
  return client
}

function markup(client: QueryClient, path: string, element: ReactElement): string {
  return renderToStaticMarkup(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes><Route path="/plan/:id/*" element={element} /></Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

/** 畫出問卷場景。path 預設 /plan/:id；生成中的畫面傳 'cooking'。 */
function render(row: MealRequest, [w, h]: readonly [number, number], sub = ''): string {
  vi.stubGlobal('window', { innerWidth: w, innerHeight: h })
  const client = cacheFor(row)
  const members = client.getQueryData(membersQuery.queryKey) ?? []
  return markup(client, `/plan/${row.id}${sub ? `/${sub}` : ''}`,
    <Editor initial={row} members={members} view={{ w, h }} layout={layoutFor(w, h)} />)
}

const STEP_VIEWS = { when: StepWhen, pantry: StepPantry, taste: StepTaste } as const

/** 單獨畫某一步的欄位（場景裡要按「下一步」才到得了）。 */
function StepHarness({ row, members, step }: { row: MealRequest; members: Member[]; step: keyof typeof STEP_VIEWS | 'members' }) {
  const form = useForm<FormValues>({ resolver: zodResolver(schema), defaultValues: toForm(row, members) })
  const readOnly = row.status !== 'draft'
  if (step === 'members') return <StepMembers form={form} row={row} readOnly={readOnly} members={members} />
  const View = STEP_VIEWS[step]
  return <View form={form} row={row} readOnly={readOnly} />
}

function renderStep(row: MealRequest, step: keyof typeof STEP_VIEWS | 'members'): string {
  const client = cacheFor(row)
  const members = client.getQueryData(membersQuery.queryKey) ?? []
  return markup(client, `/plan/${row.id}`, <StepHarness row={row} members={members} step={step} />)
}

const text = (html: string) => html.replace(/<[^>]+>/g, '')
const potKind = (html: string) => /data-pot="(\w+)"/.exec(html)?.[1]
const potImage = (html: string) => /pot-top-[\w-]+/.exec(html)?.[0]
const buttons = (html: string) => [...html.matchAll(/<button[^>]*>(.*?)<\/button>/g)].map((m) => text(m[1]))
const isDisabled = (html: string, label: string) =>
  new RegExp(`<button[^>]*disabled=""[^>]*>(?:<[^>]+>)*${label}`).test(html)

beforeEach(() => {
  clock = new Date(2026, 10, 14, 12, 0, 0).getTime()
  state = createState({ now: () => clock })
  login('林家廚房')
})
afterEach(() => vi.unstubAllGlobals())

describe('草稿', () => {
  it('手機：鍋在世界裡、問卷在抽屜裡，沒有底部分頁', () => {
    const html = render(seeded('draft'), PHONE)
    expect(html).toContain('data-layout="phone"')
    expect(html).toContain('data-size="half"')
    expect(html).not.toContain('aria-label="主要導覽"')
    expect(html).toContain('回廚房')
    expect(buttons(html)).toContain('收起問卷')
    // 五個步驟都在步驟條上，目前在第 1 步
    expect([...html.matchAll(/第 (\d) 步：/g)].map((m) => m[1])).toEqual(['1', '2', '3', '4', '5'])
    expect(html).toMatch(/aria-current="step"[^>]*>.*?第 1 步/)
    expect(text(html)).toContain('哪一餐？預算多少？')
  })

  it('手機：抽屜半開時飲食小幫手還叫得到，站在抽屜上緣（桌機照常，不用墊高）', () => {
    const phone = render(seeded('draft'), PHONE)
    expect(phone).toContain('data-size="half"')
    expect(phone).toContain('aria-label="飲食小幫手"')
    expect(phone).toContain('data-mascot="lifted"')
    const desktop = render(seeded('draft'), DESKTOP)
    expect(desktop).toContain('aria-label="飲食小幫手"')
    expect(desktop).not.toContain('data-mascot=')
  })

  it('桌機：問卷寫在紙上（沒有抽屜），保留側欄導覽', () => {
    const html = render(seeded('draft'), DESKTOP)
    expect(html).toContain('data-layout="wide"')
    expect(html).not.toContain('data-size=')
    expect(html).toContain('aria-label="主要導覽"')
    expect(text(html)).toContain('餐食日期')
  })

  it('輸入欄位都有標籤', () => {
    const html = render(seeded('draft'), DESKTOP)
    const ids = [...html.matchAll(/<(?:input|select|textarea)[^>]*\sid="([^"]+)"/g)].map((m) => m[1])
    expect(ids.length).toBeGreaterThanOrEqual(3)
    for (const id of ids) expect(html).toContain(`<label for="${id}"`)
  })

  it('沒填完：鍋不是全滿，也還不能煮', () => {
    const row = seeded('draft')
    expect(row.complete).toBe(false)
    const html = render(row, DESKTOP)
    expect(['empty', 'filling']).toContain(potKind(html))
    expect(text(html)).not.toContain('可以開始煮')
  })
})

describe('各步驟的欄位', () => {
  it('第 1 步：日期、餐別、預算', () => {
    const page = text(renderStep(seeded('draft'), 'when'))
    for (const label of ['餐食日期', '餐別', '預算（新台幣）']) expect(page).toContain(label)
  })

  it('第 2 步：每位成員都能勾用餐與帶便當；算不出目標時寫出原因，不寫 0', () => {
    const row = seeded('draft')
    const members = get<{ members: Member[] }>('/members').members
    const html = renderStep(row, 'members')
    const page = text(html)
    for (const m of members) expect(page).toContain(m.display_name)
    expect(html.match(/一起用餐/g)).toHaveLength(members.length)
    expect(html.match(/帶便當/g)?.length).toBeGreaterThanOrEqual(members.length)
    // 種子草稿選了算不出營養目標的阿嬤
    expect(row.targets_preview.status).toBe('unavailable')
    if (row.targets_preview.status === 'unavailable') expect(page).toContain(row.targets_preview.message)
    expect(page).not.toContain('約 0 大卡')
  })

  it('第 3 步：可以拍照、可以搜尋，也寫明可以略過', () => {
    const html = renderStep(seeded('draft'), 'pantry')
    expect(html).toContain('type="file"')
    expect(html).toContain('accept="image/jpeg,image/png,image/webp"')
    expect(html).toContain('type="search"')
    expect(text(html)).toContain('這一步可以略過')
  })

  it('第 4 步：主食只能從清單選；菜數、口味、指定菜色；成員不吃的食材列在下面', () => {
    const row = seeded('draft')
    const html = renderStep(row, 'taste')
    const page = text(html)
    for (const label of ['主食', '菜數', '口味偏好', '指定菜色', '不吃的食材']) expect(page).toContain(label)
    expect(page).toContain('不需要主食')
    for (const r of row.restrictions.ingredients) expect(page).toContain(r.name ?? r.ingredient_id)
  })
})

describe('生成中（/plan/:id/cooking）', () => {
  function cookingRow(): MealRequest {
    const row = seeded('queued')
    // 排隊 5 秒後開始執行
    clock += 8_000
    return get<MealRequest>(`/meal-requests/${row.id}`)
  }

  it('桌機：鍋蓋蓋上，鍋旁貼著後端回報的階段，沒有百分比', () => {
    const row = cookingRow()
    const html = render(row, DESKTOP, 'cooking')
    expect(potKind(html)).toBe('cooking')
    expect(potImage(html)).toBe('pot-top-lidded')
    const card = /<section aria-label="鍋裡的進度">(.*?)<\/section>/.exec(html)?.[1] ?? ''
    expect(card).toContain('aria-label="生成階段"')
    expect(card).toContain('aria-current="step"')
    expect(text(card)).toContain('計算營養與價格')
    expect(text(html)).not.toMatch(/\d\s*%/)
    // 紙上不再列一次階段，但取消與等待時間在
    expect(html.match(/aria-label="生成階段"/g)).toHaveLength(1)
    expect(text(html)).toContain('已等待')
    expect(buttons(html)).toContain('取消生成')
    expect(isDisabled(html, '取消生成')).toBe(false)
    // 生成中沒有步驟條、不能改問卷
    expect(html).not.toContain('aria-label="問卷步驟"')
  })

  it('手機：階段列在抽屜裡（看不到右爐口，不貼在鍋旁）', () => {
    const html = render(cookingRow(), PHONE, 'cooking')
    expect(potKind(html)).toBe('cooking')
    expect(html).not.toContain('鍋裡的進度')
    expect(html.match(/aria-label="生成階段"/g)).toHaveLength(1)
    expect(html).not.toContain('aria-label="主要導覽"')
  })

  it('鍋的小牌寫出目前的階段', () => {
    const row = cookingRow()
    const job = get<{ jobs: Job[] }>('/jobs', { meal_request_id: row.id, kind: 'meal_generation' }).jobs[0]
    expect(job.status).toBe('running')
    const tag = /<span role="status">(.*?)<\/span>/.exec(render(row, DESKTOP, 'cooking'))?.[1] ?? ''
    expect(tag).toMatch(/^生成中・.+/)
  })

  it('進度一次都沒讀到就失敗：寫出原因，可以再試一次；沒有工作可以取消', () => {
    const row = cookingRow()
    const failed = {
      job: null, active: false,
      latest: { isError: true, error: new Error('Network Error'), refetch: () => Promise.resolve() },
      cancel: { isPending: false, isError: false, error: null },
    } as unknown as Generation
    const html = markup(cacheFor(row), `/plan/${row.id}/cooking`,
      <CookingView row={row} generation={failed} showStages onAskCancel={() => {}} />)
    expect(text(html)).toContain('讀不到鍋裡的進度')
    expect(buttons(html)).toContain('再試一次')
    expect(isDisabled(html, '取消生成')).toBe(true)
    expect(html).toContain(`href="/plan/${row.id}"`)
  })

  it('不在 /cooking 時（回看問卷）：表單唯讀，並指到鍋的進度', () => {
    const html = render(cookingRow(), DESKTOP)
    expect(text(html)).toContain('這張單正在煮')
    expect(isDisabled(html, '儲存草稿')).toBe(true)
    expect(isDisabled(html, '開始煮')).toBe(true)
    // 菜單要煮好才端上餐桌：問卷上沒有採用按鈕，但可以取消生成
    expect(buttons(html)).not.toContain('採用這份菜單')
    expect(isDisabled(html, '取消生成')).toBe(false)
  })
})

describe('失敗', () => {
  it('打開在確認步驟：鍋是失敗的樣子，寫出原因，可以再煮一次', () => {
    const row = seeded('failed')
    const job = get<{ jobs: Job[] }>('/jobs', { meal_request_id: row.id, kind: 'meal_generation' }).jobs[0]
    const html = render(row, DESKTOP)
    expect(potKind(html)).toBe('failed')
    expect(potImage(html)).toBe('pot-top-failed')
    expect(html).toMatch(/aria-current="step"[^>]*>.*?第 5 步/)
    expect(text(html)).toContain('這一鍋沒煮成')
    if (job?.error && job.input_version === row.version) expect(text(html)).toContain(job.error.message)
    expect(buttons(html)).toContain('再煮一次')
    expect(isDisabled(html, '再煮一次')).toBe(false)
    expect(text(html)).toContain('失敗・第')
  })

  it('「再煮一次」固定在問卷最下面那一排，不在可捲動的內容裡', () => {
    const html = render(seeded('failed'), PHONE)
    const footer = /<footer[^>]*>(.*?)<\/footer>/.exec(html)?.[1] ?? ''
    expect(buttons(footer)).toEqual(['上一步', '儲存草稿', '再煮一次'])
  })
})

describe('有結果', () => {
  it('可以採用的結果：鍋開蓋，連到最新一版的餐桌，可以重新生成', () => {
    const row = seeded('result_ready')
    const newest = get<{ results: MealResultSummary[] }>(`/meal-requests/${row.id}/results`).results[0].result_version
    const html = render(row, DESKTOP)
    expect(potKind(html)).toBe('ready')
    expect(html).toContain(`href="/plan/${row.id}/result/${newest}"`)
    expect(buttons(html)).toContain('重新生成')
    // 這一版已經煮過：不再擺「開始煮」，也不說「可以開始煮」
    expect(buttons(html)).not.toContain('開始煮')
    expect(text(html)).not.toContain('可以開始煮')
  })

  it('待決定：提醒到餐桌處理缺的資料', () => {
    const html = render(seeded('awaiting_decision'), DESKTOP)
    expect(potKind(html)).toBe('ready')
    expect(text(html)).toContain('請到餐桌決定如何處理')
  })

  it('已採用：只能看，沒有開始煮、重新生成與儲存', () => {
    const row = seeded('confirmed')
    const html = render(row, DESKTOP)
    expect(text(html)).toContain('這張單已經採用，只能瀏覽')
    expect(buttons(html)).not.toContain('開始煮')
    expect(buttons(html)).not.toContain('重新生成')
    expect(isDisabled(html, '儲存草稿')).toBe(true)
    expect(html).toContain(`href="/plan/${row.id}/result/`)
    expect(text(html)).not.toContain('還有項目需要補充')
  })

  it('已採用：只列採用當下保存的菜，並有明顯的「回客廳」', () => {
    const row = seeded('confirmed')
    const client = cacheFor(row)
    const date = row.draft.meal_date ?? ''
    const plan = get<{ meal_plans: MealPlanSummary[] }>('/meal-plans', { date }).meal_plans.find((p) => p.meal_request_id === row.id)
    expect(plan, '種子資料裡已採用的問卷要有一筆已採用紀錄').toBeDefined()
    const html = markup(client, `/plan/${row.id}`, <AdoptedMenu row={row} />)
    const page = text(html)
    expect(html).toContain('aria-label="已採用的菜單"')
    expect(html).toMatch(/<a[^>]*href="\/today"[^>]*>回客廳<\/a>/)
    expect(html).toContain(`href="/records?tab=adopted&amp;date=${date}"`)
    for (const dish of plan!.dishes) expect(page).toContain(dish.dish_name)
    expect(page).not.toContain('整餐熱量')
    expect(buttons(html)).not.toContain('採用這份菜單')
  })

  it('已採用的問卷場景在確認步驟列出已採用的菜單', () => {
    const html = render(seeded('confirmed'), DESKTOP)
    expect(html).toContain('aria-label="已採用的菜單"')
  })
})

describe('填好的草稿（外婆家）', () => {
  beforeEach(() => login('外婆家'))

  it('鍋全滿、可以開始煮', () => {
    const row = seeded('draft')
    expect(row.complete).toBe(true)
    // 填好的草稿從第 1 步開始；確認步驟的內容另外看
    const first = render(row, DESKTOP)
    expect(potKind(first)).toBe('full')
    expect(potImage(first)).toBe('pot-top-5')
    expect(/<span role="status">(.*?)<\/span>/.exec(first)?.[1]).toBe('可以開始煮')
  })

  it('填好了但還沒按開始煮：確認步驟不先擺出菜單，也沒有採用按鈕', () => {
    const row = seeded('draft')
    vi.stubGlobal('window', { innerWidth: DESKTOP[0], innerHeight: DESKTOP[1] })
    const client = cacheFor(row)
    const members = client.getQueryData(membersQuery.queryKey) ?? []
    const html = markup(client, `/plan/${row.id}?step=5`,
      <Editor initial={row} members={members} view={{ w: DESKTOP[0], h: DESKTOP[1] }} layout={layoutFor(...DESKTOP)} />)
    expect(text(html)).toContain('問卷已填寫完整，可以開始煮')
    expect(buttons(html)).toContain('開始煮')
    expect(html).not.toContain('資料庫菜單預覽')
    expect(html).not.toContain('已採用的菜單')
    expect(text(html)).not.toContain('整餐熱量')
    expect(buttons(html)).not.toContain('採用這份菜單')
    expect(buttons(html)).not.toContain('不採用，換一份')
  })
})
