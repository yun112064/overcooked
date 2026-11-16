/**
 * 食譜書的流程與清單規則（從舊前端的 RecipePickerPage、RecipeCatalogPicker 搬過來，規則不變）。
 * 這裡只有純函式，畫面在 RecipeBookScene 與各個 *Pages 元件。
 */
import type {
  CatalogDish, CatalogFacet, MealRequest, MealRequestDraft, MealType, Member, Participant, StapleOptions,
} from '../../api/types'
import { localDate } from '../../session/queries'

/** 0 誰要吃 → 1 挑菜 → 2 結果；和舊前端的三步相同。 */
export type Step = 0 | 1 | 2

export const STEPS: readonly { short: string; title: string }[] = [
  { short: '誰要吃', title: '用餐人數與便當' },
  { short: '挑菜', title: '挑選菜色' },
  { short: '結果', title: '結果' },
]

export const MEALS: readonly MealType[] = ['breakfast', 'lunch', 'dinner']

/** 每位成員這一餐：一起吃、帶便當（可以兩個都勾）。 */
export type Picks = Record<string, { dining: boolean; bento: boolean }>

/** 依現在時間預設餐別（可改）。 */
export function mealByClock(now: Date = new Date()): MealType {
  const hour = now.getHours()
  return hour < 10 ? 'breakfast' : hour < 15 ? 'lunch' : 'dinner'
}

/** 進來時每位成員的勾選：照已存的單；沒有就都不勾。成員清單裡沒有的人不帶進來。 */
export function initialPicks(members: readonly Member[], participants: readonly Participant[]): Picks {
  const saved = new Map(participants.map((p) => [p.member_id, p]))
  return Object.fromEntries(members.map((m) => [m.id, {
    dining: saved.get(m.id)?.dining ?? false,
    bento: saved.get(m.id)?.bento ?? false,
  }]))
}

/**
 * 一張單上存的選擇（餐別、誰要吃、挑了哪些菜）→ 畫面上的狀態。
 * 進來時用它；儲存遇到版本衝突、重新讀取之後也用它把畫面整個換成伺服器那一份——
 * 只換版本號的話，畫面上還留著舊的勾選，下一次儲存就會把別處剛存的內容蓋掉。
 */
export function savedSelections(
  members: readonly Member[],
  draft: Pick<MealRequestDraft, 'meal_type' | 'participants' | 'specified_dishes'> | null | undefined,
  now: Date = new Date(),
): { mealType: MealType; picks: Picks; picked: string[] } {
  return {
    mealType: draft?.meal_type ?? mealByClock(now),
    picks: initialPicks(members, draft?.participants ?? []),
    picked: [...(draft?.specified_dishes ?? [])],
  }
}

export function countPicks(picks: Picks): { dining: number; bento: number } {
  const all = Object.values(picks)
  return { dining: all.filter((p) => p.dining).length, bento: all.filter((p) => p.bento).length }
}

/**
 * 食譜庫選菜送給後端的內容：不問預算、口味與已有食材，所以這三項固定是空的。
 * 日期、主食、道數、已挑的菜沿用已存的那一份（base）；便當一律算午餐。
 */
export function pickerDraft(
  mealType: MealType, picks: Picks, base?: MealRequestDraft, today: string = localDate(),
): MealRequestDraft {
  const participants: Participant[] = Object.entries(picks)
    .filter(([, p]) => p.dining || p.bento)
    .map(([member_id, p]) => ({ member_id, dining: p.dining, bento: p.bento, bento_meal: 'lunch' }))
  return {
    mode: 'picker',
    meal_date: base?.meal_date ?? today,
    meal_type: mealType,
    budget: null,
    staple: base?.staple ?? null,
    dish_count: base?.dish_count ?? null,
    taste_preferences: '',
    specified_dishes: base?.specified_dishes ?? [],
    participants,
    available_ingredient_ids: [],
  }
}

/**
 * 進來時停在哪一步：新的單從第一步開始；已經送出過（不是草稿）直接看結果；
 * 草稿照上一頁指定的步驟（第一次儲存後換網址時會帶 step: 1），沒有指定就從頭。
 */
export function initialStep(initial: Pick<MealRequest, 'status'> | null, routeState: unknown): Step {
  if (!initial) return 0
  if (initial.status !== 'draft') return 2
  const step = (routeState as { step?: unknown } | null)?.step
  return step === 1 || step === 2 ? step : 0
}

// ---- 食譜清單 ----

export const PAGE_SIZE = 10
export const MAX_DISHES = 10

export type FacetKey = 'main_ingredient' | 'cuisine' | 'category' | 'staple_kind'
export type Filters = Record<FacetKey, string[]>

export const FACETS: readonly { key: FacetKey; label: string }[] = [
  { key: 'main_ingredient', label: '主要食材' },
  { key: 'cuisine', label: '菜系' },
  { key: 'category', label: '類型' },
  { key: 'staple_kind', label: '米飯麵' },
]

export const NO_FILTERS: Filters = { main_ingredient: [], cuisine: [], category: [], staple_kind: [] }

/** 一道菜在某個分類的值（主要食材可以有好幾個）。 */
export function valuesOf(dish: CatalogDish, key: FacetKey): string[] {
  if (key === 'main_ingredient') return dish.main_ingredients
  const value = dish[key]
  return value ? [value] : []
}

/** 同一個分類裡符合任一個就算；不同分類要同時符合。關鍵字比對菜名或食材。 */
export function filterDishes(dishes: readonly CatalogDish[], filters: Filters, keyword: string): CatalogDish[] {
  const word = keyword.trim()
  return dishes.filter((dish) =>
    FACETS.every(({ key }) => filters[key].length === 0 || valuesOf(dish, key).some((v) => filters[key].includes(v)))
    && (!word || dish.dish_name.includes(word) || dish.ingredients.some((i) => i.includes(word))))
}

export function toggleValue(list: readonly string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value]
}

export function filterCount(filters: Filters): number {
  return FACETS.reduce((sum, { key }) => sum + filters[key].length, 0)
}

/** 已挑的菜裡，食譜庫真的有的那些（之前手打、食譜庫沒有的菜名不帶進來；確定後會被所選的菜取代）。 */
export function chosenNames(picked: readonly string[], byName: ReadonlyMap<string, CatalogDish>): string[] {
  return picked.filter((name) => byName.has(name))
}

/** 這道菜的勾選框能不能動：含禁忌的不能加、滿了不能再加；已經在托盤裡的一定可以拿出來。 */
export function canToggle(dish: CatalogDish, checked: boolean, chosenCount: number): boolean {
  if (checked) return true
  return dish.conflicts.length === 0 && chosenCount < MAX_DISHES
}

/** 菜名下面那一行標籤：菜系・類型・米飯麵・主要食材，早餐的菜多標一個「早餐」。 */
export function dishTags(dish: CatalogDish): string[] {
  return [dish.cuisine, dish.category, dish.staple_kind, ...dish.main_ingredients, dish.meal_type === '早餐' ? '早餐' : null]
    .filter((tag): tag is string => !!tag)
}

export function pageCount(total: number): number {
  return Math.max(1, Math.ceil(total / PAGE_SIZE))
}

/** 篩選後頁數變少時，把頁碼夾回最後一頁。 */
export function clampPage(page: number, total: number): number {
  return Math.min(Math.max(0, page), pageCount(total) - 1)
}

export function pageOf<T>(items: readonly T[], page: number): T[] {
  const start = clampPage(page, items.length) * PAGE_SIZE
  return items.slice(start, start + PAGE_SIZE)
}

/** 書籤最多放四個「類型」（第一個書籤是全部、最後一個是更多分類）。 */
export const BOOKMARK_SLOTS = 4
export function bookmarkValues(category: readonly CatalogFacet[]): string[] {
  return category.slice(0, BOOKMARK_SLOTS).map((f) => f.value)
}

/** 有沒有挑到主食類的菜（挑了就不另配主食）。 */
export function hasStapleDish(names: readonly string[], byName: ReadonlyMap<string, CatalogDish>): boolean {
  return names.some((name) => byName.get(name)?.category === '主食')
}

/** 沒挑飯麵類時配清單第一個主食（白飯）；挑了就填「不需要主食」。 */
export function stapleFor(hasStaple: boolean, staples: StapleOptions): string {
  return hasStaple ? staples.none_value : (staples.options[0]?.label ?? staples.none_value)
}
