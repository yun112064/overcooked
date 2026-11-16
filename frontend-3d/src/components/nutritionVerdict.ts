/**
 * 熱量與蛋白質的三態判定（使用者決定 2026-11-04）：
 * 熱量不可超過目標 110%（低於不限）、蛋白質不可低於目標 90%（高於不限）。
 * 三態：pass 通過／fail 超出或不足／unknown 計算不完整。
 *
 * 規則本身在 api/nutrition.ts 的 withinTarget（和舊前端同一份）；這裡只補上「算不出來」這一態，
 * 讓未知永遠不會被當成 0 或當成通過。
 *
 *   nutritionVerdict('energy_kcal', ratioOf(actual, target))   // ratio＝實際 ÷ 目標
 */
import { toNumber, withinTarget } from '../api/nutrition'
import type { ChipTone } from './StatusChip'

export type TargetNutrient = 'energy_kcal' | 'protein_g'
export type Verdict = 'pass' | 'fail' | 'unknown'

export const TARGET_NUTRIENT_LABELS: Record<TargetNutrient, string> = { energy_kcal: '熱量', protein_g: '蛋白質' }

/** 實際值 ÷ 目標值。任何一邊未知、不是數字，或目標不是正數，都回傳 null（未知），不當成 0。 */
export function ratioOf(
  actual: string | number | null | undefined, target: string | number | null | undefined,
): number | null {
  const a = typeof actual === 'number' ? actual : toNumber(actual)
  const t = typeof target === 'number' ? target : toNumber(target)
  if (a === null || t === null || !Number.isFinite(a) || !Number.isFinite(t) || a < 0 || t <= 0) return null
  return a / t
}

export function nutritionVerdict(nutrient: TargetNutrient, ratio: number | null | undefined): Verdict {
  if (ratio === null || ratio === undefined || !Number.isFinite(ratio) || ratio < 0) return 'unknown'
  return withinTarget(nutrient, ratio) ? 'pass' : 'fail'
}

/** 判定的文字：熱量只會「超出」、蛋白質只會「不足」。 */
export function verdictText(nutrient: TargetNutrient, verdict: Verdict): string {
  if (verdict === 'unknown') return '計算不完整'
  if (verdict === 'pass') return '通過'
  return nutrient === 'energy_kcal' ? '超出' : '不足'
}

/**
 * 一個人（或一餐）的整體判定，給飯碗這種只能畫一種樣子的地方用。
 * 只要有一項算不出來就是「計算不完整」——不能把缺值的那一份畫成有飯；
 * 已知超出或不足的那一項仍會顯示在各自的徽章上。
 */
export function overallVerdict(energyRatio: number | null | undefined, proteinRatio: number | null | undefined): Verdict {
  const energy = nutritionVerdict('energy_kcal', energyRatio)
  const protein = nutritionVerdict('protein_g', proteinRatio)
  if (energy === 'unknown' || protein === 'unknown') return 'unknown'
  return energy === 'pass' && protein === 'pass' ? 'pass' : 'fail'
}

export const OVERALL_VERDICT_TEXT: Record<Verdict, string> = {
  pass: '通過',
  fail: '超出或不足',
  unknown: '計算不完整',
}

/** 用 <StatusChip> 顯示整體判定時的顏色：<StatusChip tone={VERDICT_TONE[v]}>{OVERALL_VERDICT_TEXT[v]}</StatusChip> */
export const VERDICT_TONE: Record<Verdict, ChipTone> = { pass: 'ok', fail: 'warn', unknown: 'unknown' }

/** 0.957 → 「96%」。只給已知的比例用。 */
export function percentText(ratio: number): string {
  return `${Math.round(ratio * 100)}%`
}

/**
 * 徽章上顯示的百分比。四捨五入後剛好落在門檻（110%／90%）但其實沒過時，
 * 多顯示一位小數或直接寫「超過／不到」，免得畫面寫 110% 卻標示超出。
 */
export function ratioText(nutrient: TargetNutrient, ratio: number): string {
  const limit = nutrient === 'energy_kcal' ? 110 : 90
  if (Math.round(ratio * 100) !== limit || withinTarget(nutrient, ratio)) return percentText(ratio)
  const fine = (ratio * 100).toFixed(1)
  if (Number(fine) !== limit) return `${fine}%`
  return nutrient === 'energy_kcal' ? `超過 ${limit}%` : `不到 ${limit}%`
}
