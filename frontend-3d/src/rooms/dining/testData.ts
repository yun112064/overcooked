/**
 * 測試用的假結果（只給 *.test 檔用，畫面程式不要匯入）。
 * 一桌三道菜加糙米飯、四個人：阿明通過、小安熱量超出、阿嬤算不出來、小惠只帶便當而且蛋白質不足；
 * 鱸魚片查不到價格（總額未知，只給已知部分）；小知識兩則。
 */
import type { DailyReference, MealRequest, MealResult, PriceItem, ResultIngredient, ResultPersonNutrition } from '../../api/types'

export const DAILY: Record<string, DailyReference> = {
  energy_kcal: { kind: 'target', value: '2000', label: '每日熱量目標' },
  protein_g: { kind: 'target', value: '60', label: '每日蛋白質目標' },
  carbohydrate_g: { kind: 'range', min: '250', max: '300', label: '碳水化合物建議範圍' },
  sugars_g: { kind: 'max', max: '50', label: '添加糖上限', caveat: '對照的是添加糖' },
  sodium_mg: { kind: 'max', max: '2400', label: '鈉上限' },
}

export function person(memberId: string, name: string | null, overrides: Partial<ResultPersonNutrition> = {}): ResultPersonNutrition {
  return {
    member_id: memberId, name, kinds: ['dining'], dining: true, n: '1', share: '0.25', meal_ratio: '0.4',
    intake: { energy_kcal: '760', protein_g: '26', carbohydrate_g: '100', sugars_g: '8', sodium_mg: '700' },
    known_partial: {}, bento: null, daily: DAILY, ...overrides,
  }
}

export function ingredient(name: string, overrides: Partial<ResultIngredient> = {}): ResultIngredient {
  return {
    food_id: `f-${name}`, name, weight_basis: 'raw', whole_meal_grams: '300', display_grams: '300',
    group: null, display_amount: '300', display_unit: 'g', display_text: null, ...overrides,
  }
}

export function priceItem(name: string, overrides: Partial<PriceItem> = {}): PriceItem {
  return {
    food_id: `f-${name}`, name, shortage_grams: '300', price_status: 'known', packs: 1, purchase_price: '45', cost: '27.5',
    missing_reason: null, retailer: 'demo', source_name: '示範超市', source: 'demo', product_name: name,
    product_url: 'https://example.org/p', package_description: '1 顆約 600 g', queried_at: '2026-11-14T02:00:00Z', valid_until: null,
    ...overrides,
  }
}

type Dish = MealResult['dishes'][number]

export function dish(name: string, overrides: Partial<Dish> = {}): Dish {
  return {
    standard_dish_id: `d-${name}`, dish_name: name, aliases: [], source: 'database', recipe_id: `r-${name}`, specified: false,
    cooking_method: null, steps: ['洗一洗', '下鍋'], ingredients: [ingredient('高麗菜')], sodium_mg_per_serving: '300', high_sodium: false,
    ...overrides,
  }
}

/** 可以採用、缺一項價格的結果（第 2 版）。 */
export function mealResult(overrides: Partial<MealResult> = {}): MealResult {
  return {
    meal_request_id: 'r1', request_status: 'result_ready', current_request_version: 3, result_version: 2, request_version: 3,
    derived_from_result_version: null, created_at: '2026-11-14T03:00:00Z', mode: 'database_menu', mode_label: '資料庫菜單',
    fallback_reason: null, fallback_message: null, sodium_adjustment: null,
    dishes: [
      dish('清蒸鱸魚', { cooking_method: '蒸', specified: true, ingredients: [ingredient('鱸魚片'), ingredient('鹽', { group: '調味', display_amount: '0.5', display_unit: '小匙' })] }),
      dish('蒜炒高麗菜'),
      dish('味噌豆腐湯', { high_sodium: true, sodium_mg_per_serving: '960' }),
    ],
    staple: {
      label: '糙米飯', tfda_name: '糙米飯', food_id: 'f-rice', review_status: null, unresolved_reason: null,
      whole_meal_grams: '640', display_amount: '640', display_unit: 'g', display_text: null, counts_for_price: false,
    },
    nutrition: {
      total_n: '4', target: { energy_kcal: '3000', protein_g: '90' }, energy_kcal: '3060', protein_g: '95',
      gap: { energy_kcal: '60', protein_g: '5' }, within_range: true,
      other_nutrients: { carbohydrate_g: '400', fat_g: '100', sugars_g: null, sodium_mg: '3000', dietary_fiber_g: null },
      missing_nutrients: {}, known_partial: { sugars_g: '12' }, missing_by_nutrient: { sugars_g: ['糙米飯'] },
      quantities_verified: true, suggestions: [],
      per_person: [
        person('m1', '阿明'),
        person('m2', '小惠', {
          kinds: ['bento'], dining: false, n: '0', share: '0', meal_ratio: '0',
          intake: { energy_kcal: null, protein_g: null }, known_partial: {},
          bento: { n: '1', share: '0.25', meal_ratio: '0.3', meal: 'lunch', intake: { energy_kcal: '590', protein_g: '15' }, known_partial: {} },
        }),
        person('m3', '小安', { intake: { energy_kcal: '900', protein_g: '26' } }),
        person('m4', '阿嬤', { intake: { energy_kcal: null, protein_g: '20' }, known_partial: { energy_kcal: '500' } }),
      ],
      daily_reference: { version: 'v1', source: { publisher: '國健署', title: '每日飲食指南', url: 'https://example.org/dri', verification: '' }, high_sodium_dish_mg: '800' },
    },
    prices: {
      status: 'computed', budget: '600', budget_status: 'PRICE_INCOMPLETE', price_complete: false, unquantified_items: [],
      purchase_total: null, cost_total: null, known_purchase_total: '380', known_cost_total: '212.4',
      items: [
        priceItem('高麗菜'),
        priceItem('鱸魚片', {
          price_status: 'unknown', packs: null, purchase_price: null, cost: null, missing_reason: 'NO_PRICE_DATA',
          source_name: null, retailer: null, source: null, product_url: null, package_description: null, queried_at: null,
        }),
      ],
    },
    knowledge: {
      status: 'available', retry_available: false,
      items: [
        {
          ingredient_id: 'i1', ingredient_name: '高麗菜', explanation: '高麗菜含有維生素 C。',
          citations: [{
            knowledge_id: 'k1', source_version: 'v3', ingredient_id: 'i1', excerpt: '', source_url: 'https://example.org/k1',
            rights: null, conditions: null, attribution: '資料來源：健康醫療網〈高麗菜怎麼吃〉',
          }],
        },
        {
          ingredient_id: 'i2', ingredient_name: null, explanation: '豆腐是蛋白質來源。',
          citations: [{ knowledge_id: 'k2', source_version: 'v1', ingredient_id: 'i2', excerpt: '', source_url: null, rights: null, conditions: null }],
        },
      ],
    },
    retries: { price: null, knowledge: null },
    previous_day_check: { status: 'ok', history_changed: false },
    previous_day: { previous_date: '2026-11-13', has_records: false, excluded_dishes: [], specified_repeats: [] },
    knowledge_retry: null, price_retry: null,
    eligibility: { adoptable: true, viewable: true, blocks: [], pending_decisions: [], price_exception_accepted: true, decisions: [] },
    ...overrides,
  }
}

export function mealRequest(overrides: Partial<MealRequest> = {}): MealRequest {
  return {
    id: 'r1', status: 'result_ready', version: 3, updated_at: '2026-11-14T03:00:00Z', complete: true, issues: [],
    draft: { meal_date: '2026-11-14', meal_type: 'dinner' },
    ...overrides,
  } as MealRequest
}
