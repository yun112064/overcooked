import { describe, expect, it } from 'vitest'
import type { Issue, MealRequest, Member, StapleOptions } from '../../api/types'
import {
  LAST_STEP, STEPS, STEP_FIELDS, fmt, formFieldOf, initialStep, issuesForStep, potSteps, requestedStep, schema, splitDishNames,
  stapleChoice, stepOfServerField, stepsWithIssues, toDraft, toForm, type FormValues,
} from './draft'

const issue = (field: string, reason = 'REQUIRED'): Issue => ({ field, reason, message: `${field}:${reason}` })

function member(id: string, name: string): Member {
  return {
    id, display_name: name, birth_date: '1990-01-01', age_years: 36, equation_sex: 'F', weight_kg: '55',
    activity_category: 'active', goal: 'general', portion: '1', allergens: [], avoided: [], version: 1, updated_at: '',
  }
}

function request(overrides: Partial<MealRequest> = {}): MealRequest {
  return {
    id: 'r1', status: 'draft', version: 3, updated_at: '',
    draft: {
      mode: 'questionnaire', meal_date: null, meal_type: null, budget: null, staple: null, dish_count: null,
      taste_preferences: '', specified_dishes: [], participants: [], available_ingredient_ids: [],
    },
    available_ingredients: [], items: [],
    restrictions: { ingredients: [], expanded_ingredients: [], excluded_food_ids: [], available_but_restricted: [] },
    budget_floor: null, targets_preview: { status: 'incomplete' },
    issues: ['meal_date', 'meal_type', 'budget', 'participants', 'staple', 'dish_count'].map((f) => issue(f)),
    complete: false,
    ...overrides,
  }
}

const members = [member('m1', '阿明'), member('m2', '小惠')]

describe('toForm／toDraft', () => {
  it('空白草稿：表單是空字串，存回去是 null（不是 0、不是空字串）', () => {
    const form = toForm(request(), members)
    expect(form).toMatchObject({ meal_date: '', meal_type: '', budget: '', staple: '', dish_count: '' })
    expect(Object.keys(form.participants)).toEqual(['m1', 'm2'])
    expect(form.participants.m1).toEqual({ dining: false, bento: false, bento_meal: 'lunch' })
    expect(toDraft(form)).toEqual(request().draft)
  })

  it('填好的草稿來回轉換後內容相同', () => {
    const draft = {
      mode: 'questionnaire' as const, meal_date: '2026-11-16', meal_type: 'dinner' as const, budget: '600', staple: '白飯',
      dish_count: 3, taste_preferences: '清淡', specified_dishes: ['番茄炒蛋'],
      participants: [{ member_id: 'm2', dining: true, bento: true, bento_meal: 'breakfast' as const }],
      available_ingredient_ids: ['F001'],
    }
    const row = request({ draft, available_ingredients: [{ ingredient_id: 'F001', name: '雞蛋' }] })
    expect(toDraft(toForm(row, members))).toEqual(draft)
  })

  it('沒勾用餐也沒勾便當的成員不送出；文字欄位去掉前後空白', () => {
    const form: FormValues = {
      ...toForm(request(), members),
      budget: ' 450 ', taste_preferences: '  少辣 ', specified_dishes: [' 滷肉 ', ''],
      participants: {
        m1: { dining: false, bento: false, bento_meal: 'lunch' },
        m2: { dining: false, bento: true, bento_meal: 'dinner' },
      },
    }
    const draft = toDraft(form)
    expect(draft.participants).toEqual([{ member_id: 'm2', dining: false, bento: true, bento_meal: 'dinner' }])
    expect(draft.budget).toBe('450')
    expect(draft.taste_preferences).toBe('少辣')
    expect(draft.specified_dishes).toEqual(['滷肉'])
  })

  it('草稿裡已被刪除的成員不會出現在表單，也不會再存回去', () => {
    const row = request({ draft: { ...request().draft, participants: [{ member_id: 'gone', dining: true, bento: false, bento_meal: 'lunch' }] } })
    expect(toDraft(toForm(row, members)).participants).toEqual([])
  })
})

describe('格式檢查', () => {
  const base = toForm(request(), members)
  const check = (patch: Partial<FormValues>) => schema.safeParse({ ...base, ...patch })

  it('沒填完也通過（只檢查格式，草稿可以先存）', () => {
    expect(check({}).success).toBe(true)
  })

  it('預算：大於 0、最多兩位小數、不超過 100000', () => {
    expect(check({ budget: '600' }).success).toBe(true)
    expect(check({ budget: '599.5' }).success).toBe(true)
    expect(check({ budget: '100000' }).success).toBe(true)
    for (const bad of ['0', '-5', 'abc', '1.234', '100001', '1e3']) expect(check({ budget: bad }).success).toBe(false)
  })

  it('指定菜色：最多 10 道、每道最多 50 字、不能只有數字或符號', () => {
    expect(check({ specified_dishes: ['番茄炒蛋', 'Curry'] }).success).toBe(true)
    expect(check({ specified_dishes: ['123'] }).success).toBe(false)
    expect(check({ specified_dishes: ['！！'] }).success).toBe(false)
    expect(check({ specified_dishes: ['菜'.repeat(51)] }).success).toBe(false)
    expect(check({ specified_dishes: Array.from({ length: 11 }, (_, i) => `菜${'一二三四五六七八九十甲'[i]}`) }).success).toBe(false)
  })

  it('不是菜名的錯誤訊息會寫出是哪一個', () => {
    const result = check({ specified_dishes: ['123'] })
    expect(result.success ? '' : result.error.issues[0].message).toContain('「123」不是菜名')
  })

  it('口味最多 200 字', () => {
    expect(check({ taste_preferences: '淡'.repeat(200) }).success).toBe(true)
    expect(check({ taste_preferences: '淡'.repeat(201) }).success).toBe(false)
  })
})

describe('步驟與後端欄位', () => {
  it('每個表單欄位都有歸屬的步驟，確認步驟沒有欄位', () => {
    expect(STEP_FIELDS).toHaveLength(STEPS.length)
    expect(STEP_FIELDS[LAST_STEP]).toEqual([])
    expect(STEP_FIELDS.flat().sort()).toEqual(Object.keys(schema.shape).sort())
  })

  it('後端欄位名稱對到步驟（1 日期預算、2 成員、3 現有食材、4 主食口味）', () => {
    expect(stepOfServerField('meal_date')).toBe(0)
    expect(stepOfServerField('budget')).toBe(0)
    expect(stepOfServerField('draft.participants.0.member_id')).toBe(1)
    expect(stepOfServerField('available_ingredient_ids')).toBe(2)
    expect(stepOfServerField('staple')).toBe(3)
    expect(stepOfServerField('dish_count')).toBe(3)
    expect(stepOfServerField('draft.specified_dishes.2')).toBe(3)
    expect(stepOfServerField('taste_preferences')).toBe(3)
  })

  it('不認得的欄位放在確認步驟，不亂猜', () => {
    expect(stepOfServerField('version')).toBe(LAST_STEP)
  })

  it('後端欄位名稱對到表單欄位；表單沒有的回 null', () => {
    expect(formFieldOf('draft.available_ingredient_ids')).toBe('available')
    expect(formFieldOf('draft.budget')).toBe('budget')
    expect(formFieldOf('version')).toBeNull()
    expect(formFieldOf('draft.mode')).toBeNull()
  })

  it('issuesForStep 只給那一步的問題', () => {
    const issues = [issue('budget', 'BUDGET_TOO_LOW'), issue('participants'), issue('staple', 'STAPLE_NOT_SUPPORTED')]
    expect(issuesForStep(issues, 0, LAST_STEP).map((i) => i.reason)).toEqual(['BUDGET_TOO_LOW'])
    expect(issuesForStep(issues, 1, LAST_STEP)).toHaveLength(1)
    expect(issuesForStep(issues, 2, LAST_STEP)).toEqual([])
    expect(issuesForStep(issues, 3, LAST_STEP).map((i) => i.field)).toEqual(['staple'])
  })

  it('「還沒填」要走過那一步才提醒；已存內容的問題一打開就提醒', () => {
    const issues = [issue('budget', 'BUDGET_TOO_LOW'), issue('meal_type'), issue('participants'), issue('staple', 'STAPLE_NOT_SUPPORTED')]
    // 剛打開（還在第 1 步）：只看得到已存內容的問題
    expect(issuesForStep(issues, 0, 0).map((i) => i.reason)).toEqual(['BUDGET_TOO_LOW'])
    expect(issuesForStep(issues, 1, 0)).toEqual([])
    expect(issuesForStep(issues, 3, 0).map((i) => i.reason)).toEqual(['STAPLE_NOT_SUPPORTED'])
    // 走到第 2 步：第 1 步沒填的也提醒了，第 2 步自己的還不提醒
    expect(issuesForStep(issues, 0, 1)).toHaveLength(2)
    expect(issuesForStep(issues, 1, 1)).toEqual([])
    expect(issuesForStep(issues, 1, 2)).toHaveLength(1)
  })

  it('stepsWithIssues 標出有問題的步驟；確認步驟不標', () => {
    const issues = [issue('budget'), issue('dish_count'), issue('version')]
    expect(stepsWithIssues(issues, LAST_STEP)).toEqual([true, false, false, true, false])
    expect(stepsWithIssues(issues, 1)).toEqual([true, false, false, false, false])
    expect(stepsWithIssues(issues, 0)).toEqual([false, false, false, false, false])
    expect(stepsWithIssues([issue('staple', 'STAPLE_NOT_SUPPORTED')], 0)).toEqual([false, false, false, true, false])
    expect(stepsWithIssues([], LAST_STEP)).toEqual([false, false, false, false, false])
  })
})

describe('stapleChoice', () => {
  const staples: StapleOptions = {
    options: [{ label: '白飯', aliases: ['米飯', '飯'] }, { label: '糙米飯', aliases: [] }],
    none_value: '不需要主食', none_values: ['無', '不用'], config_version: 'v1',
  }

  it('空白、標準名稱、別名、各種「不需要」的寫法', () => {
    expect(stapleChoice('', staples)).toBe('')
    expect(stapleChoice('  ', staples)).toBe('')
    expect(stapleChoice('白飯', staples)).toBe('白飯')
    expect(stapleChoice('米飯', staples)).toBe('白飯')
    expect(stapleChoice('無', staples)).toBe('不需要主食')
    expect(stapleChoice('不需要主食', staples)).toBe('不需要主食')
  })

  it('清單外的舊文字回 null（畫面顯示為待改選）', () => {
    expect(stapleChoice('義大利麵', staples)).toBeNull()
  })
})

describe('initialStep', () => {
  it('草稿從第 1 步開始', () => {
    expect(initialStep('draft')).toBe(0)
  })

  it('生成中、有結果、已採用、失敗都直接開在確認步驟', () => {
    for (const status of ['queued', 'running', 'result_ready', 'awaiting_decision', 'confirmed', 'failed']) {
      expect(initialStep(status)).toBe(LAST_STEP)
    }
  })
})

describe('potSteps（鍋裡幾樣料）', () => {
  it('什麼都沒填 → 0', () => {
    expect(potSteps(request(), 0)).toBe(0)
  })

  it('第 1 步存好 → 1；成員存好 → 2', () => {
    expect(potSteps(request({ issues: ['participants', 'staple', 'dish_count'].map((f) => issue(f)) }), 1)).toBe(1)
    expect(potSteps(request({ issues: ['staple', 'dish_count'].map((f) => issue(f)) }), 2)).toBe(2)
  })

  it('第 3 步可以略過：走到第 4 步就算完成', () => {
    const row = request({ issues: ['staple', 'dish_count'].map((f) => issue(f)) })
    expect(potSteps(row, 2)).toBe(2)
    expect(potSteps(row, 3)).toBe(3)
    expect(potSteps(row, 4)).toBe(3)
  })

  it('前面還沒填好時，走到後面的步驟也不會多加料', () => {
    expect(potSteps(request(), 4)).toBe(0)
    expect(potSteps(request({ issues: ['participants', 'staple'].map((f) => issue(f)) }), 4)).toBe(1)
  })

  it('第 4 步填好但預算太低（不完整）→ 4，不會全滿', () => {
    expect(potSteps(request({ issues: [issue('budget', 'BUDGET_TOO_LOW')] }), 4)).toBe(4)
  })

  it('後端說完整 → 5（全滿）', () => {
    expect(potSteps(request({ issues: [], complete: true }), 0)).toBe(5)
  })
})

describe('splitDishNames', () => {
  it('依逗號、頓號、換行切開，去掉空白與重複', () => {
    expect(splitDishNames('番茄炒蛋')).toEqual(['番茄炒蛋'])
    expect(splitDishNames(' 滷肉、番茄炒蛋，滷肉,\n三杯雞 ')).toEqual(['滷肉', '番茄炒蛋', '三杯雞'])
    expect(splitDishNames('  ')).toEqual([])
  })
})

describe('fmt', () => {
  it('數字取整；不是數字原樣顯示', () => {
    expect(fmt('1820.4', 0)).toBe('1820')
    expect(fmt('68.25')).toBe('68.3')
    expect(fmt('—')).toBe('—')
  })
})

describe('requestedStep（網址上的 ?step=）', () => {
  it('草稿照網址開在那一步（1 起算 → 0 起算）', () => {
    expect(requestedStep('3', 'draft')).toBe(2)
    expect(requestedStep('1', 'draft')).toBe(0)
    expect(requestedStep('5', 'draft')).toBe(LAST_STEP)
  })

  it('沒給、不是 1–5、或不是草稿：不理它', () => {
    expect(requestedStep(null, 'draft')).toBeNull()
    for (const raw of ['0', '6', '-1', '2.5', 'abc', '', '03']) expect(requestedStep(raw, 'draft')).toBeNull()
    for (const status of ['queued', 'running', 'failed', 'result_ready', 'awaiting_decision', 'confirmed']) {
      expect(requestedStep('3', status)).toBeNull()
    }
  })
})
