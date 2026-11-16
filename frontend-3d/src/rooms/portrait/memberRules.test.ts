import { describe, expect, it } from 'vitest'
import type { Member, MemberOptions } from '../../api/types'
import {
  ageOn, backendToday, buildSchema, isMinor, minorGoalHint, serverField, toBody, toForm, type FormValues,
} from './memberRules'

// 和後端 GET /members/options 回的一樣
const OPTIONS: MemberOptions = {
  rule_version: 'hpa-dri8-mvp-0.4.1',
  portions: ['0.7', '0.85', '1', '1.15', '1.3'],
  activity_categories: ['sedentary', 'low_active', 'active', 'highly_active'],
  goals: ['general', 'maintain', 'muscle_gain', 'fat_loss'],
  adult_goals_min_age_years: 19,
  minor_goals: ['general'],
}
const TODAY = '2026-11-14'
const schema = buildSchema(OPTIONS, () => TODAY)

const VALID: FormValues = {
  display_name: '阿明', birth_date: '1984-05-20', equation_sex: 'M', weight_kg: '72',
  activity_category: 'low_active', goal: 'maintain', portion: '1.15',
  allergens: [{ ingredient_id: 'F001', name: '花生' }], avoided: [{ ingredient_id: 'F002', name: null }],
}

/** 驗證失敗時，回傳「欄位 → 訊息」。同一欄有好幾個問題時取第一個，和表單實際顯示的一樣。 */
function problems(values: Record<string, unknown>): Record<string, string> {
  const result = schema.safeParse(values)
  const out: Record<string, string> = {}
  if (result.success) return out
  for (const issue of result.error.issues) out[issue.path.join('.')] ??= issue.message
  return out
}

describe('backendToday', () => {
  it('用 UTC 日期（和後端算今天的方式一樣），不是本機日期', () => {
    // 台灣 11/15 早上 7 點＝UTC 11/14 23:00
    expect(backendToday(new Date('2026-11-14T23:00:00Z'))).toBe('2026-11-14')
    expect(backendToday(new Date('2026-11-15T00:00:00Z'))).toBe('2026-11-15')
  })
})

describe('ageOn', () => {
  it('生日還沒到就少一歲', () => {
    expect(ageOn('2007-11-14', '2026-11-14')).toBe(19)
    expect(ageOn('2007-11-15', '2026-11-14')).toBe(18)
    expect(ageOn('2007-12-01', '2026-11-14')).toBe(18)
    expect(ageOn('2026-11-14', '2026-11-14')).toBe(0)
  })

  it('不是完整日期回 null', () => {
    expect(ageOn('', TODAY)).toBeNull()
    expect(ageOn('2007-1-1', TODAY)).toBeNull()
  })
})

describe('isMinor', () => {
  it('以選項裡的成年年齡為準', () => {
    expect(isMinor('2007-11-15', TODAY, OPTIONS)).toBe(true)
    expect(isMinor('2007-11-14', TODAY, OPTIONS)).toBe(false)
    expect(isMinor('', TODAY, OPTIONS)).toBe(false)
    expect(isMinor('2007-11-14', TODAY, { adult_goals_min_age_years: 20 })).toBe(true)
  })

  it('說明文字跟著選項的年齡', () => {
    expect(minorGoalHint(OPTIONS)).toBe('未滿 19 歲只提供一般餐食')
  })
})

describe('buildSchema：表單驗證', () => {
  it('合格的內容通過，名稱與體重會去掉前後空白', () => {
    const result = schema.safeParse({ ...VALID, display_name: '  阿明 ', weight_kg: ' 72.5 ' })
    expect(result.success).toBe(true)
    expect(result.data?.display_name).toBe('阿明')
    expect(result.data?.weight_kg).toBe('72.5')
  })

  it('名稱：必填、最多 50 個字', () => {
    expect(problems({ ...VALID, display_name: '   ' })).toEqual({ display_name: '請輸入成員名稱' })
    expect(problems({ ...VALID, display_name: '字'.repeat(51) })).toEqual({ display_name: '最多 50 個字' })
    expect(problems({ ...VALID, display_name: '字'.repeat(50) })).toEqual({})
  })

  it('出生日期：必填、不能晚於今天（今天可以）', () => {
    expect(problems({ ...VALID, birth_date: '' }).birth_date).toBe('請選擇出生日期')
    expect(problems({ ...VALID, birth_date: '2026-11-15', goal: 'general' })).toEqual({ birth_date: '出生日期不能晚於今天' })
    expect(problems({ ...VALID, birth_date: TODAY, goal: 'general' })).toEqual({})
  })

  it('公式性別只能是 M 或 F', () => {
    expect(problems({ ...VALID, equation_sex: '' })).toEqual({ equation_sex: '請選擇公式性別' })
  })

  it('體重：最多三位整數兩位小數，大於 0 且不超過 500', () => {
    const message = '請輸入體重（公斤，最多兩位小數）'
    expect(problems({ ...VALID, weight_kg: '' }).weight_kg).toBe(message)
    expect(problems({ ...VALID, weight_kg: '七十' }).weight_kg).toBe(message)
    expect(problems({ ...VALID, weight_kg: '72.555' }).weight_kg).toBe(message)
    expect(problems({ ...VALID, weight_kg: '-5' }).weight_kg).toBe(message)
    expect(problems({ ...VALID, weight_kg: '0' })).toEqual({ weight_kg: '體重需大於 0 且不超過 500' })
    expect(problems({ ...VALID, weight_kg: '501' })).toEqual({ weight_kg: '體重需大於 0 且不超過 500' })
    expect(problems({ ...VALID, weight_kg: '500' })).toEqual({})
    expect(problems({ ...VALID, weight_kg: '0.01' })).toEqual({})
  })

  it('食量、活動量、目標只能是後端選項裡的值', () => {
    expect(problems({ ...VALID, portion: '2' })).toEqual({ portion: '請選擇食量' })
    expect(problems({ ...VALID, activity_category: 'couch' })).toEqual({ activity_category: '請選擇活動量' })
    expect(problems({ ...VALID, goal: 'bulk' })).toEqual({ goal: '請選擇飲食目標' })
  })

  it('未滿 19 歲只能選一般餐食；滿 19 歲當天起可以選其他目標', () => {
    expect(problems({ ...VALID, birth_date: '2007-11-15', goal: 'fat_loss' })).toEqual({ goal: '未滿 19 歲只能選擇一般餐食' })
    expect(problems({ ...VALID, birth_date: '2007-11-15', goal: 'general' })).toEqual({})
    expect(problems({ ...VALID, birth_date: '2007-11-14', goal: 'fat_loss' })).toEqual({})
  })

  it('過敏與禁吃可以是空的', () => {
    expect(problems({ ...VALID, allergens: [], avoided: [] })).toEqual({})
  })
})

describe('toForm／toBody', () => {
  const member: Member = {
    id: 'm1', display_name: '小惠', birth_date: '1986-03-02', age_years: 40, equation_sex: 'F', weight_kg: '55',
    activity_category: 'active', goal: 'general', portion: '1',
    allergens: [{ ingredient_id: 'F001', name: '花生' }], avoided: [], version: 3, updated_at: '2026-11-01T00:00:00Z',
  }

  it('新增的預設值和舊前端相同', () => {
    expect(toForm(null)).toEqual({
      display_name: '', birth_date: '', equation_sex: 'F', weight_kg: '',
      activity_category: 'low_active', goal: 'general', portion: '1', allergens: [], avoided: [],
    })
  })

  it('編輯時帶入成員現有的資料，不帶 id、年齡與版本', () => {
    const form = toForm(member)
    expect(form.display_name).toBe('小惠')
    expect(form.allergens).toEqual([{ ingredient_id: 'F001', name: '花生' }])
    expect(Object.keys(form).sort()).toEqual(Object.keys(toForm(null)).sort())
  })

  it('送出時食材只送代碼，欄位名稱是後端的', () => {
    expect(toBody(VALID)).toEqual({
      display_name: '阿明', birth_date: '1984-05-20', equation_sex: 'M', weight_kg: '72',
      activity_category: 'low_active', goal: 'maintain', portion: '1.15',
      allergen_ingredient_ids: ['F001'], avoided_ingredient_ids: ['F002'],
    })
  })

  it('成員資料原樣進表單再送出，內容不變', () => {
    expect(toBody(toForm(member))).toEqual({
      display_name: '小惠', birth_date: '1986-03-02', equation_sex: 'F', weight_kg: '55',
      activity_category: 'active', goal: 'general', portion: '1',
      allergen_ingredient_ids: ['F001'], avoided_ingredient_ids: [],
    })
  })
})

describe('serverField：後端的欄位錯誤要顯示在哪一欄', () => {
  const error = (field?: string) => ({ code: 'INVALID_INPUT', message: '不行', details: field ? { field } : undefined })

  it('同名的欄位直接對應', () => {
    expect(serverField(error('goal'))).toBe('goal')
    expect(serverField(error('birth_date'))).toBe('birth_date')
    expect(serverField(error('portion'))).toBe('portion')
  })

  it('食材的欄位名稱不同，要換成表單的', () => {
    expect(serverField(error('allergen_ingredient_ids'))).toBe('allergens')
    expect(serverField(error('avoided_ingredient_ids'))).toBe('avoided')
  })

  it('沒有指明欄位、或不是表單裡的欄位，回 null（改顯示在表單最上面）', () => {
    expect(serverField(error())).toBeNull()
    expect(serverField(error('version'))).toBeNull()
    expect(serverField(null)).toBeNull()
    expect(serverField({ code: 'VERSION_CONFLICT', message: '資料已被更新' })).toBeNull()
  })
})
