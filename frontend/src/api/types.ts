export type Me = {
  user: { id: string; email: string }
  current_household: { id: string; name: string } | null
}

export type Household = { id: string; name: string }

export type IngredientRef = { ingredient_id: string; name: string | null }

export type Activity = 'sedentary' | 'low_active' | 'active' | 'highly_active'
export type Goal = 'general' | 'maintain' | 'muscle_gain' | 'fat_loss'

export type Member = {
  id: string
  display_name: string
  birth_date: string
  age_years: number
  equation_sex: 'M' | 'F'
  weight_kg: string
  activity_category: Activity
  goal: Goal
  portion: string
  allergens: IngredientRef[]
  avoided: IngredientRef[]
  version: number
  updated_at: string
}

export type MemberInput = {
  display_name: string
  birth_date: string
  equation_sex: 'M' | 'F'
  weight_kg: string
  activity_category: Activity
  goal: Goal
  portion: string
  allergen_ingredient_ids: string[]
  avoided_ingredient_ids: string[]
}

export type MemberOptions = {
  rule_version: string
  portions: string[]
  activity_categories: Activity[]
  goals: Goal[]
  adult_goals_min_age_years: number
  minor_goals: Goal[]
}

export const ACTIVITY_LABELS: Record<Activity, string> = {
  sedentary: '低（多為靜態，PAL 1.3）',
  low_active: '稍低（PAL 1.5）',
  active: '適度（PAL 1.7）',
  highly_active: '高（PAL 1.9）',
}

export const GOAL_LABELS: Record<Goal, string> = {
  general: '一般餐食',
  maintain: '維持',
  muscle_gain: '增肌',
  fat_loss: '減脂',
}

export type MealType = 'breakfast' | 'lunch' | 'dinner'

export const MEAL_LABELS: Record<MealType, string> = {
  breakfast: '早餐',
  lunch: '午餐',
  dinner: '晚餐',
}

export type Participant = {
  member_id: string
  dining: boolean
  bento: boolean
  bento_meal: MealType
}

export type MealRequestDraft = {
  meal_date: string | null
  meal_type: MealType | null
  budget: string | null
  staple: string | null
  dish_count: number | null
  taste_preferences: string
  specified_dishes: string[]
  participants: Participant[]
  available_ingredient_ids: string[]
}

export type Issue = { field: string; reason: string; message: string }

export type MealRequest = {
  id: string
  status: string
  version: number
  updated_at: string
  draft: MealRequestDraft
  available_ingredients: IngredientRef[]
  items: { participation_id: string; member_id: string; kind: 'dining' | 'bento'; meal: MealType }[]
  restrictions: {
    ingredients: { ingredient_id: string; name: string | null; kinds: ('allergen' | 'avoided')[]; member_names: string[] }[]
    expanded_ingredients: {
      ingredient_id: string; name: string | null; relation: 'synonym' | 'broader'; via: string[]
      kinds: ('allergen' | 'avoided')[]; member_names: string[]
    }[]
    excluded_food_ids: string[]
    available_but_restricted: string[]
  }
  targets_preview:
    | { status: 'incomplete' }
    | { status: 'unavailable'; reason?: string; message: string }
    | {
        status: 'available'
        rule_version: string
        total_n: string
        target: { energy_kcal: string; protein_g: string }
        items: { participation_id: string; kind: 'dining' | 'bento'; meal: MealType; meal_ratio: string; n: string }[]
        approved_for_adoption: boolean
      }
  issues: Issue[]
  complete: boolean
}

export type MealRequestSummary = {
  id: string
  status: string
  version: number
  meal_date: string | null
  meal_type: MealType | null
  updated_at: string
}

export type Job = {
  id: string
  kind: string
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  stage: string | null
  attempts: number
  max_attempts: number
  meal_request_id: string | null
  input_version: number | null
  result_version: number | null
  error: { code: string; message: string } | null
  /** T14：成功的生成工作附結果摘要；完整結果由 T15 結果頁讀取 */
  result_summary?: {
    generation_result_id: string
    result_version: number
    mode: 'database_menu' | 'ai_generated_menu'
    mode_label: string
    fallback_reason: string | null
  } | null
  created_at: string
  updated_at: string
}
