/**
 * 整個場景與面板第一次畫出來的 HTML（沒有瀏覽器，不跑 effect、不發請求）。
 * 資料用開發用的假後端（dev/fake）產生，先放進快取；這裡只能確認「畫面上寫了什麼、有哪些按鈕」，
 * 位置、大小與互動（按下之後）要在瀏覽器裡看。
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import type { ReactElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { FakeState } from '../../../dev/fake/core.ts'
import { handle } from '../../../dev/fake/router.ts'
import { createState } from '../../../dev/fake/seed.ts'
import type { Job, MealRequest, Member, NutritionPreview, PersonPortion } from '../../api/types'
import { membersQuery } from '../../session/queries'
import RecipeBookScene from './RecipeBookScene'
import { GenerationPanel, PeoplePanel } from './ResultPanels'
import { initialPicks, pickerDraft } from './bookLogic'
import { generationStatus } from './resultStatus'
import type { Generation } from './useGeneration'

// 景深設定讀 localStorage；這裡沒有瀏覽器，換成固定值
vi.mock('../../depth/DepthProvider', () => ({ useDepth: () => ({ mode: 'static', setMode: () => {} }) }))

let state: FakeState

function call<T>(method: string, path: string, body: unknown = {}): T {
  const res = handle(state, { method, path, query: {}, headers: {}, body })
  expect(res.status, `${method} ${path} → ${JSON.stringify(res.body)}`).toBeLessThan(300)
  return res.body as T
}

const noop = () => {}
const text = (html: string) => html.replace(/<[^>]+>/g, '')

/** 一張食譜書挑菜的草稿（第一位成員一起吃）。 */
function pickerRow(): { row: MealRequest; members: Member[] } {
  const members = call<{ members: Member[] }>('GET', '/members').members
  const picks = initialPicks(members, [])
  picks[members[0].id] = { dining: true, bento: false }
  return { row: call<MealRequest>('POST', '/meal-requests', pickerDraft('dinner', picks, undefined, '2026-11-20')), members }
}

// retryOnMount: false：一次都沒讀到的查詢，畫出來的是「已經失敗」的那一刻，而不是掛上去時馬上重試的讀取中
function newClient(): QueryClient {
  return new QueryClient({ defaultOptions: { queries: { retry: false, retryOnMount: false } } })
}

function wrap(client: QueryClient, path: string, element: ReactElement): string {
  return renderToStaticMarkup(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/pick/:id" element={element} />
          <Route path="*" element={element} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

function renderScene(client: QueryClient, id: string, [w, h]: readonly [number, number] = [390, 844]): string {
  vi.stubGlobal('window', { innerWidth: w, innerHeight: h })
  return wrap(client, `/pick/${id}`, <RecipeBookScene />)
}

/** 讓某個查詢變成「讀取失敗」（快取裡原本有資料的話會留著，和背景重讀失敗一樣）。 */
async function fail(client: QueryClient, queryKey: readonly unknown[]) {
  await client.prefetchQuery({ queryKey, queryFn: () => Promise.reject(new Error('offline')), staleTime: 0 })
}

beforeEach(() => {
  state = createState({ now: () => new Date(2026, 10, 14, 12, 0, 0).getTime() })
  call('POST', '/auth/login', { email: 'demo@example.com', password: 'password123' })
  const { households } = call<{ households: { id: string; name: string }[] }>('GET', '/household')
  call('POST', '/household/select', { household_id: households.find((h) => h.name === '林家廚房')!.id })
})
afterEach(() => vi.unstubAllGlobals())

describe('讀取失敗', () => {
  it('這張單一次都沒讀到：整頁是錯誤說明，「開一張新的」是紙上的按鈕（夠大好按）', async () => {
    const { row, members } = pickerRow()
    const client = newClient()
    client.setQueryData(membersQuery.queryKey, members)
    await fail(client, ['meal-request', row.id])
    const html = renderScene(client, row.id)
    expect(text(html)).toContain('發生錯誤，請稍後再試。')
    expect(text(html)).not.toContain('這一餐，誰要吃？')
    expect(html).toMatch(/<a[^>]*class="[^"]*ink[^"]*"[^>]*href="\/pick"[^>]*>開一張新的<\/a>/)
  })

  it('已經翻開之後背景重讀失敗：書還在（畫面上的選擇不會被丟掉），另外寫一則讀不到的訊息', async () => {
    const { row, members } = pickerRow()
    const client = newClient()
    client.setQueryData(membersQuery.queryKey, members)
    client.setQueryData(['meal-request', row.id], row)
    await fail(client, ['meal-request', row.id])
    const html = renderScene(client, row.id)
    expect(text(html)).toContain('這一餐，誰要吃？')
    expect(html).toMatch(new RegExp(`<input[^>]*aria-label="${members[0].display_name}一起吃"[^>]*checked=""`))
    expect(text(html)).toContain('最新的資料讀不到，畫面上是上一次讀到的內容。')
    // 這裡是剛掛上去的那一刻（馬上又在重讀），按鈕寫「重試中…」；重讀又失敗後才是「再試一次」
    expect(text(html)).toMatch(/再試一次|重試中…/)
    expect(text(html)).not.toContain('開一張新的')
  })

  it('成員清單背景重讀失敗也一樣不擋畫面', async () => {
    const { row, members } = pickerRow()
    const client = newClient()
    client.setQueryData(membersQuery.queryKey, members)
    client.setQueryData(['meal-request', row.id], row)
    await fail(client, membersQuery.queryKey)
    const html = renderScene(client, row.id)
    expect(text(html)).toContain('這一餐，誰要吃？')
    expect(text(html)).toContain('最新的資料讀不到，畫面上是上一次讀到的內容。')
  })
})

describe('手機版面', () => {
  function scene(size: readonly [number, number], patch: Partial<MealRequest> = {}): string {
    const { row, members } = pickerRow()
    const client = newClient()
    client.setQueryData(membersQuery.queryKey, members)
    client.setQueryData(['meal-request', row.id], { ...row, ...patch })
    return renderScene(client, row.id, size)
  }

  it('沒有側邊的「‹ 上一段／下一段 ›」按鈕（會壓在書頁的字上）；換頁靠物件列', () => {
    const html = scene([390, 844])
    expect(html).toContain('data-layout="phone"')
    expect(html).not.toContain('<span aria-hidden="true"> ›</span>')
    const dock = /<nav[^>]*data-dock[^>]*>(.*?)<\/nav>/.exec(html)?.[1] ?? ''
    expect(text(dock)).toContain('誰要吃')
    expect(text(dock)).toContain('說明')
    expect(text(dock)).toContain('闔上書')
  })

  it('平板橫放（沒有物件列，一頁被切掉）保留側邊按鈕', () => {
    const html = scene([1024, 768])
    expect(html).toContain('data-layout="wide"')
    expect(html).toContain('說明<span aria-hidden="true"> ›</span>')
  })

  it('畫面矮（390×664）：托盤右邊讓出吉祥物站的地方；夠高（390×844）就不用讓', () => {
    expect(scene([390, 664])).toMatch(/--tray-clear:\d+px/)
    expect(scene([390, 844])).not.toContain('--tray-clear')
  })

  it('已採用：只能瀏覽，「重新挑一次」是紙上的按鈕', () => {
    const html = scene([390, 844], { status: 'confirmed' })
    expect(text(html)).toContain('這餐已採用，只能瀏覽')
    expect(html).toMatch(/<a[^>]*class="[^"]*ink[^"]*"[^>]*href="\/pick"[^>]*>重新挑一次<\/a>/)
  })
})

describe('PeoplePanel（每人營養）', () => {
  const portion = (intake: Record<string, string | null>): PersonPortion => ({
    participation_id: 'p1', n: '1', share: '1', meal: 'dinner', meal_ratio: '0.4', dish_share: '1', dish_ids: [],
    staple_g: null, intake, known_partial: {},
  })
  const nutrition = (intake: Record<string, string | null>) => ({
    people: [{
      member_id: 'm1', name: '阿明', kinds: ['dining'], bento: null, dining: portion(intake),
      daily: {
        sodium_mg: { kind: 'max', max: '2300', label: '鈉上限' },
        sugars_g: { kind: 'max', max: '50', label: '添加糖上限' },
        protein_g: { kind: 'target', value: '60', label: '蛋白質' },
      },
    }],
  }) as unknown as NutritionPreview
  const panel = (intake: Record<string, string | null>) => text(wrap(newClient(), '/', (
    <PeoplePanel nutrition={nutrition(intake)} names={new Map()} dishName={new Map()} staple={null} onClose={noop} />
  )))

  it('鈉、糖超標寫成字，不只靠長條的顏色', () => {
    // 鈉 2500 > 每日上限 2300；糖 30 ÷ 50 = 60%，超過這一餐的 40%
    const page = panel({ sodium_mg: '2500', sugars_g: '30', protein_g: '90' })
    expect(page).toContain('超過每日上限')
    expect(page).toContain('超過這一餐的份額')
    // 蛋白質是建議值不是上限：吃超過不標
    expect(page.match(/超過/g)).toHaveLength(2)
  })

  it('沒超標不寫；未知不當成 0，也不標超標', () => {
    const page = panel({ sodium_mg: '600', sugars_g: null, protein_g: '20' })
    expect(page).not.toContain('超過')
    expect(page).toContain('未知')
  })
})

describe('GenerationPanel（工作狀態讀不到）', () => {
  function generationOf(patch: Partial<Generation>): Generation {
    return {
      job: null, running: false, requestLocked: false, jobError: null, jobLoading: false, retryJob: noop,
      forThisVersion: false, confirmed: false, hasResult: false, complete: true,
      results: { data: undefined, isError: false, isPending: true, isFetching: false },
      latestResultVersion: null, team: null, canSubmit: false, canRegenerate: false,
      submitting: false, cancelling: false, submit: noop, cancel: noop, submitError: null, cancelError: null,
      ...patch,
    } as unknown as Generation
  }
  const panel = (row: MealRequest, generation: Generation) =>
    wrap(newClient(), '/', <GenerationPanel row={row} generation={generation} onClose={noop} />)

  it('一次都沒讀到：寫出讀不到並給「再試一次」；這張單還在生成中時說明會自動更新', () => {
    const row = { ...pickerRow().row, status: 'running' } as MealRequest
    const generation = generationOf({ requestLocked: true, jobError: new Error('offline') })
    const html = panel(row, generation)
    expect(text(html)).toContain('讀不到生成進度')
    expect(text(html)).toContain('這張單還在生成中，連上之後會自動更新。')
    expect(html).toMatch(/<button[^>]*>再試一次<\/button>/)
    // 右頁按鈕與物件列上的狀態字也寫出來
    expect(generationStatus(generation)).toBe('讀不到進度')
  })

  it('輪詢到一半斷線：保留上一次讀到的階段，另外說明連線中斷', () => {
    const row = { ...pickerRow().row, status: 'running' } as MealRequest
    const job = {
      id: 'j1', kind: 'meal_generation', status: 'running', stage: 'calculating', attempts: 1, max_attempts: 3,
      meal_request_id: row.id, input_version: row.version, result_version: null, error: null,
    } as Job
    const html = panel(row, generationOf({
      job, running: true, forThisVersion: true, requestLocked: true, jobError: new Error('offline'),
    }))
    expect(text(html)).toContain('計算營養與價格')
    expect(text(html)).toContain('進度停在上一次讀到的階段；連上之後會自動更新。')
    expect(text(html)).not.toContain('讀不到生成進度')
    expect(html).toMatch(/<button[^>]*>取消生成<\/button>/)
  })

  it('讀得到時不出現這些訊息', () => {
    const html = panel(pickerRow().row, generationOf({}))
    expect(text(html)).not.toContain('讀不到生成進度')
    expect(text(html)).not.toContain('連線中斷')
  })
})
