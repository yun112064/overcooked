import { describe, expect, it } from 'vitest'
import type { MealRequestSummary } from '../../api/types'
import type { PotState } from '../../pot/potState'
import {
  anyCooking, dateParts, destinationFor, greeting, latestAdoptedDate, planLine, potAction, railTickets, resultPath,
  ticketText, todayTitle, trendItems, weekStats,
} from './kitchenLogic'

const request = (over: Partial<MealRequestSummary> = {}): MealRequestSummary => ({
  id: 'r1', status: 'draft', version: 3, meal_date: '2026-10-24', meal_type: 'dinner', mode: 'questionnaire',
  updated_at: '2026-10-24T10:00:00Z', ...over,
})
const pot = (kind: PotState['kind']): PotState => (kind === 'filling' ? { kind, steps: 2 } : { kind })

describe('dateParts', () => {
  it('拆成月/日與星期（以本機日期計算，不受時區影響）', () => {
    expect(dateParts('2026-10-24')).toEqual({ md: '10/24', weekday: '週六' })
    expect(dateParts('2026-11-01')).toEqual({ md: '11/1', weekday: '週日' })
  })

  it('沒有日期或看不懂就是 null', () => {
    expect(dateParts(null)).toBeNull()
    expect(dateParts('')).toBeNull()
    expect(dateParts('明天')).toBeNull()
  })
})

describe('問候與今天的日期', () => {
  it('11 點前早安、17 點前午安、之後晚安', () => {
    expect(greeting(new Date(2026, 10, 14, 10, 59))).toBe('早安')
    expect(greeting(new Date(2026, 10, 14, 11, 0))).toBe('午安')
    expect(greeting(new Date(2026, 10, 14, 16, 59))).toBe('午安')
    expect(greeting(new Date(2026, 10, 14, 17, 0))).toBe('晚安')
  })

  it('日期標題', () => {
    expect(todayTitle(new Date(2026, 10, 14))).toBe('11 月 14 日・星期六')
  })
})

describe('ticketText', () => {
  it('問卷：日期、餐別、草稿版本', () => {
    expect(ticketText(request())).toEqual({ date: '10/24', weekday: '週六', meal: '晚餐', note: '草稿第 3 版' })
  })

  it('還沒選的欄位寫出來，不留空', () => {
    expect(ticketText(request({ meal_date: null, meal_type: null })))
      .toMatchObject({ date: '—', weekday: '未選日期', meal: '未選餐別' })
  })

  it('食譜庫選菜的單', () => {
    expect(ticketText(request({ mode: 'picker' })).note).toBe('食譜庫選菜')
  })
})

describe('railTickets', () => {
  it('最新的三張掛上單軌，其餘算掛不下', () => {
    expect(railTickets(['a', 'b', 'c', 'd', 'e'])).toEqual({ shown: ['a', 'b', 'c'], hidden: 2 })
    expect(railTickets(['a'])).toEqual({ shown: ['a'], hidden: 0 })
    expect(railTickets([])).toEqual({ shown: [], hidden: 0 })
  })
})

describe('destinationFor', () => {
  it('食譜庫選菜一律回食譜書（不論狀態）', () => {
    for (const status of ['draft', 'confirmed', 'result_ready']) {
      expect(destinationFor(request({ mode: 'picker', status }), pot('ready'))).toEqual({ kind: 'path', to: '/pick/r1' })
    }
  })

  it('草稿回問卷', () => {
    for (const kind of ['empty', 'filling', 'full'] as const) {
      expect(destinationFor(request(), pot(kind))).toEqual({ kind: 'path', to: '/plan/r1' })
    }
  })

  it('生成中與失敗到進度畫面', () => {
    expect(destinationFor(request({ status: 'running' }), pot('cooking'))).toEqual({ kind: 'path', to: '/plan/r1/cooking' })
    expect(destinationFor(request({ status: 'failed' }), pot('failed'))).toEqual({ kind: 'path', to: '/plan/r1/cooking' })
  })

  it('有結果、已採用要先查結果版本再上桌', () => {
    expect(destinationFor(request({ status: 'result_ready' }), pot('ready'))).toEqual({ kind: 'result', requestId: 'r1' })
    expect(destinationFor(request({ status: 'awaiting_decision' }), pot('ready'))).toEqual({ kind: 'result', requestId: 'r1' })
    // 已採用的鍋是空鍋，但單子要帶去看唯讀的結果
    expect(destinationFor(request({ status: 'confirmed' }), pot('empty'))).toEqual({ kind: 'result', requestId: 'r1' })
  })

  it('工作比問卷新：清單還寫草稿，但工作已經在跑，就去看進度', () => {
    expect(destinationFor(request({ status: 'draft' }), pot('cooking'))).toEqual({ kind: 'path', to: '/plan/r1/cooking' })
  })
})

describe('resultPath', () => {
  it('用最新（第一筆）的結果版本', () => {
    expect(resultPath('r1', [{ result_version: 4 }, { result_version: 3 }])).toBe('/plan/r1/result/4')
  })

  it('其實沒有結果就回問卷', () => {
    expect(resultPath('r1', [])).toBe('/plan/r1')
  })
})

describe('anyCooking', () => {
  it('問卷或工作任何一邊在排隊／生成中', () => {
    expect(anyCooking([{ status: 'draft' }, { status: 'queued' }], [])).toBe(true)
    expect(anyCooking([{ status: 'draft' }], [{ status: 'running' }])).toBe(true)
  })

  it('都結束了、或還沒讀到，就不輪詢', () => {
    expect(anyCooking([{ status: 'result_ready' }], [{ status: 'succeeded' }, { status: 'failed' }])).toBe(false)
    expect(anyCooking(undefined, undefined)).toBe(false)
  })
})

describe('potAction', () => {
  it('空鍋：沒有問卷是開新的一餐，有問卷是接著填', () => {
    expect(potAction(pot('empty'), false)).toBe('新的一餐')
    expect(potAction(pot('empty'), true)).toBe('接著填')
  })

  it('其餘狀態', () => {
    expect(potAction(pot('filling'), true)).toBe('接著填')
    expect(potAction(pot('full'), true)).toBe('去送出')
    expect(potAction(pot('cooking'), true)).toBe('看進度')
    expect(potAction(pot('ready'), true)).toBe('上桌看結果')
    expect(potAction(pot('failed'), true)).toBe('看原因')
  })
})

describe('trendItems', () => {
  const items = [{ meal_date: '2026-11-13', meal_type: 'dinner' }]

  it('完整回應與只有 items 的快取都收', () => {
    expect(trendItems({ items })).toBe(items)
    expect(trendItems(items)).toBe(items)
  })

  it('沒有資料是空的', () => {
    expect(trendItems(undefined)).toEqual([])
    expect(trendItems({})).toEqual([])
  })
})

describe('weekStats', () => {
  const item = (over: object) => ({ meal_date: '2026-11-13', meal_type: 'dinner', ...over })

  it('熱量達成是各餐比例的平均，成本是價格完整的餐的平均', () => {
    const stats = weekStats([
      item({ energy_kcal: '900', target_energy_kcal: '1000', cost_total: '200' }),
      item({ energy_kcal: '1100', target_energy_kcal: '1000', cost_total: '100.5' }),
    ])
    expect(stats.meals).toBe(2)
    expect(stats.energyReach).toBeCloseTo(1)
    expect(stats.averageCost).toBeCloseTo(150.25)
    expect(stats.unknownCostMeals).toBe(0)
  })

  it('字串（含分數）與數字混在一起都算得出來', () => {
    const stats = weekStats([
      item({ energy_kcal: 500, target_energy_kcal: 1000, cost_total: 80 }),
      item({ energy_kcal: '3000/2', target_energy_kcal: '1000', cost_total: '120' }),
    ])
    expect(stats.energyReach).toBeCloseTo(1)
    expect(stats.averageCost).toBeCloseTo(100)
  })

  it('未知的餐不當成 0：不列入平均，另外記下幾餐價格未知', () => {
    const stats = weekStats([
      item({ energy_kcal: null, target_energy_kcal: '1000', cost_total: null }),
      // 資料庫菜單的舊紀錄沒有成本欄位
      item({ energy_kcal: 800, target_energy_kcal: 1000 }),
      item({ energy_kcal: '1200', target_energy_kcal: '1000', cost_total: '90' }),
    ])
    expect(stats.meals).toBe(3)
    expect(stats.energyReach).toBeCloseTo(1)
    expect(stats.averageCost).toBe(90)
    expect(stats.unknownCostMeals).toBe(2)
  })

  it('全部未知就是 null（未知），不是 0', () => {
    const stats = weekStats([item({ energy_kcal: null, target_energy_kcal: null, cost_total: null })])
    expect(stats).toEqual({ meals: 1, energyReach: null, averageCost: null, unknownCostMeals: 1 })
  })

  it('沒有目標或目標是 0 的餐不能算比例', () => {
    expect(weekStats([item({ energy_kcal: '500', target_energy_kcal: '0' })]).energyReach).toBeNull()
    expect(weekStats([item({ energy_kcal: '500' })]).energyReach).toBeNull()
  })

  it('沒有紀錄', () => {
    expect(weekStats([])).toEqual({ meals: 0, energyReach: null, averageCost: null, unknownCostMeals: 0 })
  })

  it('成本 0 元是已知的 0，照算', () => {
    expect(weekStats([item({ cost_total: '0' })]).averageCost).toBe(0)
  })
})

describe('latestAdoptedDate', () => {
  const dates = (...days: string[]) => days.map((meal_date) => ({ meal_date }))

  it('到今天為止最近的一天', () => {
    expect(latestAdoptedDate(dates('2026-11-16', '2026-11-14', '2026-11-12'), '2026-11-14')).toBe('2026-11-14')
    expect(latestAdoptedDate(dates('2026-11-16', '2026-11-12', '2026-11-10'), '2026-11-14')).toBe('2026-11-12')
  })

  it('只有預先排好的未來餐：最接近今天的那一天', () => {
    expect(latestAdoptedDate(dates('2026-11-20', '2026-11-16'), '2026-11-14')).toBe('2026-11-16')
  })

  it('沒有紀錄', () => {
    expect(latestAdoptedDate([], '2026-11-14')).toBeNull()
  })
})

describe('planLine', () => {
  it('餐別與菜名', () => {
    expect(planLine({ meal_type: 'dinner', dishes: [{ dish_name: '五穀飯' }, { dish_name: '清蒸鱸魚' }] }))
      .toEqual({ meal: '晚餐', dishes: '五穀飯、清蒸鱸魚' })
  })

  it('不認得的餐別照後端給的字顯示', () => {
    expect(planLine({ meal_type: 'snack', dishes: [] })).toEqual({ meal: 'snack', dishes: '' })
  })
})
