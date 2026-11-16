import type { CatalogDish, CatalogFacet, IngredientRef, RecipeCatalog, RecipeSummary } from '../../src/api/types.ts'

/**
 * 合成的標準食材字典與食譜庫。數值只是「看起來合理」的示範值，不是任何真實來源的資料；
 * null 代表該營養素未知（海帶芽的膳食纖維、糙米飯的糖），用來看「未知」的畫面。
 */

export const NUTRIENT_KEYS = [
  'energy_kcal', 'protein_g', 'carbohydrate_g', 'fat_g', 'sugars_g', 'sodium_mg', 'dietary_fiber_g',
] as const
export type NutrientKey = typeof NUTRIENT_KEYS[number]

export type Food = {
  id: string
  name: string
  aliases: string[]
  /** 每 100 g；null＝未知 */
  per100: Record<NutrientKey, number | null>
  /** 假超市的一包；null＝查不到價格 */
  pack: { grams: number; price: number } | null
  seasoning: boolean
}

type FoodRow = [name: string, kcal: number, protein: number, carb: number, fat: number, sugar: number | null,
  sodium: number, fiber: number | null, packGrams: number | null, packPrice: number | null, aliases?: string[]]

const SEASONINGS = new Set(['醬油', '食鹽', '味噌', '香油', '沙拉油', '砂糖'])

const FOOD_ROWS: FoodRow[] = [
  ['雞胸肉', 117, 24, 0, 1.9, 0, 50, 0, 300, 119, ['雞胸']],
  ['雞腿肉', 157, 18.5, 0, 8.7, 0, 75, 0, 300, 109, ['去骨雞腿']],
  ['豬里肌', 187, 22, 0, 10.2, 0, 45, 0, 300, 135, ['里肌肉']],
  ['豬絞肉', 212, 19, 0, 14.5, 0, 60, 0, 300, 99, ['絞肉']],
  ['豬五花', 368, 14.5, 0, 34, 0, 40, 0, 300, 129, ['五花肉']],
  ['牛肉片', 250, 19, 0, 19, 0, 55, 0, 250, 189, ['牛肉']],
  ['牛腱', 123, 20.5, 0, 4, 0, 70, 0, 300, 219],
  ['羊肉片', 198, 18.8, 0, 13, 0, 65, 0, 250, 199, ['羊肉']],
  // 鱸魚片在假超市查不到價格：用來看「價格未知」與缺價重試
  ['鱸魚片', 97, 19.9, 0, 1.4, 0, 60, 0, null, null, ['鱸魚']],
  ['鮭魚', 221, 20, 0, 15.5, 0, 50, 0, 250, 239],
  ['白蝦', 100, 21.9, 1, 0.7, 0, 160, 0, 300, 179, ['蝦子']],
  ['蝦仁', 96, 20.5, 1, 0.6, 0, 180, 0, 250, 199],
  ['蛤蜊', 37, 7.6, 2.7, 0.5, 0, 450, 0, 600, 89, ['蛤蠣']],
  ['透抽', 73, 16, 1.6, 0.4, 0, 190, 0, 300, 169],
  ['雞蛋', 134, 12.5, 1.8, 8.8, 0.4, 135, 0, 600, 79, ['蛋']],
  ['板豆腐', 88, 8.5, 6, 3.4, 0.6, 2, 0.6, 400, 25, ['豆腐']],
  ['嫩豆腐', 51, 4.9, 2, 2.6, 0.5, 30, 0.8, 300, 20],
  ['豆干', 160, 17.4, 4.2, 8.6, 0.5, 250, 2.2, 300, 39, ['豆乾']],
  ['高麗菜', 23, 1.3, 4.8, 0.1, 2.9, 12, 1.1, 1000, 49, ['甘藍']],
  ['青江菜', 13, 1.3, 2.1, 0.1, 0.9, 40, 1.4, 250, 25],
  ['菠菜', 18, 2.2, 2.4, 0.3, 0.3, 43, 1.9, 250, 29],
  ['空心菜', 22, 1.4, 4, 0.3, 0.5, 52, 2.1, 250, 25],
  ['花椰菜', 28, 3.7, 4.4, 0.2, 1.5, 15, 3.1, 300, 45, ['青花菜']],
  ['紅蘿蔔', 37, 1, 8.5, 0.1, 5.5, 70, 2.6, 600, 35, ['胡蘿蔔']],
  ['洋蔥', 42, 1, 10, 0.1, 6.3, 1, 1.3, 600, 39],
  ['番茄', 19, 0.9, 4.1, 0.1, 2.6, 2, 1, 600, 59, ['蕃茄']],
  ['小黃瓜', 13, 0.9, 2.4, 0.2, 1.4, 5, 1.3, 300, 29],
  ['茄子', 25, 1.2, 5.3, 0.2, 2.6, 3, 2.2, 300, 35],
  ['青椒', 22, 0.8, 4.9, 0.3, 2.6, 6, 2.1, 300, 39],
  ['玉米', 107, 3.3, 22.8, 1.5, 4.8, 3, 4.7, 400, 45],
  ['馬鈴薯', 77, 2.6, 15.8, 0.2, 0.5, 5, 1.3, 600, 39, ['洋芋']],
  ['地瓜', 121, 1.3, 27.8, 0.2, 5.5, 44, 2.5, 600, 45, ['番薯']],
  ['南瓜', 74, 1.9, 17.3, 0.2, 5.1, 1, 2.5, 600, 49],
  ['白蘿蔔', 18, 0.5, 3.9, 0.1, 2.4, 40, 1.1, 800, 39, ['蘿蔔']],
  ['香菇', 39, 3, 7.6, 0.1, 1.7, 1, 3.8, 200, 59],
  ['金針菇', 37, 2.6, 7.2, 0.3, 1.1, 2, 2.3, 200, 20],
  // 膳食纖維未知：整餐的膳食纖維會變成「未知（已知 X，缺：海帶芽）」
  ['海帶芽', 45, 3, 9, 0.6, 0.5, 870, null, 100, 55],
  ['豆芽菜', 26, 2.5, 4.9, 0.2, 1.2, 8, 1.4, 300, 15],
  ['四季豆', 30, 1.7, 5.9, 0.2, 2.2, 6, 2, 300, 35],
  ['絲瓜', 19, 1, 4, 0.2, 1.8, 0, 1, 600, 45],
  ['冬瓜', 13, 0.5, 2.9, 0.1, 1.8, 2, 1.1, 800, 39],
  ['白飯', 183, 3.1, 41, 0.3, 0, 2, 0.6, 1000, 85, ['飯', '米飯']],
  // 糖未知（比照真實設定檔沒有 sugars 值）
  ['糙米飯', 112, 2.3, 23.5, 0.8, null, 1, 1.8, 1000, 99],
  ['白米', 354, 7, 77.8, 0.6, 0, 2, 0.7, 2000, 169],
  ['烏龍麵', 126, 2.6, 27, 0.4, 0.2, 80, 0.8, 600, 49],
  ['米粉', 109, 0.9, 25, 0.2, 0, 20, 1, 300, 45],
  ['義大利麵', 150, 5.5, 30, 0.9, 0.6, 1, 1.8, 500, 59],
  ['吐司', 290, 9.3, 51, 5.3, 6, 450, 2.2, 400, 45],
  ['燕麥', 389, 11.5, 66, 10, 1, 3, 8.5, 800, 99],
  ['牛奶', 63, 3, 4.8, 3.6, 4.8, 40, 0, 936, 92, ['鮮奶']],
  ['優格', 70, 3.5, 9, 2.5, 9, 45, 0, 500, 69],
  ['起司', 310, 18, 3, 25, 3, 900, 0, 200, 109, ['乳酪']],
  ['花生', 553, 28, 21, 43, 4, 5, 8, 200, 69],
  ['紅豆', 328, 21, 61, 0.6, 2, 2, 18, 500, 89],
  ['香蕉', 85, 1.5, 22, 0.1, 15, 0, 1.6, 600, 39],
  ['蘋果', 51, 0.2, 13.9, 0.1, 11, 2, 1.3, 800, 99],
  ['泡菜', 30, 2, 5, 0.5, 2, 700, 2, 400, 89],
  ['咖哩塊', 510, 7, 40, 35, 10, 4200, 3, 200, 75],
  ['蒜頭', 122, 6.7, 26, 0.2, 2, 5, 4.2, 200, 39, ['大蒜']],
  ['薑', 20, 0.7, 4.3, 0.2, 1, 6, 2, 150, 25, ['老薑']],
  ['青蔥', 26, 1.5, 5.2, 0.3, 2.6, 4, 2.6, 200, 29, ['蔥']],
  ['醬油', 88, 8, 13, 0, 9, 5500, 0, 500, 65],
  ['食鹽', 0, 0, 0, 0, 0, 39000, 0, 1000, 25, ['鹽']],
  ['味噌', 199, 12, 26, 5, 14, 4300, 5, 500, 79],
  ['香油', 884, 0, 0, 100, 0, 0, 0, 200, 119, ['麻油']],
  ['沙拉油', 884, 0, 0, 100, 0, 0, 0, 1000, 99, ['食用油']],
  ['砂糖', 387, 0, 100, 0, 100, 1, 0, 1000, 42, ['糖']],
]

export const FOODS: Food[] = FOOD_ROWS.map((row, index) => {
  const [name, kcal, protein, carb, fat, sugar, sodium, fiber, packGrams, packPrice, aliases] = row
  return {
    id: `FOOD9${String(index + 1).padStart(5, '0')}`,
    name,
    aliases: aliases ?? [],
    per100: {
      energy_kcal: kcal, protein_g: protein, carbohydrate_g: carb, fat_g: fat, sugars_g: sugar, sodium_mg: sodium,
      dietary_fiber_g: fiber,
    },
    pack: packGrams !== null && packPrice !== null ? { grams: packGrams, price: packPrice } : null,
    seasoning: SEASONINGS.has(name),
  }
})

const FOOD_BY_ID = new Map(FOODS.map((f) => [f.id, f]))
const FOOD_BY_NAME = new Map(FOODS.map((f) => [f.name, f]))

export function food(id: string): Food | undefined {
  return FOOD_BY_ID.get(id)
}
export function foodByName(name: string): Food {
  const found = FOOD_BY_NAME.get(name)
  if (!found) throw new Error(`假食材字典沒有「${name}」`)
  return found
}
export function foodId(name: string): string {
  return foodByName(name).id
}
export function describeFoods(ids: string[]): IngredientRef[] {
  return ids.map((id) => ({ ingredient_id: id, name: food(id)?.name ?? null }))
}
export function unknownFoodIds(ids: string[]): string[] {
  return ids.filter((id) => !FOOD_BY_ID.has(id))
}

/** 與後端 ingredients.search 相同的排序：完全相同 → 開頭相同 → 其他；同級比名稱長度。 */
export function searchFoods(query: string, limit: number): IngredientRef[] {
  const q = query.trim()
  const rows = FOODS.filter((f) => !q || f.name.includes(q) || f.aliases.some((a) => a.includes(q)))
  const rank = (f: Food) => {
    if (f.name === q || f.aliases.includes(q)) return 0
    if (f.name.startsWith(q) || f.aliases.some((a) => a.startsWith(q))) return 1
    return 2
  }
  const sorted = q
    ? [...rows].sort((a, b) => rank(a) - rank(b) || a.name.length - b.name.length || a.name.localeCompare(b.name, 'zh-Hant'))
    : [...rows].sort((a, b) => a.name.localeCompare(b.name, 'zh-Hant'))
  return sorted.slice(0, limit).map((f) => ({ ingredient_id: f.id, name: f.name }))
}

/** 同義食材：對 A 過敏或禁吃時一併排除 B（後端的 restriction_expansion）。 */
const SYNONYM_PAIRS: [string, string, 'synonym' | 'broader'][] = [
  ['白蝦', '蝦仁', 'broader'],
  ['板豆腐', '嫩豆腐', 'synonym'],
]
export function expansions(id: string): { food_id: string; relation: 'synonym' | 'broader' }[] {
  const name = food(id)?.name
  const out: { food_id: string; relation: 'synonym' | 'broader' }[] = []
  for (const [a, b, relation] of SYNONYM_PAIRS) {
    if (a === name) out.push({ food_id: foodId(b), relation })
    if (b === name) out.push({ food_id: foodId(a), relation })
  }
  return out
}

// ---- 食譜 ----

export const MAIN = '主菜'
export const SOUP = '湯'
export const STAPLE = '主食'
export const DESSERT = '點心'

export type Recipe = {
  recipe_id: string
  standard_dish_id: string
  name: string
  aliases: string[]
  category: string
  cuisine: string
  meal_type: string
  method: string
  /** 每 1 份（1n）的基準用量（克） */
  lines: { food: Food; grams: number }[]
  steps: string[]
}

type RecipeRow = [name: string, category: string, cuisine: string, method: string, lines: [string, number][],
  options?: { aliases?: string[]; breakfast?: boolean }]

const RECIPE_ROWS: RecipeRow[] = [
  // 主菜・海鮮
  ['清蒸鱸魚', MAIN, '台式', '蒸', [['鱸魚片', 120], ['薑', 5], ['青蔥', 8], ['醬油', 6]]],
  ['蒜蓉蒸蝦', MAIN, '台式', '蒸', [['白蝦', 110], ['蒜頭', 8], ['沙拉油', 3], ['食鹽', 0.5]]],
  ['香煎鮭魚', MAIN, '日式', '煎', [['鮭魚', 100], ['食鹽', 0.6], ['沙拉油', 3]]],
  ['蛤蜊絲瓜', MAIN, '台式', '炒', [['蛤蜊', 80], ['絲瓜', 150], ['薑', 3], ['食鹽', 0.5]]],
  ['三杯透抽', MAIN, '台式', '燒', [['透抽', 110], ['蒜頭', 6], ['薑', 5], ['醬油', 8], ['香油', 4], ['砂糖', 2]]],
  // 主菜・蔬菜
  ['蒜炒高麗菜', MAIN, '台式', '炒', [['高麗菜', 150], ['蒜頭', 5], ['沙拉油', 4], ['食鹽', 0.8]], { aliases: ['炒高麗菜'] }],
  ['清炒青江菜', MAIN, '台式', '炒', [['青江菜', 150], ['蒜頭', 4], ['沙拉油', 4], ['食鹽', 0.7]]],
  ['蒜香菠菜', MAIN, '台式', '炒', [['菠菜', 140], ['蒜頭', 5], ['沙拉油', 4], ['食鹽', 0.7]]],
  ['蒜炒空心菜', MAIN, '台式', '炒', [['空心菜', 150], ['蒜頭', 5], ['沙拉油', 4], ['食鹽', 0.7]]],
  ['清燙花椰菜', MAIN, '西式', '燙', [['花椰菜', 140], ['食鹽', 0.6], ['香油', 2]]],
  ['涼拌小黃瓜', MAIN, '台式', '涼拌', [['小黃瓜', 120], ['蒜頭', 4], ['香油', 2], ['食鹽', 0.6], ['砂糖', 1.5]]],
  ['魚香茄子', MAIN, '中式', '燒', [['茄子', 130], ['豬絞肉', 30], ['蒜頭', 5], ['醬油', 6], ['砂糖', 2], ['沙拉油', 5]]],
  ['乾煸四季豆', MAIN, '中式', '炒', [['四季豆', 130], ['豬絞肉', 25], ['蒜頭', 5], ['醬油', 5], ['沙拉油', 5]]],
  // 主菜・肉
  ['蔥油雞腿', MAIN, '台式', '蒸', [['雞腿肉', 130], ['青蔥', 10], ['薑', 4], ['食鹽', 0.8], ['香油', 3]]],
  ['宮保雞丁', MAIN, '中式', '炒', [['雞胸肉', 110], ['花生', 12], ['青蔥', 8], ['蒜頭', 4], ['醬油', 7], ['砂糖', 2], ['沙拉油', 5]]],
  ['日式咖哩雞', MAIN, '日式', '燉', [['雞腿肉', 100], ['馬鈴薯', 60], ['紅蘿蔔', 40], ['洋蔥', 40], ['咖哩塊', 18]], { aliases: ['咖哩雞'] }],
  ['蒜泥白肉', MAIN, '台式', '燙', [['豬五花', 90], ['蒜頭', 6], ['醬油', 8]]],
  ['洋蔥炒豬里肌', MAIN, '台式', '炒', [['豬里肌', 110], ['洋蔥', 60], ['醬油', 6], ['沙拉油', 4]]],
  ['青椒牛肉', MAIN, '中式', '炒', [['牛肉片', 100], ['青椒', 70], ['蒜頭', 4], ['醬油', 6], ['沙拉油', 4]]],
  ['番茄燉牛腱', MAIN, '西式', '燉', [['牛腱', 120], ['番茄', 100], ['洋蔥', 50], ['紅蘿蔔', 40], ['食鹽', 0.8]]],
  ['蔥爆羊肉', MAIN, '台式', '炒', [['羊肉片', 100], ['青蔥', 30], ['蒜頭', 5], ['醬油', 7], ['沙拉油', 5]]],
  // 主菜・蛋與豆腐
  ['番茄炒蛋', MAIN, '台式', '炒', [['雞蛋', 80], ['番茄', 100], ['青蔥', 5], ['食鹽', 0.6], ['砂糖', 2], ['沙拉油', 4]], { aliases: ['蕃茄炒蛋'] }],
  ['麻婆豆腐', MAIN, '中式', '燒', [['板豆腐', 150], ['豬絞肉', 30], ['蒜頭', 4], ['醬油', 7], ['沙拉油', 4]]],
  ['紅燒豆腐', MAIN, '台式', '燒', [['板豆腐', 160], ['香菇', 20], ['青蔥', 6], ['醬油', 8], ['砂糖', 2], ['沙拉油', 4]]],
  ['青椒炒豆干', MAIN, '台式', '炒', [['青椒', 80], ['豆干', 60], ['醬油', 5], ['沙拉油', 4]]],
  ['玉子燒', MAIN, '日式', '煎', [['雞蛋', 90], ['砂糖', 3], ['醬油', 2], ['沙拉油', 3]], { breakfast: true }],
  // 湯
  // 味噌放得多：放大成整餐後每人份的鈉會超過 1000 mg，用來看「高鈉」提醒
  ['味噌豆腐湯', SOUP, '日式', '煮', [['嫩豆腐', 80], ['海帶芽', 3], ['味噌', 15], ['青蔥', 4]], { aliases: ['味噌湯'] }],
  ['蛤蜊冬瓜湯', SOUP, '台式', '煮', [['蛤蜊', 60], ['冬瓜', 120], ['薑', 3], ['食鹽', 0.5]]],
  ['番茄蛋花湯', SOUP, '台式', '煮', [['番茄', 80], ['雞蛋', 30], ['青蔥', 4], ['食鹽', 0.7]]],
  ['香菇雞湯', SOUP, '台式', '燉', [['雞腿肉', 70], ['香菇', 20], ['薑', 3], ['食鹽', 0.7]]],
  ['玉米蘿蔔湯', SOUP, '台式', '燉', [['白蘿蔔', 100], ['玉米', 60], ['豬里肌', 40], ['食鹽', 0.7]]],
  ['南瓜濃湯', SOUP, '西式', '煮', [['南瓜', 150], ['洋蔥', 30], ['牛奶', 80], ['食鹽', 0.5]]],
  ['泡菜豆腐湯', SOUP, '韓式', '煮', [['泡菜', 60], ['嫩豆腐', 80], ['豬五花', 30], ['青蔥', 5]]],
  ['金針菇豆芽湯', SOUP, '台式', '煮', [['金針菇', 50], ['豆芽菜', 60], ['青蔥', 4], ['食鹽', 0.6]]],
  // 主食
  ['地瓜糙米飯', STAPLE, '台式', '煮', [['糙米飯', 150], ['地瓜', 60]]],
  ['番茄蛋炒飯', STAPLE, '台式', '炒', [['白飯', 180], ['雞蛋', 50], ['番茄', 50], ['青蔥', 5], ['醬油', 5], ['沙拉油', 5]]],
  ['鮭魚茶泡飯', STAPLE, '日式', '煮', [['白飯', 170], ['鮭魚', 50], ['海帶芽', 2]]],
  ['香菇雞肉粥', STAPLE, '台式', '煮', [['白米', 50], ['雞胸肉', 40], ['香菇', 15], ['薑', 2], ['食鹽', 0.6]], { breakfast: true }],
  ['燕麥牛奶粥', STAPLE, '西式', '煮', [['燕麥', 40], ['牛奶', 200], ['香蕉', 50]], { breakfast: true }],
  ['牛肉烏龍麵', STAPLE, '日式', '煮', [['烏龍麵', 200], ['牛肉片', 60], ['洋蔥', 40], ['青蔥', 5], ['醬油', 10]]],
  ['炒米粉', STAPLE, '台式', '炒', [['米粉', 180], ['豬里肌', 40], ['高麗菜', 60], ['紅蘿蔔', 20], ['香菇', 10], ['醬油', 8], ['沙拉油', 5]]],
  ['番茄肉醬義大利麵', STAPLE, '西式', '煮', [['義大利麵', 200], ['豬絞肉', 60], ['番茄', 100], ['洋蔥', 40], ['食鹽', 0.8]], { aliases: ['肉醬麵'] }],
  ['起司蛋吐司', STAPLE, '西式', '煎', [['吐司', 70], ['雞蛋', 50], ['起司', 20]], { breakfast: true }],
  // 點心
  ['紅豆湯', DESSERT, '台式', '煮', [['紅豆', 40], ['砂糖', 15]]],
  ['水果優格', DESSERT, '西式', '涼拌', [['優格', 120], ['蘋果', 60], ['香蕉', 40]]],
  ['烤地瓜', DESSERT, '台式', '烤', [['地瓜', 150]]],
  ['香蕉牛奶', DESSERT, '西式', '打', [['香蕉', 100], ['牛奶', 200]]],
]

function fakeSteps(name: string, method: string, lines: { food: Food }[]): string[] {
  const main = lines.filter((l) => !l.food.seasoning).map((l) => l.food.name)
  const seasoning = lines.filter((l) => l.food.seasoning).map((l) => l.food.name)
  return [
    `（示範步驟）把${main.join('、')}洗淨，切成適口大小。`,
    `用「${method}」的方式處理到全熟。`,
    seasoning.length ? `加入${seasoning.join('、')}調味，試過味道後起鍋，${name}完成。` : `盛盤，${name}完成。`,
  ]
}

export const RECIPES: Recipe[] = RECIPE_ROWS.map((row, index) => {
  const [name, category, cuisine, method, rawLines, options] = row
  const lines = rawLines.map(([foodName, grams]) => ({ food: foodByName(foodName), grams }))
  const serial = String(index + 1).padStart(3, '0')
  return {
    recipe_id: `RCP-FAKE-${serial}`,
    standard_dish_id: `DISH-FAKE-${serial}`,
    name,
    aliases: options?.aliases ?? [],
    category,
    cuisine,
    meal_type: options?.breakfast ? '早餐' : '午晚餐',
    method,
    lines,
    steps: fakeSteps(name, method, lines),
  }
})

const RECIPE_BY_ID = new Map(RECIPES.map((r) => [r.recipe_id, r]))

export function recipe(id: string): Recipe | undefined {
  return RECIPE_BY_ID.get(id)
}

/** 菜名比對：去掉空白後完全相同（含別名）。 */
export function normalizeName(name: string): string {
  return name.replace(/\s+/g, '').toLowerCase()
}
export function recipeByName(name: string): Recipe | undefined {
  const key = normalizeName(name)
  return RECIPES.find((r) => [r.name, ...r.aliases].some((n) => normalizeName(n) === key))
}

export function recipeSummary(r: Recipe, specified: boolean): RecipeSummary {
  return {
    recipe_id: r.recipe_id, recipe_name: r.name, standard_dish_id: r.standard_dish_id, category: r.category,
    aliases: r.aliases, food_ids: r.lines.map((l) => l.food.id), specified,
  }
}

export const RECIPE_SOURCE = { source_name: '（假資料）示範食譜庫', source_url: 'https://example.com/fake-recipes' }

// ---- 食譜庫選菜的分類（規則照搬 backend/app/services/recipe_catalog.py） ----

const STAPLE_KINDS: [string, string[]][] = [['粥', ['粥']], ['麵', ['麵', '米粉', '烏龍']], ['飯', ['飯', '丼']]]
const OTHER_STAPLE = '其他主食'
const MAIN_INGREDIENT_RULES: [string, string[]][] = [
  ['豆腐', ['豆腐', '豆干', '豆乾', '豆皮', '百頁']],
  ['蛋', ['蛋']],
  ['雞肉', ['雞']],
  ['牛肉', ['牛']],
  ['羊肉', ['羊']],
  ['海鮮', ['魚', '蝦', '蟹', '蛤', '蚵', '透抽', '小卷', '魷', '花枝', '章魚', '干貝', '鮭', '鯛', '鱈', '鮪', '鯖', '鱸', '貝', '甜不辣']],
  ['豬肉', ['豬', '五花', '排骨', '絞肉', '培根', '火腿', '里肌', '梅花', '香腸', '肉絲', '肉片', '腰內肉', '肉燥', '貢丸', '松阪']],
]
const MEATS = new Set(['雞肉', '牛肉', '羊肉', '海鮮', '豬肉'])
export const VEGETABLE = '蔬菜（無肉）'
const NOT_MAIN_SUFFIXES = ['粉', '醬', '油', '露', '汁', '精', '水', '湯', '乳']
const NOT_MAIN_WORDS = ['高湯', '蝦米', '蝦皮', '柴魚', '昆布', '牛奶', '牛蒡', '牛番茄']
const PREVIEW_INGREDIENTS = 6
const CATEGORY_ORDER = [MAIN, SOUP, STAPLE, DESSERT]

export function stapleKind(name: string, category: string): string | null {
  if (category !== STAPLE) return null
  return STAPLE_KINDS.find(([, words]) => words.some((w) => name.includes(w)))?.[0] ?? OTHER_STAPLE
}

export function mainIngredients(names: string[]): string[] {
  const found = new Set<string>()
  for (const name of names) {
    if (NOT_MAIN_SUFFIXES.some((s) => name.endsWith(s)) || NOT_MAIN_WORDS.some((w) => name.includes(w))) continue
    const tag = MAIN_INGREDIENT_RULES.find(([, words]) => words.some((w) => name.includes(w)))?.[0]
    if (tag) found.add(tag)
  }
  if (![...found].some((tag) => MEATS.has(tag))) found.add(VEGETABLE)
  const order = [...MAIN_INGREDIENT_RULES.map(([tag]) => tag), VEGETABLE]
  return [...found].sort((a, b) => order.indexOf(a) - order.indexOf(b))
}

function facet(values: (string | null)[]): CatalogFacet[] {
  const counts = new Map<string, number>()
  for (const v of values) if (v) counts.set(v, (counts.get(v) ?? 0) + 1)
  return [...counts.entries()].sort((a, b) => b[1] - a[1]).map(([value, count]) => ({ value, count }))
}

const rankOf = (category: string) => {
  const index = CATEGORY_ORDER.indexOf(category)
  return index === -1 ? CATEGORY_ORDER.length : index
}

/** 含本餐過敏／禁吃食材的菜照列但標出衝突食材（前端不能勾選）。 */
export function recipeCatalog(excludedFoodIds: Set<string>): RecipeCatalog {
  const dishes: CatalogDish[] = RECIPES.map((r) => {
    const names = r.lines.map((l) => l.food.name)
    return {
      dish_name: r.name,
      standard_dish_id: r.standard_dish_id,
      cuisine: r.cuisine,
      category: r.category,
      staple_kind: stapleKind(r.name, r.category),
      meal_type: r.meal_type,
      main_ingredients: mainIngredients(names),
      ingredients: [...new Set(names)].slice(0, PREVIEW_INGREDIENTS),
      conflicts: [...new Set(r.lines.filter((l) => excludedFoodIds.has(l.food.id)).map((l) => l.food.name))].sort(),
    }
  }).sort((a, b) => rankOf(a.category) - rankOf(b.category) || a.dish_name.localeCompare(b.dish_name, 'zh-Hant'))
  return {
    dishes,
    facets: {
      main_ingredient: facet(dishes.flatMap((d) => d.main_ingredients)),
      cuisine: facet(dishes.map((d) => d.cuisine)),
      category: facet(dishes.map((d) => d.category)).sort((a, b) => rankOf(a.value) - rankOf(b.value)),
      staple_kind: facet(dishes.map((d) => d.staple_kind)),
    },
  }
}

// ---- 食材小知識（合成內容；出處一律同時顯示） ----

export type KnowledgeEntry = { foodName: string; fact: string; excerpt: string; title: string; published_at: string; slug: string }

export const KNOWLEDGE_SOURCE = '（假資料）示範健康網'

export const KNOWLEDGE: KnowledgeEntry[] = [
  { foodName: '高麗菜', slug: 'cabbage', title: '高麗菜的營養', published_at: '2026-01-15',
    fact: '高麗菜含有膳食纖維與維生素 C，快炒時間短一點比較能保留口感。',
    excerpt: '高麗菜含膳食纖維與維生素 C。' },
  { foodName: '鱸魚片', slug: 'sea-bass', title: '認識鱸魚', published_at: '2026-02-03',
    fact: '鱸魚的蛋白質含量高、脂肪較少，清蒸是常見的料理方式。',
    excerpt: '鱸魚蛋白質含量高、脂肪較少。' },
  { foodName: '嫩豆腐', slug: 'tofu', title: '豆腐小百科', published_at: '2026-03-20',
    fact: '豆腐是黃豆製品，提供植物性蛋白質，口感軟嫩容易入口。',
    excerpt: '豆腐提供植物性蛋白質。' },
  { foodName: '板豆腐', slug: 'firm-tofu', title: '豆腐小百科', published_at: '2026-03-20',
    fact: '板豆腐水分較少、質地紮實，適合紅燒或煎。',
    excerpt: '板豆腐質地紮實，適合紅燒或煎。' },
  { foodName: '番茄', slug: 'tomato', title: '番茄怎麼吃', published_at: '2026-04-08',
    fact: '番茄含有茄紅素，加一點油一起煮比較容易被吸收。',
    excerpt: '番茄含茄紅素，與油脂一起烹調較易吸收。' },
  { foodName: '雞蛋', slug: 'egg', title: '雞蛋的營養', published_at: '2026-04-22',
    fact: '雞蛋提供完整的蛋白質，是很方便的蛋白質來源。',
    excerpt: '雞蛋提供完整蛋白質。' },
  { foodName: '地瓜', slug: 'sweet-potato', title: '地瓜與膳食纖維', published_at: '2026-05-11',
    fact: '地瓜含有膳食纖維，連皮洗淨一起吃可以吃到更多纖維。',
    excerpt: '地瓜含膳食纖維。' },
  { foodName: '鮭魚', slug: 'salmon', title: '鮭魚的脂肪酸', published_at: '2026-05-30',
    fact: '鮭魚含有 Omega-3 脂肪酸，煎或烤都很合適。',
    excerpt: '鮭魚含 Omega-3 脂肪酸。' },
  { foodName: '菠菜', slug: 'spinach', title: '深綠色蔬菜', published_at: '2026-06-14',
    fact: '菠菜屬於深綠色蔬菜，含有葉酸與鐵。',
    excerpt: '菠菜含葉酸與鐵。' },
  { foodName: '香菇', slug: 'shiitake', title: '菇類入菜', published_at: '2026-07-02',
    fact: '香菇有特殊的香氣，含有膳食纖維，燉湯可以增加鮮味。',
    excerpt: '香菇含膳食纖維，可增加湯頭鮮味。' },
  { foodName: '雞腿肉', slug: 'chicken-thigh', title: '雞肉部位比較', published_at: '2026-07-19',
    fact: '雞腿肉的脂肪比雞胸多一些，口感比較嫩。',
    excerpt: '雞腿肉脂肪略高於雞胸，口感較嫩。' },
]

export function knowledgeFor(foodIds: string[]): KnowledgeEntry[] {
  const names = new Set(foodIds.map((id) => food(id)?.name))
  return KNOWLEDGE.filter((k) => names.has(k.foodName))
}
export const knowledgeUrl = (k: KnowledgeEntry) => `https://example.com/fake-knowledge/${k.slug}`
