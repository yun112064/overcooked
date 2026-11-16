import type { ReactElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { MemoryRouter } from 'react-router'
import { describe, expect, it, vi } from 'vitest'
import type { MealRequestSummary } from '../../api/types'
import { CalendarNote, DayNote, MealRows, RequestsNote, TrendNote } from './DoorNotes'
import type { AdoptedPlan, Trend } from './records'
import type { MarketCheckout } from './useMarketCheckout'

// 便利貼只跟房間要「哪個物件正在閃」；測試裡不需要整個房間
vi.mock('../../camera/Room', () => ({ useRoom: () => ({ flashId: null }) }))

const html = (node: ReactElement) => renderToStaticMarkup(<MemoryRouter>{node}</MemoryRouter>)
/** 只看畫出來的文字（去掉標籤），確認使用者讀到什麼。 */
const plain = (markup: string) => markup.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ')
const text = (node: ReactElement) => plain(html(node))

const noop = () => undefined
const loaded = <T,>(data: T) => ({ data, isPending: false, isError: false, isFetching: false, error: null, refetch: noop })
const pending = { data: undefined, isPending: true, isError: false, isFetching: true, error: null, refetch: noop }
const failed = { data: undefined, isPending: false, isError: true, isFetching: false, error: new Error('x'), refetch: noop }
const idle: MarketCheckout = { open: noop, pendingId: null, errorId: null, errorText: null }

describe('月曆', () => {
  const calendar = (dates: typeof failed | ReturnType<typeof loaded>) => (
    <CalendarNote
      month="2026-11" selected="2026-11-14" today="2026-11-15" counts={new Map([['2026-11-14', 2], ['2026-11-03', 5]])}
      dates={dates} incomplete={false} onMonth={noop} onPick={noop}
    />
  )
  const out = html(calendar(loaded([])))

  it('每一天是一顆按鈕，名稱裡寫出日期、是不是今天、有幾餐', () => {
    expect(out).toContain('aria-label="11 月 14 日（週六），2 餐已採用"')
    expect(out).toContain('aria-label="11 月 15 日（週日），今天，沒有標記"')
    expect((out.match(/aria-pressed="true"/g) ?? []).length).toBe(1)
  })

  it('貼紙一餐一張，最多畫三張', () => {
    const stickers = (day: number) => out.split(`aria-label="11 月 ${day} 日`)[1].split('</button>')[0].match(/<i>/g)?.length ?? 0
    expect(stickers(14)).toBe(2)
    expect(stickers(3)).toBe(3)
    expect(stickers(15)).toBe(0)
  })

  it('可以翻月份、回今天、跳到指定日期；說明這裡只記錄「採用」', () => {
    expect(out).toContain('aria-label="上個月"')
    expect(out).toContain('aria-label="下個月"')
    expect(out).toContain('aria-label="跳到指定日期"')
    expect(out).toContain('不代表實際吃了')
  })

  it('日期清單讀不到：寫出錯誤並可以重試', () => {
    const broken = text(calendar(failed))
    expect(broken).toContain('發生錯誤，請稍後再試。')
    expect(broken).toContain('再試一次')
  })
})

describe('當日餐單', () => {
  const generation = {
    id: 'p1', meal_date: '2026-11-14', meal_type: 'dinner', mode: 'database_menu', status: 'adopted', adopted_at: '2026-11-14T10:00:00Z',
    meal_request_id: 'r1', result_version: 2, dishes: [{ dish_name: '清蒸鱸魚' }, { dish_name: '蒜炒花椰菜' }],
    nutrition: { energy_kcal: null }, prices: { items: [{ food_id: 'f1' }] },
  } as unknown as AdoptedPlan
  const menu = {
    id: 'p2', meal_date: '2026-11-14', meal_type: 'lunch', mode: 'database_menu', status: 'adopted', adopted_at: '2026-11-14T03:00:00Z',
    meal_request_id: 'r2', result_version: null, market_shortages_available: false, energy_kcal: '1820.4',
    dishes: [{ dish_name: '番茄炒蛋', recipe_id: 'x' }], snapshot: { staple_choice: '白飯' },
  } as unknown as AdoptedPlan
  const props = { date: '2026-11-14', latestDate: '2026-11-14', neverAdopted: false, checkout: idle, onOpen: noop, onPick: noop, onStep: noop }

  it('列出每一餐的菜；熱量未知寫未知，不寫 0', () => {
    const out = text(<DayNote {...props} plans={loaded([generation, menu])} />)
    expect(out).toContain('11/14（週六）採用的餐單')
    expect(out).toContain('清蒸鱸魚、蒜炒花椰菜')
    expect(out).toMatch(/整餐熱量\s*未知/)
    expect(out).toContain('白飯、番茄炒蛋')
    expect(out).toContain('1,820 kcal')
    expect(out).not.toMatch(/\s0 kcal/)
  })

  it('生成結果的紀錄可以回餐桌看那一版；有缺料清單才有一鍵購入', () => {
    const out = html(<DayNote {...props} plans={loaded([generation, menu])} />)
    expect(out).toContain('href="/plan/r1/result/2"')
    expect(out).toContain('aria-label="晚餐：一鍵購入缺的食材"')
    expect(out).not.toContain('aria-label="午餐：一鍵購入缺的食材"')
    expect((out.match(/看這桌/g) ?? []).length).toBe(1)
  })

  it('生成結果的紀錄也列出主食（整餐熱量有算它）', () => {
    const withStaple = { ...generation, staple: { label: '白飯' } } as AdoptedPlan
    expect(text(<DayNote {...props} plans={loaded([withStaple])} />)).toContain('白飯、清蒸鱸魚、蒜炒花椰菜')
  })

  it('同一份清單也能放在面板裡（物件列的「當日餐單」）：每一餐都能開明細、購入', () => {
    const out = html(<MealRows plans={[generation, menu]} checkout={idle} onOpen={noop} />)
    expect((out.match(/看明細/g) ?? []).length).toBe(2)
    expect(out).toContain('aria-label="晚餐：一鍵購入缺的食材"')
    expect(plain(out)).toContain('白飯、番茄炒蛋')
  })

  it('可以一天一天翻', () => {
    const out = html(<DayNote {...props} plans={loaded([])} />)
    expect(out).toContain('aria-label="前一天"')
    expect(out).toContain('aria-label="後一天"')
  })

  it('開啟超市失敗時，在那一餐旁邊寫出原因', () => {
    const out = text(
      <DayNote {...props} plans={loaded([generation])} checkout={{ ...idle, errorId: 'p1', errorText: '暫時無法開啟模擬超市，請重試。' }} />,
    )
    expect(out).toContain('暫時無法開啟模擬超市，請重試。')
  })

  it('那天沒有紀錄：帶去最近一筆；從來沒採用過：帶去廚房', () => {
    const empty = text(<DayNote {...props} date="2026-11-10" plans={loaded([])} />)
    expect(empty).toContain('11 月 10 日（週二）沒有已採用的餐食紀錄')
    expect(empty).toContain('看最近一筆（11/14）')
    const never = html(<DayNote {...props} latestDate={null} neverAdopted plans={loaded([])} />)
    expect(never).toContain('還沒有採用過菜單')
    expect(never).toContain('href="/plan"')
  })

  it('讀取中與讀取失敗都有文字', () => {
    expect(text(<DayNote {...props} plans={pending} />)).toContain('正在翻那天的紀錄')
    expect(text(<DayNote {...props} plans={failed} />)).toContain('再試一次')
  })
})

describe('趨勢', () => {
  const trend: Trend = {
    since: '2026-11-08', until: '2026-11-14',
    items: [
      {
        meal_date: '2026-11-08', meal_type: 'dinner', energy_kcal: '2000', protein_g: '80', target_energy_kcal: '2000', target_protein_g: '80',
        cost_total: '300', known_cost_total: '300', budget: '400',
      },
      {
        meal_date: '2026-11-10', meal_type: 'dinner', energy_kcal: null, protein_g: null, target_energy_kcal: '2000', target_protein_g: '80',
        cost_total: null, known_cost_total: '120', budget: '400',
      },
      // 資料庫菜單的紀錄：數字型別，沒有成本欄位
      { meal_date: '2026-11-12', meal_type: 'lunch', energy_kcal: 1500, protein_g: 60, target_energy_kcal: 2000, target_protein_g: 80 },
    ],
  }
  const props = { days: 7 as const, selected: '2026-11-08', onDays: noop, onKind: noop, onPick: noop }

  it('成本：未知的那天不畫長條、缺價的那天標「僅已知部分」，統計不把未知算成 0', () => {
    const out = html(<TrendNote {...props} kind="cost" trend={loaded(trend)} />)
    expect(out).toContain('11/8・1 餐，按比例成本 300 元，預算 400 元')
    expect(out).toContain('11/10・1 餐，按比例成本 120 元（僅已知部分），預算 400 元')
    expect(out).toContain('11/12・1 餐，按比例成本 未知')
    // 有紀錄的三天各一顆按鈕；其中只有兩天畫得出有高度的長條
    expect((out.match(/看當天的餐單"/g) ?? []).length).toBe(3)
    expect((out.match(/style="height:/g) ?? []).length).toBe(2)
    const words = plain(out)
    expect(words).toMatch(/近 7 天採用\s*3\s*餐/)
    expect(words).toMatch(/平均每餐\s*NT\$300/)
    expect(words).toMatch(/預算內\s*1\s*\/\s*1\s*餐/)
    expect(words).toContain('2 餐價格未知')
    expect(words).toContain('和整包花費是兩個數字')
    expect(words).not.toContain('NT$0')
  })

  it('熱量：三態判定；規則寫在旁邊', () => {
    const out = html(<TrendNote {...props} kind="energy" trend={loaded(trend)} />)
    expect(out).toContain('11/10・1 餐，熱量 未知，目標 2,000 kcal')
    const words = plain(out)
    expect(words).toContain('通過')
    expect(words).toContain('熱量不超過目標 110%、蛋白質達 90% 算通過')
  })

  it('只計已知部分的長條一定有文字說明：圖例多一格、說明多一句（成本與熱量都是）', () => {
    // 11/10 缺價只知道 120 元 → 成本圖有部分長條；那天熱量整天未知 → 熱量圖沒有
    const cost = plain(html(<TrendNote {...props} kind="cost" trend={loaded(trend)} />))
    expect(cost).toContain('部分已知')
    expect(cost).toContain('虛線框的長條只計已知的部分，不是整天的數字')
    const energy = plain(html(<TrendNote {...props} kind="energy" trend={loaded(trend)} />))
    expect(energy).not.toContain('部分已知')
    expect(energy).not.toContain('虛線框')

    // 同一天一餐熱量已知、一餐未知；成本一餐完整、一餐是沒有成本欄位的資料庫菜單
    const mixed: Trend = {
      ...trend,
      items: [
        { ...trend.items[0], meal_date: '2026-11-09' },
        { meal_date: '2026-11-09', meal_type: 'lunch', energy_kcal: null, protein_g: null, target_energy_kcal: 2000, target_protein_g: 80 },
      ],
    }
    for (const kind of ['cost', 'energy'] as const) {
      const out = html(<TrendNote {...props} kind={kind} trend={loaded(mixed)} />)
      expect(out).toContain('（僅已知部分）')
      expect(plain(out)).toContain('部分已知')
      expect(plain(out)).toContain('虛線框的長條只計已知的部分，不是整天的數字')
    }
  })

  it('30／90 天的長條太細，不做成按鈕；改用選單選日期，選項裡寫著每一天的數字', () => {
    const month: Trend = { ...trend, since: '2026-10-16' }
    const out = html(<TrendNote {...props} days={30} kind="cost" trend={loaded(month)} />)
    expect(out).not.toContain('看當天的餐單"')
    expect(plain(out)).not.toContain('點長條看當天的餐單')
    expect(plain(out)).toContain('看某一天的餐單')
    // 有紀錄的三天各一個選項（另外一個是「選一天…」），最近的排前面
    expect((out.match(/<option/g) ?? []).length).toBe(4)
    expect(out.indexOf('>11/12・1 餐，按比例成本 未知<')).toBeGreaterThan(-1)
    expect(out.indexOf('>11/12・')).toBeLessThan(out.indexOf('>11/8・1 餐，按比例成本 300 元，預算 400 元<'))
    // 目前選的那一天仍然標在圖上，也是選單的值
    expect((out.match(/data-current="true"/g) ?? []).length).toBe(1)
    expect(out).toMatch(/<option value="2026-11-08" selected="">/)

    // 看一週時維持原樣：長條可以點，沒有選單
    const week = html(<TrendNote {...props} kind="cost" trend={loaded(trend)} />)
    expect(week).not.toContain('<select')
    expect(plain(week)).toContain('點長條看當天的餐單')
  })

  it('沒有可比較的紀錄時寫「計算不完整」，不寫 0%', () => {
    const unknown: Trend = { ...trend, items: [trend.items[1]] }
    const out = text(<TrendNote {...props} kind="energy" trend={loaded(unknown)} />)
    expect(out).toContain('計算不完整')
    expect(out).toContain('沒有可比較的紀錄')
    expect(out).not.toMatch(/\s0%/)
  })

  it('這段期間沒有紀錄、讀取失敗', () => {
    expect(text(<TrendNote {...props} kind="cost" trend={loaded({ ...trend, items: [] })} />)).toContain('近 7 天沒有已採用的餐食')
    expect(text(<TrendNote {...props} kind="cost" trend={failed} />)).toContain('再試一次')
  })
})

describe('生成紀錄', () => {
  const request = (over: Partial<MealRequestSummary>) => ({
    id: 'r1', status: 'draft', version: 1, meal_date: '2026-11-15', meal_type: 'lunch', mode: 'questionnaire', ...over,
  }) as MealRequestSummary
  const rows = [
    request({ id: 'a', status: 'awaiting_decision', version: 2 }),
    request({ id: 'b', status: 'confirmed', meal_date: '2026-11-14', meal_type: 'dinner' }),
    request({ id: 'c', status: 'failed', mode: 'picker' }),
    request({ id: 'd', status: 'draft' }),
  ]

  it('每一列有日期餐別、版本與狀態文字，連回那張單', () => {
    const out = html(<RequestsNote requests={loaded(rows)} limit={3} onMore={noop} />)
    const words = plain(out)
    expect(words).toContain('11/15（週日）午餐')
    expect(words).toContain('草稿第 2 版')
    expect(words).toContain('待決定')
    expect(words).toContain('已採用')
    expect(words).toContain('失敗')
    expect(out).toContain('href="/plan/a"')
    expect(out).toContain('href="/pick/c"')
  })

  it('便利貼只列前幾筆，其餘按「全部」', () => {
    const out = html(<RequestsNote requests={loaded(rows)} limit={3} onMore={noop} />)
    expect(out).not.toContain('href="/plan/d"')
    expect(plain(out)).toMatch(/全部\s*4\s*筆/)
  })

  it('沒有任何一張單時帶去廚房', () => {
    const out = html(<RequestsNote requests={loaded([])} limit={3} onMore={noop} />)
    expect(out).toContain('還沒有任何一張單')
    expect(out).toContain('href="/plan"')
  })
})
