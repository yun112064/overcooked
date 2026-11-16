import type {
  DailyReference, MealRequest, MealRequestDraft, MealType, NutritionPreview, PersonNutrition, PersonPortion,
  ResultPersonNutrition, ResultPersonPortion,
} from '../../src/api/types.ts'
import { NUTRIENT_KEYS, foodByName } from './catalog.ts'
import type { Food, NutrientKey } from './catalog.ts'
import { ageOn, num, numOrNull, ratio } from './core.ts'
import type { MemberRow } from './core.ts'

/**
 * 假的營養規則：每日目標、整餐用量與每人份量。
 * 只求數字合理、結果固定，不是 T03／T12 的真實公式；判定方式（熱量 ≤110%、蛋白質 ≥90%）與真後端相同。
 */

export const RULE_VERSION = 'fake-t03-0.1'
export const PORTIONS = ['0.7', '0.85', '1', '1.15', '1.3']
export const MEAL_RATIO: Record<MealType, number> = { breakfast: 0.3, lunch: 0.3, dinner: 0.4 }
const PAL = { sedentary: 1.3, low_active: 1.5, active: 1.7, highly_active: 1.9 }
const PROTEIN_PER_KG = { general: 1.1, maintain: 1.1, muscle_gain: 1.6, fat_loss: 1.4 }
const ENERGY_SHIFT = { general: 0, maintain: 0, muscle_gain: 300, fat_loss: -500 }
/** 未滿 19 歲用年齡區間表（熱量 kcal、蛋白質 g） */
const MINOR_BANDS: [maxAge: number, energy: number, protein: number][] = [
  [0, 700, 15], [3, 1250, 20], [6, 1650, 30], [9, 1950, 40], [12, 2200, 55], [15, 2500, 65], [18, 2650, 70],
]
export const ADULT_AGE = 19
export const GRAMS_PER_N_STAPLE = 160

export const REASON_MESSAGES: Record<string, string> = {
  INVALID_PORTION: '食量只能選 0.7、0.85、1、1.15 或 1.3。',
  GOAL_NOT_ALLOWED_FOR_AGE: '未滿 19 歲只能選擇一般餐食，不提供維持／增肌／減脂目標。',
  DAILY_ENERGY_BELOW_MINIMUM: '以目前體重與活動量設定減脂，每日熱量會低於 1200 大卡，請改選其他目標。',
  INVALID_WEIGHT: '體重必須大於 0。',
}

export class TargetError extends Error {
  reason: string
  field: string
  constructor(reason: string, field: string) {
    super(REASON_MESSAGES[reason] ?? '成員資料無法計算營養目標，請檢查輸入。')
    this.reason = reason
    this.field = field
  }
}

type TargetInput = Pick<MemberRow, 'birth_date' | 'equation_sex' | 'weight_kg' | 'activity_category' | 'goal'>

/** 每日目標（未乘餐別比例與食量 n）。算不出來丟 TargetError，不回 0。 */
export function dailyTarget(member: TargetInput, on: string): { energy: number; protein: number } {
  const age = ageOn(member.birth_date, on)
  const weight = Number(member.weight_kg)
  if (!(weight > 0)) throw new TargetError('INVALID_WEIGHT', 'weight_kg')
  if (age < ADULT_AGE) {
    if (member.goal !== 'general') throw new TargetError('GOAL_NOT_ALLOWED_FOR_AGE', 'goal')
    const band = MINOR_BANDS.find(([max]) => age <= max) ?? MINOR_BANDS[MINOR_BANDS.length - 1]
    return { energy: Math.round(band[1] * (member.equation_sex === 'F' ? 0.9 : 1)), protein: band[2] }
  }
  const resting = 10 * weight + (member.equation_sex === 'M' ? 900 : 700)
  const energy = Math.round(resting * PAL[member.activity_category] + ENERGY_SHIFT[member.goal])
  if (member.goal === 'fat_loss' && energy < 1200) throw new TargetError('DAILY_ENERGY_BELOW_MINIMUM', 'goal')
  return { energy, protein: Math.round(weight * PROTEIN_PER_KG[member.goal] * 10) / 10 }
}

/** 展開為製作項目：同一成員用餐＋便當是兩個項目。 */
export function requestItems(draft: MealRequestDraft): MealRequest['items'] {
  const out: MealRequest['items'] = []
  for (const p of draft.participants) {
    if (p.dining && draft.meal_type) {
      out.push({ participation_id: `${p.member_id}:dining`, member_id: p.member_id, kind: 'dining', meal: draft.meal_type })
    }
    if (p.bento) {
      out.push({ participation_id: `${p.member_id}:bento`, member_id: p.member_id, kind: 'bento', meal: p.bento_meal })
    }
  }
  return out
}

export type TargetItem = {
  participation_id: string
  member_id: string
  kind: 'dining' | 'bento'
  meal: MealType
  ratio: number
  n: number
  daily: { energy: number; protein: number }
  scaled: { energy: number; protein: number }
}

export type Targets =
  | { status: 'incomplete' }
  | { status: 'unavailable'; reason: string; message: string }
  | { status: 'available'; items: TargetItem[]; totalN: number; energy: number; protein: number }

export function computeTargets(draft: MealRequestDraft, members: Map<string, MemberRow>): Targets {
  const entries = requestItems(draft)
  if (!entries.length || !draft.meal_date) return { status: 'incomplete' }
  const items: TargetItem[] = []
  for (const entry of entries) {
    const member = members.get(entry.member_id)
    if (!member) return { status: 'incomplete' }
    let daily
    try {
      daily = dailyTarget(member, draft.meal_date)
    } catch (err) {
      if (err instanceof TargetError) {
        return { status: 'unavailable', reason: err.reason,
          message: REASON_MESSAGES[err.reason] ?? '部分成員資料無法計算目標，請檢查成員設定。' }
      }
      throw err
    }
    const n = Number(member.portion)
    const ratio = MEAL_RATIO[entry.meal]
    items.push({ ...entry, ratio, n, daily,
      scaled: { energy: daily.energy * ratio * n, protein: daily.protein * ratio * n } })
  }
  return {
    status: 'available', items,
    totalN: items.reduce((s, i) => s + i.n, 0),
    energy: items.reduce((s, i) => s + i.scaled.energy, 0),
    protein: items.reduce((s, i) => s + i.scaled.protein, 0),
  }
}

export function targetsView(t: Targets): MealRequest['targets_preview'] {
  if (t.status !== 'available') return t
  return {
    status: 'available',
    rule_version: RULE_VERSION,
    total_n: num(t.totalN),
    target: { energy_kcal: num(t.energy), protein_g: num(t.protein) },
    items: t.items.map((i) => ({
      participation_id: i.participation_id, kind: i.kind, meal: i.meal, meal_ratio: num(i.ratio), n: num(i.n),
    })),
    approved_for_adoption: true,
  }
}

// ---- 整餐用量與營養 ----

export type DishInput = { key: string; lines: { food: Food; grams: number }[] }
export type MealCalc = {
  scale: number
  dishes: DishInput[]
  staple: { food: Food; grams: number } | null
  totals: Record<NutrientKey, number | null>
  /** 只列值為未知的營養素：已知部分合計與缺值食材 */
  knownPartial: Record<string, number>
  missingByNutrient: Record<string, string[]>
}

function sumNutrients(lines: { food: Food; grams: number }[]) {
  const totals = {} as Record<NutrientKey, number | null>
  const knownPartial: Record<string, number> = {}
  const missingByNutrient: Record<string, string[]> = {}
  for (const key of NUTRIENT_KEYS) {
    let known = 0
    const missing: string[] = []
    for (const line of lines) {
      const per100 = line.food.per100[key]
      if (per100 === null) missing.push(line.food.name)
      else known += (line.grams * per100) / 100
    }
    if (missing.length) {
      // 任一食材缺值，整餐就是未知；不拿已知部分充數
      totals[key] = null
      knownPartial[key] = known
      missingByNutrient[key] = [...new Set(missing)]
    } else {
      totals[key] = known
    }
  }
  return { totals, knownPartial, missingByNutrient }
}

const roundGrams = (g: number) => (g >= 10 ? Math.round(g) : Math.round(g * 10) / 10)

export function stapleFood(label: string | null): Food | null {
  if (!label) return null
  if (label === '糙米飯') return foodByName('糙米飯')
  if (label === '白飯' || label === '不指定') return foodByName('白飯')
  return null
}

/**
 * 把每份（1n）的基準食譜放大成整餐用量：主食固定每份 160 g，其餘的菜等比例調整，
 * 讓整餐熱量落在目標的 98% 左右（調整幅度有上下限，所以仍可能超出範圍）。
 */
export function calcMeal(dishes: DishInput[], staple: Food | null, targets: { totalN: number; energy: number }): MealCalc {
  const stapleLine = staple ? { food: staple, grams: Math.round(GRAMS_PER_N_STAPLE * targets.totalN) } : null
  const stapleEnergy = stapleLine ? (stapleLine.grams * (stapleLine.food.per100.energy_kcal ?? 0)) / 100 : 0
  const baseEnergy = dishes.flatMap((d) => d.lines)
    .reduce((s, l) => s + (l.grams * targets.totalN * (l.food.per100.energy_kcal ?? 0)) / 100, 0)
  const wanted = baseEnergy > 0 ? (targets.energy * 0.98 - stapleEnergy) / baseEnergy : 1
  const scale = Math.min(1.8, Math.max(0.6, wanted))
  const scaled = dishes.map((d) => ({
    key: d.key,
    lines: d.lines.map((l) => ({ food: l.food, grams: roundGrams(l.grams * targets.totalN * scale) })),
  }))
  const all = [...scaled.flatMap((d) => d.lines), ...(stapleLine ? [stapleLine] : [])]
  return { scale, dishes: scaled, staple: stapleLine, ...sumNutrients(all) }
}

/** 熱量不可超過目標 110%（低於不限）；蛋白質不可低於目標 90%（高於不限）。未知回傳 null。 */
export function withinRange(calc: MealCalc, targets: { energy: number; protein: number }): boolean | null {
  const { energy_kcal: energy, protein_g: protein } = calc.totals
  if (energy === null || protein === null) return null
  return energy <= targets.energy * 1.1 && protein >= targets.protein * 0.9
}

/** 調味料換成小匙／大匙（取到 ¼，太少為「少許」）；其餘顯示克數。 */
export function displayAmount(f: Food, grams: number) {
  if (!f.seasoning) return { display_amount: num(grams), display_unit: 'g', display_text: null as string | null }
  const teaspoons = Math.round((grams / 5) * 4) / 4
  if (teaspoons < 0.25) return { display_amount: null, display_unit: null, display_text: '少許' }
  if (teaspoons >= 3) {
    return { display_amount: num(Math.round((grams / 15) * 4) / 4), display_unit: '大匙', display_text: null }
  }
  return { display_amount: num(teaspoons), display_unit: '小匙', display_text: null }
}

// ---- 每日建議與每人份量 ----

export const DAILY_REFERENCE_META = {
  version: 'fake-daily-reference-0.1',
  source: {
    publisher: '（假資料）示範衛生單位',
    title: '每日建議攝取量示範表',
    url: 'https://example.com/fake-daily-reference',
    verification: '合成資料，僅供畫面測試',
  },
}
export const HIGH_SODIUM_DISH_MG = 1000

export function dailyReference(energy: number, protein: number): Record<string, DailyReference> {
  return {
    energy_kcal: { kind: 'target', value: num(energy), label: '每日熱量目標' },
    protein_g: { kind: 'target', value: num(protein), label: '每日蛋白質目標' },
    carbohydrate_g: { kind: 'range', min: num((energy * 0.5) / 4), max: num((energy * 0.6) / 4), label: '占每日熱量 50–60%' },
    fat_g: { kind: 'range', min: num((energy * 0.2) / 9), max: num((energy * 0.3) / 9), label: '占每日熱量 20–30%' },
    sugars_g: { kind: 'max', max: num((energy * 0.1) / 4), label: '添加糖不超過每日熱量 10%',
      caveat: '這裡的「糖」是總糖（含天然糖），10% 是指添加糖，僅供參考' },
    sodium_mg: { kind: 'max', max: '2400', label: '每日不超過 2400 mg' },
    dietary_fiber_g: { kind: 'target', value: num((energy / 1000) * 14), label: '每 1000 大卡 14 g' },
  }
}

function shareOf(calc: MealCalc, share: number) {
  const intake: Record<string, string | null> = {}
  const known_partial: Record<string, string> = {}
  for (const key of NUTRIENT_KEYS) {
    const whole = calc.totals[key]
    intake[key] = numOrNull(whole === null ? null : whole * share)
    if (whole === null && calc.knownPartial[key] !== undefined) known_partial[key] = num(calc.knownPartial[key] * share)
  }
  return { intake, known_partial }
}

/**
 * 每人份量。假資料刻意依食量 n 平分整餐（真後端依每人目標熱量比例分），
 * 這樣食量設得比需求大的小孩會超過自己的熱量上限，畫面才看得到「有人超出」的狀態。
 */
function grouped(items: TargetItem[]) {
  const people = new Map<string, { dining: TargetItem | null; bento: TargetItem | null; kinds: ('dining' | 'bento')[] }>()
  for (const item of items) {
    const person = people.get(item.member_id) ?? { dining: null, bento: null, kinds: [] }
    person[item.kind] = item
    if (!person.kinds.includes(item.kind)) person.kinds.push(item.kind)
    people.set(item.member_id, person)
  }
  return people
}

/** 生成結果的每人營養（T15 結果頁）。 */
export function resultPeople(calc: MealCalc, targets: { items: TargetItem[]; totalN: number },
  names: Map<string, string>): ResultPersonNutrition[] {
  const portion = (item: TargetItem): ResultPersonPortion => ({
    n: num(item.n), share: ratio(item.n / targets.totalN), meal_ratio: ratio(item.ratio * item.n),
    ...shareOf(calc, item.n / targets.totalN),
  })
  const empty: ResultPersonPortion = {
    n: '0', share: '0', meal_ratio: '0', intake: Object.fromEntries(NUTRIENT_KEYS.map((k) => [k, null])), known_partial: {},
  }
  return [...grouped(targets.items).entries()].map(([memberId, p]) => {
    const any = (p.dining ?? p.bento)!
    return {
      member_id: memberId,
      name: names.get(memberId) ?? null,
      kinds: p.kinds,
      dining: p.dining !== null,
      // 只帶便當、不在家吃：當餐份量為 0、攝取量為未知（不是 0）
      ...(p.dining ? portion(p.dining) : empty),
      bento: p.bento ? { ...portion(p.bento), meal: p.bento.meal } : null,
      daily: dailyReference(any.daily.energy, any.daily.protein),
    }
  })
}

/** T12 預覽的每份結果（用餐與便當各一筆）。 */
export function previewParticipations(calc: MealCalc, targets: { items: TargetItem[]; totalN: number },
  dishIds: string[]): NonNullable<NutritionPreview['per_person']> {
  return targets.items.map((item) => {
    const share = item.n / targets.totalN
    const { intake, known_partial } = shareOf(calc, share)
    const energy = calc.totals.energy_kcal === null ? null : calc.totals.energy_kcal * share
    const protein = calc.totals.protein_g === null ? null : calc.totals.protein_g * share
    return {
      participation_id: item.participation_id,
      member_id: item.member_id,
      kind: item.kind,
      meal: item.meal,
      n: num(item.n),
      dish_share: ratio(share),
      dish_ids: dishIds,
      staple_g: calc.staple ? num(calc.staple.grams * share) : null,
      target: { energy_kcal: num(item.scaled.energy), protein_g: num(item.scaled.protein) },
      nutrition: { energy_kcal: numOrNull(energy), protein_g: numOrNull(protein) },
      within_10pct: {
        energy_kcal: energy !== null && energy <= item.scaled.energy * 1.1,
        protein_g: protein !== null && protein >= item.scaled.protein * 0.9,
      },
      nutrition_all: intake,
      nutrition_known_partial: known_partial,
    }
  })
}

/** T12 預覽依成員合併：dining 為這一餐，bento 另列。 */
export function previewPeople(calc: MealCalc, targets: { items: TargetItem[]; totalN: number }, dishIds: string[],
  names: Map<string, string>): PersonNutrition[] {
  const portion = (item: TargetItem): PersonPortion => {
    const share = item.n / targets.totalN
    return {
      participation_id: item.participation_id, n: num(item.n), share: ratio(share), meal: item.meal,
      meal_ratio: ratio(item.ratio * item.n), dish_share: ratio(share), dish_ids: dishIds,
      staple_g: calc.staple ? num(calc.staple.grams * share) : null, ...shareOf(calc, share),
    }
  }
  return [...grouped(targets.items).entries()].map(([memberId, p]) => {
    const any = (p.dining ?? p.bento)!
    return {
      member_id: memberId,
      name: names.get(memberId) ?? null,
      kinds: p.kinds,
      dining: p.dining ? portion(p.dining) : null,
      bento: p.bento ? portion(p.bento) : null,
      daily: dailyReference(any.daily.energy, any.daily.protein),
    }
  })
}

export function totalsView(calc: MealCalc): Record<string, string | null> {
  return Object.fromEntries(NUTRIENT_KEYS.map((key) => [key, numOrNull(calc.totals[key])]))
}
export function knownPartialView(calc: MealCalc): Record<string, string> {
  return Object.fromEntries(Object.entries(calc.knownPartial).map(([key, value]) => [key, num(value)]))
}
