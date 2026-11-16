import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import { PlanDetail } from './PlanDetail'
import type { AdoptedPlan } from './records'

/** 只看畫出來的文字（去掉標籤），確認使用者讀到什麼。 */
const text = (plan: AdoptedPlan) => renderToStaticMarkup(<PlanDetail plan={plan} />).replace(/<[^>]+>/g, '')

const daily = {
  energy_kcal: { kind: 'target' as const, value: '2000', label: '每日熱量' },
  sodium_mg: { kind: 'max' as const, max: '2400', label: '鈉上限' },
}

const generationPlan = {
  id: 'p1', meal_date: '2026-11-14', meal_type: 'dinner', mode: 'ai_generated_menu', mode_label: 'AI 生成菜單', ai_generated: true,
  status: 'adopted', adopted_at: '2026-11-14T10:00:00Z', meal_request_id: 'r1', result_version: 2, price_exception_accepted: true,
  fallback_message: '食譜資料庫沒有可行組合，已自動改由 AI 生成。',
  dishes: [{
    standard_dish_id: 'd1', dish_name: '蘿蔔排骨湯', aliases: ['排骨湯'], cooking_method: '煮', high_sodium: true, sodium_mg_per_serving: '960',
    steps: ['切塊', '加[調味]'], steps_display: ['切塊', '加鹽'],
    ingredients: [
      { name: '白蘿蔔', group: null, display_amount: null, display_unit: null, display_text: null, display_grams: '300' },
      { name: '鹽', group: '調味', display_amount: '0.5', display_unit: '小匙', display_text: null, display_grams: null },
    ],
    recipe_source: { recipe_id: 'x', source_name: '示範食譜庫', source_url: 'https://example.org/recipe' },
  }],
  prices: {
    status: 'computed', budget: '500', budget_status: 'PRICE_INCOMPLETE', price_complete: false,
    purchase_total: null, cost_total: null, known_purchase_total: '380', known_cost_total: '212.4', items: [],
  },
  nutrition: {
    total_n: '3', target: { energy_kcal: '2400', protein_g: '90' }, energy_kcal: '2300', protein_g: '88',
    gap: { energy_kcal: '-100' }, within_range: true,
    other_nutrients: { carbohydrate_g: '300', fat_g: '70', sugars_g: null, sodium_mg: '3000', dietary_fiber_g: null },
    missing_nutrients: {}, known_partial: { sugars_g: '12' }, missing_by_nutrient: { sugars_g: ['醬油'] },
    per_person: [{
      member_id: 'm1', name: '小安', kinds: ['dining'], dining: true, n: '1', share: '0.3', meal_ratio: '0.4',
      intake: { energy_kcal: '900', protein_g: '30', sodium_mg: '1100', sugars_g: null }, known_partial: { sugars_g: '4' }, daily,
    }],
    daily_reference: { version: 'v1', source: { publisher: '國健署', title: '每日飲食指南', url: 'https://example.org/dri', verification: '' } },
  },
  knowledge: {
    status: 'available',
    items: [{
      ingredient_id: 'i1', ingredient_name: '白蘿蔔', explanation: '富含膳食纖維。',
      citations: [{ knowledge_id: 'k1', source_version: 'v3', ingredient_id: 'i1', excerpt: '', source_url: 'https://example.org/k', rights: null, conditions: null, attribution: '資料來源：健康醫療網' }],
    }],
  },
  previous_day: { previous_date: '2026-11-13', has_records: true, excluded_dishes: [{ standard_dish_id: 'd9', dish_name: '滷肉', aliases: [] }], specified_repeats: [], history_changed: false },
} as unknown as AdoptedPlan

describe('PlanDetail：生成結果的紀錄', () => {
  const out = text(generationPlan)

  it('標示來源模式、版本與唯讀', () => {
    expect(out).toContain('AI 生成菜單')
    expect(out).toContain('部分價格未知')
    expect(out).toContain('結果第 2 版')
    expect(out).toContain('只能看、不能改')
    expect(out).toContain('已自動改由 AI 生成')
  })

  it('整包花費與按比例成本是兩個數字；未知寫未知並附已知部分，不寫 0', () => {
    expect(out).toContain('按比例成本未知（已知 NT$212）')
    expect(out).toContain('整包花費未知（已知 NT$380）')
    expect(out).toContain('預算NT$500')
    expect(out).not.toContain('NT$0')
    // 不相加：212 + 380 = 592 不應該出現
    expect(out).not.toContain('592')
  })

  it('菜色：別名、高鈉提醒（文字）、食材分組、展開過的步驟、食譜來源', () => {
    expect(out).toContain('蘿蔔排骨湯')
    expect(out).toContain('又名 排骨湯')
    expect(out).toContain('高鈉・960 mg')
    expect(out).toContain('約占每日上限 2400 mg 的 40%')
    expect(out).toContain('白蘿蔔 300 g')
    expect(out).toContain('調味：鹽 ½ 小匙')
    expect(out).toContain('加鹽')
    expect(out).not.toContain('加[調味]')
    expect(out).toContain('食譜來源：示範食譜庫')
  })

  it('每人營養：未知附已知部分；上限類超過這一餐的份額用文字提醒；熱量有三態判定', () => {
    expect(out).toContain('小安')
    expect(out).toContain('這一餐約占每日熱量 45%')
    expect(out).toContain('建議約 40%')
    // 45% ÷ 40% = 112.5% → 超出
    expect(out).toContain('超出')
    expect(out).toContain('未知（已知 4.0 g）')
    expect(out).toContain('超過這一餐的份額')
  })

  it('小知識排在菜色前面，不用捲過所有做法才看得到', () => {
    expect(out.indexOf('食材小知識')).toBeGreaterThan(-1)
    expect(out.indexOf('食材小知識')).toBeLessThan(out.indexOf('菜色'))
  })

  it('小知識旁一定有來源', () => {
    expect(out).toContain('富含膳食纖維。')
    expect(out).toContain('資料來源：健康醫療網')
    expect(renderToStaticMarkup(<PlanDetail plan={generationPlan} />)).toContain('href="https://example.org/k"')
  })

  it('昨日比對', () => {
    expect(out).toContain('昨日比對（2026-11-13）：排除 滷肉')
  })

  it('主食列在菜色最後：有份量寫份量，算不出來寫未知與原因；沒有主食就不列', () => {
    const staple = {
      label: '白飯', tfda_name: '白飯', food_id: 'f9', review_status: 'pending_review', unresolved_reason: null,
      whole_meal_grams: '600', display_amount: '600', display_unit: 'g', display_text: null, counts_for_price: false as const,
    }
    const withStaple = text({ ...generationPlan, staple })
    expect(withStaple).toContain('白飯主食')
    expect(withStaple).toContain('白飯 600 g')
    expect(withStaple).toContain('計入營養驗證，不計入價格；份量設定待營養審核')
    // 排在菜色後面
    expect(withStaple.indexOf('白飯主食')).toBeGreaterThan(withStaple.indexOf('食譜來源：示範食譜庫'))

    const unresolved = text({ ...generationPlan, staple: { ...staple, display_amount: null, review_status: null, unresolved_reason: 'no_tfda_cooked_sample' } })
    expect(unresolved).toContain('份量未知（這個主食當時沒有營養資料，份量沒有算出來）')
    expect(unresolved).not.toContain('白飯 0')

    expect(out).not.toContain('計入營養驗證')
    expect(text({ ...generationPlan, staple: null })).not.toContain('計入營養驗證')
  })

  it('三大營養素有缺值時不畫比例', () => {
    const missing = { ...generationPlan, nutrition: { ...generationPlan.nutrition!, other_nutrients: { carbohydrate_g: null, fat_g: '70' } } }
    expect(text(missing)).toContain('有營養素缺值，算不出比例')
    expect(out).toContain('碳水')
  })
})

describe('PlanDetail：資料庫菜單的紀錄', () => {
  const snapshotPlan = {
    id: 'p2', meal_date: '2026-11-12', meal_type: 'lunch', mode: 'database_menu', status: 'adopted',
    adopted_at: '2026-11-12T03:00:00Z', meal_request_id: 'r2', result_version: null,
    dishes: [{ dish_name: '番茄炒蛋', recipe_id: 'r1' }], energy_kcal: '1500', protein_g: '60',
    snapshot: {
      selected: [{ recipe_id: 'r1', recipe_name: '番茄炒蛋', standard_dish_id: 's1', category: '蛋類', aliases: [], food_ids: [], specified: true }],
      staple_choice: '白飯',
      targets: { energy_kcal: '1600', protein_g: '70' },
      nutrition: { totals: { energy_kcal: '1500', protein_g: '60' } },
      dishes: [{
        recipe_id: 'r1', adjusted: false, steps: ['炒'],
        ingredients: [
          { name: '番茄', food_id: 'f1', unit: null, grams: '300', group: '主食材', display_amount: null, display_unit: null, display_text: null },
          { name: '鹽', food_id: 'f2', unit: null, grams: null, group: '調味', display_amount: null, display_unit: null, display_text: '少許' },
        ],
      }],
      staple: { name: '白飯', final_grams: '480' },
      people: [{
        member_id: 'm1', name: '爸爸', kinds: ['dining'], bento: null, daily,
        dining: {
          participation_id: 'x', n: '1', share: '1', meal: 'lunch', meal_ratio: '0.3', dish_share: '1', dish_ids: ['r1'], staple_g: '480',
          intake: { energy_kcal: '600' }, known_partial: {},
        },
      }],
    },
  } as unknown as AdoptedPlan

  it('整餐數字、目標與三態判定', () => {
    const out = text(snapshotPlan)
    expect(out).toContain('資料庫菜單')
    expect(out).toContain('整餐熱量1500 kcal')
    expect(out).toContain('目標 1600 kcal')
    expect(out).toContain('主食：白飯')
    // 蛋白質 60 ÷ 70 = 86% → 不足
    expect(out).toContain('不足')
  })

  it('全家配方：分組、原食譜用量的說明、主食碗數、每人份量', () => {
    const out = text(snapshotPlan)
    expect(out).toContain('指定菜')
    expect(out).toContain('原食譜用量（營養計算未通過，未依目標調整）')
    expect(out).toContain('主食材')
    expect(out).toContain('番茄300 g')
    expect(out).toContain('鹽少許')
    expect(out).toContain('480 g（約 3 碗）')
    expect(out).toContain('番茄炒蛋 1.00 份、白飯 480 g（約 3 碗）')
  })

  it('只有菜名的最小紀錄：熱量寫未知，不寫 0', () => {
    const minimal = { ...snapshotPlan, snapshot: {}, energy_kcal: null, protein_g: null } as AdoptedPlan
    const out = text(minimal)
    expect(out).toContain('整餐熱量未知')
    expect(out).toContain('整餐蛋白質未知')
    expect(out).toContain('這筆紀錄只保存了菜名')
    expect(out).toContain('番茄炒蛋')
    expect(out).not.toContain('0 kcal')
  })
})
