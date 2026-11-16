/**
 * 熱量／蛋白質的三態徽章：通過、超出（熱量）或不足（蛋白質）、計算不完整。
 * 判定規則在 nutritionVerdict.ts。算不出來時不顯示百分比，用虛線樣式寫「計算不完整」。
 *
 *   <NutritionBadge nutrient="energy_kcal" ratio={ratioOf(actual, target)} />   → 熱量 96% 通過
 *   <NutritionBadge nutrient="protein_g" ratio={null} />                        → 蛋白質 計算不完整
 *
 * ratio＝實際 ÷ 目標；未知請傳 null，不要傳 0。
 */
import {
  TARGET_NUTRIENT_LABELS, nutritionVerdict, ratioText, verdictText, type TargetNutrient,
} from './nutritionVerdict'
import styles from './NutritionBadge.module.css'

type NutritionBadgeProps = {
  nutrient: TargetNutrient
  ratio: number | null | undefined
  className?: string
}

// 記號只是輔助；判定的文字一定寫出來，不只靠顏色或記號
const MARK = { pass: '✓', fail: '!', unknown: '?' } as const

export function NutritionBadge({ nutrient, ratio, className }: NutritionBadgeProps) {
  const verdict = nutritionVerdict(nutrient, ratio)
  return (
    <span className={`${styles.badge} ${styles[verdict]} ${className ?? ''}`}>
      <i aria-hidden="true">{MARK[verdict]}</i>
      <b>{TARGET_NUTRIENT_LABELS[nutrient]}</b>
      {verdict !== 'unknown' && typeof ratio === 'number' && <span>{ratioText(nutrient, ratio)}</span>}
      <em>{verdictText(nutrient, verdict)}</em>
    </span>
  )
}
