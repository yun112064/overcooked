import { describe, expect, it } from 'vitest'
import {
  barHeight, barScale, dateLabel, dayBars, dayText, defaultMeal, dishLine, dishNames, nextMeal, parseMeal,
  planForMeal, planRatios, shiftDate, todayText, trendItems, type TodayPlan, type TrendItem,
} from './today'

const TODAY = '2026-11-14' // 星期六

function item(date: string, meal: string, energy: TrendItem['energy_kcal'], target: TrendItem['target_energy_kcal']): TrendItem {
  return { meal_date: date, meal_type: meal, energy_kcal: energy, protein_g: null, target_energy_kcal: target, target_protein_g: null }
}

/** 今天晚餐只有這一筆時，今天那一根長條 */
function todayBar(energy: TrendItem['energy_kcal'], target: TrendItem['target_energy_kcal']) {
  return dayBars([item(TODAY, 'dinner', energy, target)], 'dinner', TODAY)[6]
}

describe('餐別', () => {
  it('只認得三種餐別，其他當作沒寫', () => {
    expect(parseMeal('lunch')).toBe('lunch')
    expect(parseMeal('brunch')).toBeNull()
    expect(parseMeal('')).toBeNull()
    expect(parseMeal(null)).toBeNull()
  })

  it('沒指定時依時間猜', () => {
    expect(defaultMeal(7)).toBe('breakfast')
    expect(defaultMeal(10)).toBe('lunch')
    expect(defaultMeal(14)).toBe('lunch')
    expect(defaultMeal(15)).toBe('dinner')
    expect(defaultMeal(23)).toBe('dinner')
  })

  it('換台：早 → 午 → 晚 → 早', () => {
    expect(nextMeal('breakfast')).toBe('lunch')
    expect(nextMeal('lunch')).toBe('dinner')
    expect(nextMeal('dinner')).toBe('breakfast')
  })
})

describe('日期', () => {
  it('跨月、跨年都照本機日曆算', () => {
    expect(shiftDate('2026-11-01', -1)).toBe('2026-10-31')
    expect(shiftDate('2026-01-01', -1)).toBe('2025-12-31')
    expect(shiftDate('2026-11-14', 0)).toBe('2026-11-14')
  })

  it('日期標籤帶星期', () => {
    expect(dateLabel(TODAY)).toBe('11/14（六）')
  })
})

describe('trendItems', () => {
  it('整包或只有 items 都讀得到；讀不懂就是空的', () => {
    const items = [item(TODAY, 'dinner', '600', '650')]
    expect(trendItems({ since: '2026-11-08', until: TODAY, items })).toEqual(items)
    expect(trendItems(items)).toEqual(items)
    expect(trendItems(undefined)).toEqual([])
    expect(trendItems({})).toEqual([])
  })
})

describe('dayBars', () => {
  it('連續七天，舊到新，最後一格是今天', () => {
    const bars = dayBars([], 'dinner', TODAY)
    expect(bars.map((b) => b.date)).toEqual([
      '2026-11-08', '2026-11-09', '2026-11-10', '2026-11-11', '2026-11-12', '2026-11-13', '2026-11-14',
    ])
    expect(bars.map((b) => b.label)).toEqual(['日', '一', '二', '三', '四', '五', '今天'])
    expect(bars.map((b) => b.today)).toEqual([false, false, false, false, false, false, true])
    expect(bars.every((b) => b.state === 'none' && b.ratio === null && b.energy === null)).toBe(true)
  })

  it('只算選到的餐別', () => {
    const bars = dayBars([item(TODAY, 'lunch', '500', '500'), item(TODAY, 'dinner', '650', '650')], 'lunch', TODAY)
    expect(bars[6]).toMatchObject({ meals: 1, energy: 500, ratio: 1, state: 'pass' })
  })

  it('字串（含分數）和數字都收', () => {
    const bars = dayBars([item(TODAY, 'dinner', '1300/2', 650), item('2026-11-13', 'dinner', 325, '650')], 'dinner', TODAY)
    expect(bars[6]).toMatchObject({ energy: 650, target: 650, ratio: 1, state: 'pass' })
    expect(bars[5]).toMatchObject({ ratio: 0.5, state: 'pass' })
  })

  it('超過目標 110% 才算超出；剛好 110% 通過；低於不限', () => {
    expect(todayBar('716', '650').state).toBe('over')
    expect(todayBar('715', '650').state).toBe('pass')
    expect(todayBar('100', '650').state).toBe('pass')
  })

  it('熱量未知是 unknown，不是 0，也不是沒紀錄', () => {
    expect(todayBar(null, '650')).toMatchObject({ meals: 1, energy: null, ratio: null, state: 'unknown' })
  })

  it('沒有目標可比也是 unknown，但保留已知的熱量', () => {
    expect(todayBar(620, null)).toMatchObject({ energy: 620, target: null, ratio: null, state: 'unknown' })
  })

  it('同一餐別有兩筆時合計；其中一筆未知就整天未知，不拿已知的部分充數', () => {
    const both = dayBars([item(TODAY, 'dinner', '300', '325'), item(TODAY, 'dinner', '350', '325')], 'dinner', TODAY)[6]
    expect(both).toMatchObject({ meals: 2, energy: 650, target: 650, ratio: 1 })
    const partial = dayBars([item(TODAY, 'dinner', '300', '325'), item(TODAY, 'dinner', null, '325')], 'dinner', TODAY)[6]
    expect(partial).toMatchObject({ meals: 2, energy: null, ratio: null, state: 'unknown' })
  })

  it('範圍外的日子不算', () => {
    const bars = dayBars([item('2026-11-07', 'dinner', '600', '650'), item('2026-11-15', 'dinner', '600', '650')], 'dinner', TODAY)
    expect(bars.every((b) => b.meals === 0)).toBe(true)
  })
})

describe('長條的高度', () => {
  it('圖表上緣至少 125%，有更高的跟著拉高', () => {
    expect(barScale(dayBars([], 'dinner', TODAY))).toBe(1.25)
    expect(barScale([todayBar('1300', '650')])).toBe(2)
  })

  it('高度是達成率佔圖表的比例，夾在 0–100', () => {
    expect(barHeight(1, 1.25)).toBe(80)
    expect(barHeight(1.25, 1.25)).toBe(100)
    expect(barHeight(3, 1.25)).toBe(100)
    expect(barHeight(0, 1.25)).toBe(0)
  })
})

describe('文字說明', () => {
  it('每種情況各有自己的說法，未知不會寫成 0', () => {
    expect(dayText(dayBars([], 'dinner', TODAY)[6])).toBe('11/14（六）：沒有採用紀錄')
    expect(dayText(todayBar(null, '650'))).toBe('11/14（六）：熱量未知，計算不完整')
    expect(dayText(todayBar(null, '650'))).not.toContain('0 kcal')
    expect(dayText(todayBar('620', null))).toBe('11/14（六）：熱量 620 kcal，沒有目標可以比較')
    expect(dayText(todayBar('624', '650'))).toBe('11/14（六）：熱量 624 kcal，目標 650 kcal，達成 96%，通過')
    expect(dayText(todayBar('780', '650'))).toBe('11/14（六）：熱量 780 kcal，目標 650 kcal，達成 120%，超出')
  })

  it('電視右上角的今天', () => {
    expect(todayText(dayBars([], 'dinner', TODAY))).toBe('今天沒有紀錄')
    expect(todayText([todayBar(null, '650')])).toBe('今天 未知')
    expect(todayText([todayBar('624', '650')])).toBe('今天 96%')
  })
})

const basePlan = { meal_date: TODAY, adopted_at: '2026-11-14T03:00:00Z' }

describe('今天採用的那一餐', () => {
  it('挑出選到的餐別；沒有就是 null', () => {
    const plans: TodayPlan[] = [{ ...basePlan, id: 'a', meal_type: 'lunch', dishes: [] }]
    expect(planForMeal(plans, 'lunch')).toEqual({ plan: plans[0], others: 0 })
    expect(planForMeal(plans, 'dinner')).toEqual({ plan: null, others: 0 })
  })

  it('同一餐別有好幾筆時顯示最後採用的', () => {
    const plans: TodayPlan[] = [
      { ...basePlan, id: 'old', meal_type: 'dinner', dishes: [] },
      { ...basePlan, id: 'new', meal_type: 'dinner', dishes: [], adopted_at: '2026-11-14T09:00:00Z' },
    ]
    expect(planForMeal(plans, 'dinner')).toMatchObject({ plan: { id: 'new' }, others: 1 })
  })

  it('生成結果的紀錄：從 nutrition 讀合計與目標', () => {
    const plan: TodayPlan = {
      ...basePlan, id: 'g', meal_type: 'dinner', dishes: [], energy_kcal: null, protein_g: null,
      nutrition: { energy_kcal: '624', protein_g: '31.2', target: { energy_kcal: '650', protein_g: '30' } },
    }
    expect(planRatios(plan).energy).toBeCloseTo(0.96)
    expect(planRatios(plan).protein).toBeCloseTo(1.04)
  })

  it('生成結果的熱量未知：就是未知，不會被當成 0', () => {
    const plan: TodayPlan = {
      ...basePlan, id: 'g', meal_type: 'dinner', dishes: [],
      nutrition: { energy_kcal: null, protein_g: '31.2', target: { energy_kcal: '650', protein_g: '30' } },
    }
    expect(planRatios(plan).energy).toBeNull()
    expect(planRatios(plan).protein).toBeCloseTo(1.04)
  })

  it('資料庫菜單的紀錄：從快照讀合計與目標', () => {
    const plan: TodayPlan = {
      ...basePlan, id: 'd', meal_type: 'lunch', dishes: [], energy_kcal: '700', protein_g: '27',
      snapshot: { nutrition: { totals: { energy_kcal: '700', protein_g: '27' } }, targets: { energy_kcal: '700', protein_g: '30' } },
    }
    expect(planRatios(plan)).toEqual({ energy: 1, protein: 0.9 })
  })

  it('只有菜名的最小紀錄：兩項都未知', () => {
    const plan: TodayPlan = { ...basePlan, id: 'm', meal_type: 'lunch', dishes: [{ dish_name: '滷肉' }], snapshot: {} }
    expect(planRatios(plan)).toEqual({ energy: null, protein: null })
  })

  it('主食不在菜色清單裡，另外補在菜名後面', () => {
    const database: TodayPlan = {
      ...basePlan, id: 'd', meal_type: 'lunch', dishes: [{ dish_name: '香煎鮭魚' }, { dish_name: '清炒青江菜' }],
      snapshot: { staple: { name: '白飯', final_grams: '320' }, staple_choice: '白飯' },
    }
    expect(dishNames(database)).toEqual(['香煎鮭魚', '清炒青江菜', '白飯'])
    const unnamed: TodayPlan = { ...database, snapshot: { staple: {}, staple_choice: '糙米飯' } }
    expect(dishNames(unnamed)).toEqual(['香煎鮭魚', '清炒青江菜', '糙米飯'])
    const generated: TodayPlan = {
      ...basePlan, id: 'g', meal_type: 'dinner', dishes: [{ dish_name: '清蒸鱸魚' }], staple: { label: '糙米飯' },
      nutrition: { energy_kcal: null, protein_g: null, target: null },
    }
    expect(dishNames(generated)).toEqual(['清蒸鱸魚', '糙米飯'])
  })

  it('沒有主食就不補；主食已經在菜色裡也不重複', () => {
    const noStaple: TodayPlan = {
      ...basePlan, id: 'd', meal_type: 'lunch', dishes: [{ dish_name: '香煎鮭魚' }],
      snapshot: { staple: null, staple_choice: '不需要主食' },
    }
    expect(dishNames(noStaple)).toEqual(['香煎鮭魚'])
    const generated: TodayPlan = { ...basePlan, id: 'g', meal_type: 'dinner', dishes: [{ dish_name: '清蒸鱸魚' }], staple: null }
    expect(dishNames(generated)).toEqual(['清蒸鱸魚'])
    const repeated: TodayPlan = { ...basePlan, id: 'r', meal_type: 'dinner', dishes: [{ dish_name: '白飯' }], staple: { label: '白飯' } }
    expect(dishNames(repeated)).toEqual(['白飯'])
  })

  it('菜名排成一行，太多時說明總共幾道', () => {
    expect(dishLine(['A', 'B', 'C'])).toBe('A、B、C')
    expect(dishLine(['A', 'B', 'C', 'D'], 2)).toBe('A、B 等 4 道')
    expect(dishLine([])).toBe('')
  })
})
