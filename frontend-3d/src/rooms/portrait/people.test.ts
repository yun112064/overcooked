import { describe, expect, it } from 'vitest'
import type { IngredientRef } from '../../api/types'
import {
  MAX_DRAWN, ONE_ROW_MAX, crayonKind, familyStats, figureBox, figureScale, ingredientLabel, layoutPeople,
  memberSummary, plainNumber, summaryText, sumPortions,
} from './people'

describe('crayonKind：依性別與年齡選蠟筆人物', () => {
  it('12 歲以下是小孩，不分性別', () => {
    expect(crayonKind({ equation_sex: 'M', age_years: 5 })).toBe('kid')
    expect(crayonKind({ equation_sex: 'F', age_years: 12 })).toBe('kid')
    expect(crayonKind({ equation_sex: 'F', age_years: 0 })).toBe('kid')
  })

  it('65 歲以上是長輩，不分性別', () => {
    expect(crayonKind({ equation_sex: 'F', age_years: 78 })).toBe('elder')
    expect(crayonKind({ equation_sex: 'M', age_years: 65 })).toBe('elder')
  })

  it('中間的年齡照性別畫', () => {
    expect(crayonKind({ equation_sex: 'M', age_years: 13 })).toBe('man')
    expect(crayonKind({ equation_sex: 'F', age_years: 13 })).toBe('woman')
    expect(crayonKind({ equation_sex: 'M', age_years: 64 })).toBe('man')
    expect(crayonKind({ equation_sex: 'F', age_years: 40 })).toBe('woman')
  })

  it('年齡讀不到時不猜小孩或長輩，只照性別', () => {
    expect(crayonKind({ equation_sex: 'F', age_years: null })).toBe('woman')
    expect(crayonKind({ equation_sex: 'M', age_years: undefined })).toBe('man')
    expect(crayonKind({ equation_sex: 'M', age_years: Number.NaN })).toBe('man')
  })
})

describe('figureScale', () => {
  it('小孩最矮、青少年略矮、其餘一樣大', () => {
    expect(figureScale(5)).toBeLessThan(figureScale(15))
    expect(figureScale(15)).toBeLessThan(figureScale(30))
    expect(figureScale(30)).toBe(1)
    expect(figureScale(80)).toBe(1)
    expect(figureScale(null)).toBe(1)
  })
})

describe('layoutPeople：把人排在圖畫紙上', () => {
  // 圖畫紙是 510–1090
  const insidePaper = (count: number) =>
    layoutPeople(count).every((slot) => slot.x - slot.width / 2 >= 510 && slot.x + slot.width / 2 <= 1090)

  it('沒有人就沒有格子', () => {
    expect(layoutPeople(0)).toEqual([])
  })

  it('只有一格時放在紙的正中間', () => {
    const [slot] = layoutPeople(1)
    expect(slot.x).toBe(800)
    expect(slot.row).toBe('single')
  })

  it('五格以內排一排，由左到右、左右對稱', () => {
    for (let count = 1; count <= ONE_ROW_MAX; count += 1) {
      const slots = layoutPeople(count)
      expect(slots).toHaveLength(count)
      expect(slots.every((slot) => slot.row === 'single')).toBe(true)
      expect(slots.map((slot) => slot.x)).toEqual([...slots.map((slot) => slot.x)].sort((a, b) => a - b))
      expect(slots[0].x + slots[count - 1].x).toBeCloseTo(1600)
      expect(insidePaper(count)).toBe(true)
    }
  })

  it('超過五格排兩排，前排不比後排少，後排站得比較高', () => {
    for (let count = ONE_ROW_MAX + 1; count <= MAX_DRAWN + 1; count += 1) {
      const slots = layoutPeople(count)
      const back = slots.filter((slot) => slot.row === 'back')
      const front = slots.filter((slot) => slot.row === 'front')
      expect(slots).toHaveLength(count)
      expect(back.length + front.length).toBe(count)
      expect(front.length).toBeGreaterThanOrEqual(back.length)
      expect(front.length - back.length).toBeLessThanOrEqual(1)
      expect(back[0].feet).toBeLessThan(front[0].feet)
      expect(insidePaper(count)).toBe(true)
    }
  })

  it('最後一格（畫上新成員）在前排最右邊', () => {
    const slots = layoutPeople(9)
    const last = slots[slots.length - 1]
    expect(last.row).toBe('front')
    expect(last.x).toBe(Math.max(...slots.map((slot) => slot.x)))
  })

  it('同一排的格子不重疊', () => {
    for (const count of [3, 5, 6, 9, 13]) {
      const slots = layoutPeople(count)
      for (const row of ['single', 'back', 'front'] as const) {
        const xs = slots.filter((slot) => slot.row === row)
        for (let i = 1; i < xs.length; i += 1) {
          expect(xs[i].x - xs[i - 1].x).toBeGreaterThanOrEqual(xs[i].width - 0.2)
        }
      }
    }
  })

  it('畫得下的最多人數時，每一格在手機上仍然點得到（390px 看 620 單位，至少 44px）', () => {
    const pxPerUnit = 390 / 620
    for (const slot of layoutPeople(MAX_DRAWN + 1)) expect(slot.width * pxPerUnit).toBeGreaterThanOrEqual(44)
  })

  it('兩排時前排的頭不會頂到後排腳下的名字（留至少 80 單位寫字）', () => {
    const slots = layoutPeople(9)
    const back = slots.find((slot) => slot.row === 'back')!
    const front = slots.find((slot) => slot.row === 'front')!
    expect(figureBox(front, 1).top - back.feet).toBeGreaterThanOrEqual(80)
  })

  it('人物不會比格子寬', () => {
    for (const slot of layoutPeople(13)) expect(slot.figure).toBeLessThanOrEqual(slot.width)
  })
})

describe('figureBox', () => {
  it('腳踩在這一格的 feet 上：上緣＝feet − 寬 × 200/120', () => {
    const [slot] = layoutPeople(1)
    const box = figureBox(slot, 1)
    expect(box.width).toBe(slot.figure)
    expect(box.top + box.width * (200 / 120)).toBeCloseTo(slot.feet)
  })

  it('小孩比較矮，但腳還是踩在同一條線上', () => {
    const [slot] = layoutPeople(1)
    const adult = figureBox(slot, 1)
    const kid = figureBox(slot, 0.78)
    expect(kid.top).toBeGreaterThan(adult.top)
    expect(kid.top + kid.width * (200 / 120)).toBeCloseTo(slot.feet)
  })
})

describe('memberSummary：名字下面那一行', () => {
  it('年齡與活動量', () => {
    const pieces = memberSummary({ age_years: 42, activity_category: 'low_active' })
    expect(pieces).toEqual([{ text: '42 歲', missing: false }, { text: '活動量稍低', missing: false }])
    expect(summaryText(pieces)).toBe('42 歲、活動量稍低')
  })

  it('0 歲是真的 0，不是沒填', () => {
    expect(memberSummary({ age_years: 0, activity_category: 'sedentary' })[0]).toEqual({ text: '0 歲', missing: false })
  })

  it('沒有資料的欄位標成未填，不猜也不寫 0', () => {
    const pieces = memberSummary({ age_years: null, activity_category: undefined })
    expect(pieces).toEqual([{ text: '年齡未填', missing: true }, { text: '活動量未填', missing: true }])
    expect(summaryText(pieces)).not.toContain('0')
  })

  it('後端說每日目標算不出來時多一段，也用鉛筆底線', () => {
    const pieces = memberSummary({ age_years: 78, activity_category: 'sedentary' }, true)
    expect(pieces[2]).toEqual({ text: '目標算不出來', missing: true })
    expect(memberSummary({ age_years: 78, activity_category: 'sedentary' }, false)).toHaveLength(2)
  })
})

describe('ingredientLabel', () => {
  it('有名稱用名稱，沒有就顯示代碼', () => {
    expect(ingredientLabel({ ingredient_id: 'F001', name: '花生' })).toBe('花生')
    expect(ingredientLabel({ ingredient_id: 'F001', name: null })).toBe('F001')
  })
})

describe('sumPortions：食量加總不經過浮點數', () => {
  it('和舊前端的顯示一樣：去掉多餘的 0', () => {
    expect(sumPortions(['1.15', '1', '0.85', '0.7'])).toBe('3.7')
    expect(sumPortions(['1', '1'])).toBe('2')
    expect(sumPortions(['0.85', '0.85'])).toBe('1.7')
    expect(sumPortions(['1.15'])).toBe('1.15')
    expect(sumPortions(['0.7', '0.7', '0.7'])).toBe('2.1')
  })

  it('沒有人是 0 份', () => {
    expect(sumPortions([])).toBe('0')
  })

  it('有一個讀不懂就是未知，不當成 0 加進去', () => {
    expect(sumPortions(['1', 'abc'])).toBeNull()
    expect(sumPortions(['1', null])).toBeNull()
    expect(sumPortions(['1', ''])).toBeNull()
    expect(sumPortions(['1.234'])).toBeNull()
  })
})

describe('familyStats：名單上方的統計', () => {
  const ref = (id: string): IngredientRef => ({ ingredient_id: id, name: id })

  it('人數、合計食量、有過敏與有不吃的人數和食材種類', () => {
    const stats = familyStats([
      { portion: '1.15', allergens: [], avoided: [ref('茄子')] },
      { portion: '1', allergens: [ref('花生')], avoided: [] },
      { portion: '0.85', allergens: [ref('白蝦'), ref('花生')], avoided: [ref('青椒'), ref('茄子')] },
      { portion: '0.7', allergens: [], avoided: [] },
    ])
    expect(stats).toEqual({
      count: 4,
      portionTotal: '3.7',
      allergic: { people: 2, ingredients: 2 },
      avoiding: { people: 2, ingredients: 2 },
    })
  })

  it('沒有成員', () => {
    expect(familyStats([])).toEqual({
      count: 0, portionTotal: '0', allergic: { people: 0, ingredients: 0 }, avoiding: { people: 0, ingredients: 0 },
    })
  })
})

describe('plainNumber：後端的數字字串', () => {
  it('去掉多餘的 0、加千分位，不四捨五入', () => {
    expect(plainNumber('77.0')).toBe('77')
    expect(plainNumber('2139.50')).toBe('2,139.5')
    expect(plainNumber('1800')).toBe('1,800')
    expect(plainNumber('64.25')).toBe('64.25')
    expect(plainNumber('0')).toBe('0')
    expect(plainNumber(1234567)).toBe('1,234,567')
  })

  it('沒有值或不是數字是未知（null），不是 0', () => {
    expect(plainNumber(null)).toBeNull()
    expect(plainNumber(undefined)).toBeNull()
    expect(plainNumber('')).toBeNull()
    expect(plainNumber('abc')).toBeNull()
  })
})
