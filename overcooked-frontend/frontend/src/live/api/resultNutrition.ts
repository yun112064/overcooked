import type { MealResult } from './types'

export type Nutrition = MealResult['nutrition']

/** 7 種營養素（整餐值）；熱量、蛋白質為必要營養，有目標與差距，其餘僅顯示、不擋採用（決策 #4）。 */
export const NUTRIENTS: { key: string; label: string; unit: string; required: boolean }[] = [
  { key: 'energy_kcal', label: '熱量', unit: 'kcal', required: true },
  { key: 'protein_g', label: '蛋白質', unit: 'g', required: true },
  { key: 'carbohydrate_g', label: '碳水化合物', unit: 'g', required: false },
  { key: 'fat_g', label: '脂肪', unit: 'g', required: false },
  { key: 'sugars_g', label: '糖', unit: 'g', required: false },
  { key: 'sodium_mg', label: '鈉', unit: 'mg', required: false },
  { key: 'dietary_fiber_g', label: '膳食纖維', unit: 'g', required: false },
]

/** 顯示取整到 0.1；null／undefined 一律「未知」，不以 0 代替（0 仍顯示 0.0）。 */
export const amount = (v: string | null | undefined, unit: string) =>
  v === null || v === undefined ? '未知' : `${Number(v).toFixed(1)} ${unit}`

function total(n: Nutrition, key: string): string | null | undefined {
  if (key === 'energy_kcal') return n.energy_kcal
  if (key === 'protein_g') return n.protein_g
  return n.other_nutrients?.[key]
}

/**
 * 決策 #53：值為 null 時顯示「未知（已知 X，缺：A、B）」，比照價格的「未知（已知 X 元）」。
 * 已知部分與缺值食材由後端提供（known_partial／missing_by_nutrient）；舊資料沒有時退回 missing_nutrients。
 */
export function nutrientText(n: Nutrition, key: string, unit: string): string {
  const value = total(n, key)
  if (value !== null && value !== undefined) return amount(value, unit)
  const known = n.known_partial?.[key]
  const missing = [...new Set(n.missing_by_nutrient?.[key] ?? n.missing_nutrients?.[key] ?? [])]
  const parts = [
    known !== null && known !== undefined ? `已知 ${amount(known, unit)}` : null,
    missing.length ? `缺：${missing.join('、')}` : null,
  ].filter(Boolean)
  return parts.length ? `未知（${parts.join('，')}）` : '未知'
}
