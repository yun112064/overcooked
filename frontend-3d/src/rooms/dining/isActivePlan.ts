import type { MealPlanSummary } from '../../api/types'

/**
 * 這筆餐食紀錄是不是這份問卷目前有效的採用。
 * 舊版後端的 /meal-plans 不一定帶 status，沒帶就當作有效；只有明確被取代（superseded）的才排除。
 */
export function isActivePlan(
  plan: Pick<MealPlanSummary, 'meal_request_id'> & { status?: string | null },
  requestId: string | undefined,
): boolean {
  return plan.meal_request_id === requestId && plan.status !== 'superseded'
}
