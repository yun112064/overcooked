/** 確認步驟用到的文字規則（純函式）。 */

/** T04 來源檢查的食材重量：DEMO 的重量來自 1027 工作簿、尚未確認是整份配方烹調前重量，不可用那個說法。 */
export function feedbackWeightText(line: { whole_recipe_pre_cooking_grams: string | null; weight_semantics?: string | null }): string {
  const grams = line.whole_recipe_pre_cooking_grams ?? '未知'
  return line.weight_semantics === 'workbook_weight_g_unverified'
    ? `工作簿重量（未核對是否為整份配方烹調前） ${grams} g`
    : `整份配方烹調前 ${grams} g`
}
