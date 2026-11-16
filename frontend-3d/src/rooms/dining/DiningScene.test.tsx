import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter, Route, Routes } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { Me, MealPlanSummary, MealRequest, MealResult, MealResultSummary } from '../../api/types'
import DiningScene from './DiningScene'
import { mealRequest, mealResult } from './testData'

// 景深設定讀 localStorage；這裡沒有瀏覽器，換成固定值
vi.mock('../../depth/DepthProvider', () => ({ useDepth: () => ({ mode: 'static', setMode: () => {} }) }))

const ME: Me = { user: { id: 'u1', email: 'a@example.com' }, current_household: { id: 'h1', name: '林家廚房' } }
const summary = (version: number): MealResultSummary => ({
  result_version: version, request_version: 3, mode: 'database_menu', mode_label: '資料庫菜單', created_at: '', adoptable: true,
})

type Setup = {
  result?: MealResult | null
  versions?: number[]
  request?: MealRequest
  plans?: MealPlanSummary[]
  path?: string
  width?: number
  height?: number
}

/** 沒有瀏覽器：只給 Room 需要的畫面大小，把資料先放進快取，看第一次畫出來的 HTML。 */
function render({ result = mealResult(), versions = [2], request = mealRequest(), plans, path = '/plan/r1/result/2', width = 390, height = 844 }: Setup = {}): string {
  vi.stubGlobal('window', { innerWidth: width, innerHeight: height })
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  queryClient.setQueryData(['me'], ME)
  queryClient.setQueryData(['meal-results', 'r1'], versions.map(summary))
  queryClient.setQueryData(['meal-request', 'r1'], request)
  if (result) queryClient.setQueryData(['meal-result', 'r1', result.result_version], result)
  if (plans) queryClient.setQueryData(['sim-market-adopted-plan', 'r1', '2026-11-14'], plans)
  return renderToStaticMarkup(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/plan/:id/result/:version" element={<DiningScene />} />
          <Route path="*" element={<p>離開餐桌</p>} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  )
}

const text = (markup: string) => markup.replace(/<[^>]+>/g, '')
/** 物件列（手機）或視覺隱藏的清單（桌機）裡每顆按鈕的文字。 */
function dock(markup: string): string[] {
  const nav = /<nav[^>]*aria-label="餐桌裡可以操作的東西"[^>]*>(.*?)<\/nav>/.exec(markup)?.[1] ?? ''
  return [...nav.matchAll(/<button[^>]*>(.*?)<\/button>/g)].map((m) => text(m[1]))
}
/** 底部動作列裡的按鈕與連結。 */
function actionBar(markup: string): string[] | null {
  const group = /<div[^>]*role="group"[^>]*aria-label="這一桌可以做的事"[^>]*>(.*?)<\/div>/.exec(markup)?.[1]
  if (group === undefined) return null
  return [...group.matchAll(/<(button|a)[^>]*>(.*?)<\/\1>/g)].map((m) => text(m[2]))
}
const hotspotLabels = (markup: string) => [...markup.matchAll(/<button type="button" aria-label="([^"]+)"/g)].map((m) => m[1])
const count = (markup: string, needle: string) => markup.split(needle).length - 1
/** 畫了幾張這個圖（只數 <img>；React 另外輸出的預先載入標籤不算）。 */
const images = (markup: string, name: string) => [...markup.matchAll(/<img[^>]*src="([^"]*)"/g)].filter((m) => m[1].includes(name)).length
const adoptedPlan = (version: number | null): MealPlanSummary => ({
  id: 'p1', meal_date: '2026-11-14', meal_type: 'dinner', mode: 'database_menu', status: 'adopted', adopted_at: '',
  meal_request_id: 'r1', result_version: version, dishes: [], energy_kcal: null, protein_g: null,
})

afterEach(() => vi.unstubAllGlobals())

describe('餐桌場景', () => {
  it('物件列列出每道菜、每位成員、家庭合計、收據、小知識與各自的狀態', () => {
    expect(dock(render())).toEqual([
      '糙米飯主食', '清蒸鱸魚指定菜', '蒜炒高麗菜', '味噌豆腐湯高鈉',
      '阿明通過', '小惠蛋白質不足', '小安熱量超出', '阿嬤計算不完整',
      '家庭合計通過', '收據價格不完整', '小知識2 則',
    ])
  })

  it('桌上每個可以點的東西都有名字', () => {
    const labels = hotspotLabels(render())
    expect(labels).toContain('主食 糙米飯，看份量')
    expect(labels).toContain('清蒸鱸魚，看做法與食材')
    expect(labels).toContain('阿明：通過，看個人營養')
    expect(labels).toContain('小惠（只帶便當）：蛋白質不足，看個人營養')
    expect(labels).toContain('阿嬤：計算不完整，看個人營養')
    expect(labels).toContain('家庭合計：通過，看全家營養')
    expect(labels).toContain('營養小知識 2 則，打開小卡盤')
    expect(labels.some((label) => label.startsWith('收據'))).toBe(true)
  })

  it('算不出來的人是空碗，不畫飯；其他人是有飯的碗', () => {
    const markup = render()
    expect(images(markup, 'rice-bowl-unknown')).toBe(1)
    expect(images(markup, 'rice-bowl.')).toBe(3)
    expect(images(markup, 'chair-top')).toBe(4)
    expect(images(markup, 'place-setting')).toBe(4)
  })

  it('器皿依菜的種類：飯桶、橢圓盤、圓盤、湯鍋', () => {
    const markup = render()
    for (const art of ['dish-staple', 'dish-seafood', 'dish-vegetable', 'dish-soup']) expect(images(markup, art)).toBe(1)
  })

  it('手機：沒有底部分頁，最底下是動作列', () => {
    const markup = render()
    expect(markup).not.toContain('aria-label="主要導覽"')
    expect(actionBar(markup)).toEqual(['採用這桌', '換一桌', '重新查價'])
  })

  it('桌機：有側欄；收據上兩個金額各自寫未知與已知部分，不相加、不蓋章', () => {
    const markup = render({ width: 1440, height: 900 })
    expect(markup).toContain('aria-label="主要導覽"')
    const out = text(markup)
    expect(out).toContain('要買的整包未知已知 NT$380')
    expect(out).toContain('這餐吃掉未知已知 NT$212')
    expect(out).toContain('鱸魚片未知')
    expect(out).not.toContain('592')
    expect(out).not.toContain('NT$0')
    expect(out).not.toContain('預算內')
    expect(out).toContain('資料庫菜單')
    expect(out).toContain('第 2 版')
  })

  it('AI 生成的菜單：手機的狀態鍵上有 AI 記號（上方放不下模式標籤），資料庫菜單沒有', () => {
    // 上方那顆「這桌的狀況」按鈕裡的字
    const pill = (markup: string) => text(markup.split('<button').find((part) => part.includes('這桌的狀況：'))?.split('</button>')[0] ?? '')
    expect(pill(render({ result: mealResult({ mode: 'ai_generated_menu', mode_label: 'AI 生成菜單' }) }))).toContain('AI（AI 生成菜單）')
    expect(pill(render())).not.toContain('AI')
  })

  it('價格完整而且在預算內才蓋章', () => {
    const result = mealResult()
    result.prices = { ...result.prices, items: [result.prices.items[0]], purchase_total: '45', cost_total: '27.5', budget_status: 'OK', price_complete: true }
    const out = text(render({ result, width: 1440, height: 900 }))
    expect(out).toContain('要買的整包NT$45')
    expect(out).toContain('這餐吃掉NT$28')
    // 印章寫明比的是「這餐吃掉」那一個金額
    expect(out).toContain('這餐吃掉預算內')
    expect(dock(render({ result }))).toContain('收據吃掉的在預算內')
  })

  it('沒有預算的一餐（後端這時的狀態也是 OK）：收據不蓋「預算內」', () => {
    const result = mealResult()
    result.prices = {
      ...result.prices, items: [result.prices.items[0]], purchase_total: '45', cost_total: '27.5', budget: null, budget_status: 'OK', price_complete: true,
    }
    const markup = render({ result, width: 1440, height: 900 })
    expect(text(markup)).toContain('要買的整包NT$45')
    expect(text(markup)).not.toContain('預算內')
    expect(dock(render({ result }))).toContain('收據')
  })

  it('家庭合計立牌三態都有字：通過、未通過、計算不完整', () => {
    // 立牌按鈕裡的字
    const tent = (result: MealResult) => text(render({ result }).split('<button').find((part) => part.includes('aria-label="家庭合計：'))?.split('</button>')[0] ?? '')
    expect(tent(mealResult())).toContain('通過')
    const over = mealResult()
    over.nutrition = { ...over.nutrition, energy_kcal: '3450' }
    expect(tent(over)).toContain('超出或不足')
    const unknown = mealResult()
    unknown.nutrition = { ...unknown.nutrition, energy_kcal: null }
    expect(tent(unknown)).toContain('計算不完整')
    expect(tent(unknown)).not.toContain('0%')
  })

  it('後端說不能採用：沒有採用鍵，改成去決定', () => {
    const result = mealResult({ request_status: 'awaiting_decision' })
    result.eligibility = {
      ...result.eligibility, adoptable: false, blocks: [{ code: 'NUTRITION_INCOMPLETE', message: '缺營養' }],
      pending_decisions: [{ kind: 'continue_missing_nutrition', message: '要先看看嗎？' }],
    }
    const markup = render({ result })
    expect(actionBar(markup)).toEqual(['去決定', '換一桌', '重新查價'])
    expect(text(markup)).toContain('等你決定')
  })

  it('問卷正在重新煮：桌上的結果只能看，按鈕都停用', () => {
    const markup = render({ request: mealRequest({ status: 'running' }) })
    expect(text(markup)).toContain('這份問卷正在重新煮')
    const group = /role="group"[^>]*>(.*?)<\/div>/.exec(markup)?.[1] ?? ''
    expect(count(group, 'disabled=""')).toBe(3)
  })

  it('已採用的快照：沒有動作列，回上一層是紀錄，寫明已採用與餐期', () => {
    const markup = render({ result: mealResult({ request_status: 'confirmed' }), request: mealRequest({ status: 'confirmed' }), plans: [adoptedPlan(2)] })
    expect(actionBar(markup)).toBeNull()
    expect(markup).not.toContain('aria-label="主要導覽"')
    expect(markup).toContain('href="/records?tab=adopted&amp;date=2026-11-14"')
    expect(text(markup)).toContain('已採用・11/14 晚餐')
    expect(text(markup)).not.toContain('已經有比較新的結果')
  })

  it('問卷採用的是資料庫菜單：這一桌不寫已採用，說清楚並連到紀錄', () => {
    const markup = render({ result: mealResult({ request_status: 'confirmed' }), request: mealRequest({ status: 'confirmed' }), plans: [adoptedPlan(null)] })
    const out = text(markup)
    expect(out).toContain('這桌的狀況：採用的是資料庫菜單')
    expect(out).toContain('這份問卷採用的是資料庫菜單，不是這一桌；這一桌沒有被採用，只能看。')
    expect(out).not.toContain('已採用・11/14 晚餐')
    expect(out).not.toContain('已採用這一桌')
    expect(actionBar(markup)).toBeNull()
  })

  it('已採用但還沒讀到已採用紀錄：不猜這一桌是不是被採用的那一版', () => {
    const markup = render({ result: mealResult({ request_status: 'confirmed' }), request: mealRequest({ status: 'confirmed' }) })
    const out = text(markup)
    expect(out).toContain('這桌的狀況：問卷已採用')
    expect(out).not.toContain('已採用・11/14 晚餐')
    expect(actionBar(markup)).toBeNull()
  })

  it('已採用，但看的不是被採用的那一版：說清楚並連到被採用的版本', () => {
    const markup = render({
      result: mealResult({ request_status: 'confirmed' }), request: mealRequest({ status: 'confirmed' }), versions: [2, 1], plans: [adoptedPlan(1)],
    })
    expect(text(markup)).toContain('這份問卷採用的是第 1 版；這是第 2 版，沒有被採用，只能看。')
    expect(markup).toContain('href="/plan/r1/result/1"')
    expect(actionBar(markup)).toBeNull()
  })

  it('不是最新的版本：只能看，不能採用、換一桌或重試，動作列只剩到最新版的路', () => {
    const result = mealResult({ knowledge: { status: 'unavailable', retry_available: true, items: [] } })
    const markup = render({ result, versions: [3, 2] })
    expect(text(markup)).toContain('這是第 2 版，已經有比較新的結果了。舊的版本只能看。')
    expect(text(markup)).toContain('這桌的狀況：舊版，只能看')
    expect(markup).toContain('href="/plan/r1/result/3"')
    expect(actionBar(markup)).toEqual(['看最新的第 3 版'])
  })

  it('手機：提示一次只露出最要緊的一則，其餘收在「還有 N 則」後面；桌機全部列出', () => {
    const failed = { job_id: 'j9', status: 'failed' as const, stage: 'failed', attempts: 1, error: { code: 'X', message: '定向查價失敗' }, produced_result_version: null, created_at: '' }
    const setup = { result: mealResult({ retries: { price: failed, knowledge: null } }), request: mealRequest({ status: 'running' }) }
    const phone = text(render(setup))
    expect(phone).toContain('上次重新查價沒有完成：定向查價失敗')
    expect(phone).not.toContain('這份問卷正在重新煮')
    expect(phone).toContain('還有 1 則提示')
    const wide = text(render({ ...setup, width: 1440, height: 900 }))
    expect(wide).toContain('上次重新查價沒有完成：定向查價失敗')
    expect(wide).toContain('這份問卷正在重新煮')
    expect(wide).not.toContain('則提示')
  })

  it('重試失敗的原因寫在畫面上，不只在面板裡；已採用的快照不寫', () => {
    const failed = { job_id: 'j9', status: 'failed' as const, stage: 'failed', attempts: 1, error: { code: 'PRICE_LOOKUP_FAILED', message: '定向查價失敗' }, produced_result_version: null, created_at: '' }
    const out = text(render({ result: mealResult({ retries: { price: failed, knowledge: null } }) }))
    expect(out).toContain('上次重新查價沒有完成：定向查價失敗。原結果不變，可以直接採用或稍後再試。')
    expect(actionBar(render({ result: mealResult({ retries: { price: failed, knowledge: null } }) }))).toContain('重新查價')
    const adopted = text(render({ result: mealResult({ request_status: 'confirmed', retries: { price: failed, knowledge: null } }) }))
    expect(adopted).not.toContain('上次重新查價沒有完成')
  })

  it('有小知識時上方另有入口（桌緣的小卡盤不起眼）；拿不到時沒有', () => {
    const chip = (markup: string) => /<button[^>]*aria-expanded="false"[^>]*>營養小知識 \d+ 則/.test(markup)
    expect(chip(render({ width: 1440, height: 900 }))).toBe(true)
    expect(chip(render({ width: 1440, height: 900, result: mealResult({ knowledge: { status: 'unavailable', retry_available: true, items: [] } }) }))).toBe(false)
  })

  it('小知識拿不到時是空卡盤，動作列多一顆重試', () => {
    const markup = render({ result: mealResult({ knowledge: { status: 'unavailable', retry_available: true, items: [] } }) })
    expect(images(markup, 'tip-tray-empty')).toBe(1)
    expect(dock(markup)).toContain('小知識暫時拿不到')
    expect(actionBar(markup)).toEqual(['採用這桌', '換一桌', '重新查價', '重試小知識'])
  })

  it('這份問卷還沒有結果：空桌加一句引導', () => {
    const markup = render({ result: null, versions: [] })
    expect(text(markup)).toContain('這份問卷還沒有煮出結果')
    expect(markup).toContain('href="/plan/r1"')
    expect(dock(markup)).toEqual([])
    expect(actionBar(markup)).toBeNull()
  })

  it('網址上的版本不是數字：帶到最新的那一版', () => {
    // MemoryRouter 在伺服器端渲染時不會真的換頁；只要不把亂打的版本拿去問後端、也不畫桌子就好
    const markup = render({ path: '/plan/r1/result/latest', versions: [2] })
    expect(dock(markup)).toEqual([])
  })

  it('舊結果沒有每人資料：桌邊不擺座位，其餘照常', () => {
    const result = mealResult()
    result.nutrition = { ...result.nutrition, per_person: [] }
    const markup = render({ result })
    expect(images(markup, 'chair-top')).toBe(0)
    expect(dock(markup)).toEqual(['糙米飯主食', '清蒸鱸魚指定菜', '蒜炒高麗菜', '味噌豆腐湯高鈉', '家庭合計通過', '收據價格不完整', '小知識2 則'])
  })
})
