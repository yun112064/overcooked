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
  created_at: string
  updated_at: string
}

/** T11 資料庫菜單（GET /meal-requests/{id}/recipes） */
export type RecipeSummary = {
  recipe_id: string
  recipe_name: string
  standard_dish_id: string
  category: string | null
  aliases: string[]
  food_ids: string[]
  specified: boolean
}

export type RecipeRetrieval = {
  status: string
  code: string | null
  reason: string | null
  details?: { specified_dishes?: string[] }
  previous_day?: {
    previous_date: string
    records: { id: string; meal_type: string }[]
    excluded_dishes: { standard_dish_id: string; dish_name: string }[]
    specified_exceptions: string[]
  } | null
  selected: RecipeSummary[]
  candidates: RecipeSummary[]
  input_version: number
  fallback_eligible: boolean
  /** 這一版問卷已「不採用」的組合數 */
  rejected_count?: number
  /** 已採用的紀錄（採用後才有） */
  adopted_plan?: MealPlanSummary | null
}

/** 已採用餐食紀錄 */
export type MealPlanSummary = {
  id: string
  meal_date: string
  meal_type: MealType
  mode: string
  status: string
  adopted_at: string
  meal_request_id: string | null
  dishes: { dish_name: string; recipe_id: string | null }[]
  energy_kcal: string | null
  protein_g: string | null
}

/** 採用當下保存的快照（與結果頁相同欄位，之後資料更新不會改變） */
export type MealPlanDetail = MealPlanSummary & {
  snapshot: {
    selected?: RecipeSummary[]
    staple_choice?: string | null
    status_message?: string
    targets?: Record<string, string>
    nutrition?: { totals: Record<string, string | null> | null }
    nutrition_all?: NutritionPreview['nutrition_all']
    dishes?: NutritionPreview['dishes']
    staple?: NutritionPreview['staple']
    people?: NutritionPreview['people']
    daily_reference_meta?: NutritionPreview['daily_reference_meta']
  }
}

/** T12 營養計算（POST /meal-requests/{id}/nutrition-preview）；數值為精確字串（可能是分數 a/b） */
export type NutritionPreview = {
  status: 'nutrition_passed' | 'outside_tolerance' | 'calculation_incomplete'
  status_message: string
  dish_portions: { factors?: Record<string, string> } | null
  /** 每道菜驗證後的整餐用量（克，精確字串） */
  dishes?: {
    recipe_id: string
    /** grams 為整餐克數（精確字串）；調味料另附 display_*（小匙／大匙或「少許」） */
    ingredients: {
      name: string; food_id: string; unit: string | null; grams: string | null
      /** 畫面分組：主食材／佐料／調味 */
      group?: '主食材' | '佐料' | '調味'
      display_amount: string | null; display_unit: string | null; display_text: string | null
    }[]
    steps: string[]
    adjusted: boolean
  }[]
  staple: { name?: string; final_grams?: string } | null
  /** 每位參與者依自己的熱量目標分到的份量與營養（只有營養通過時才有） */
  per_person?: {
    participation_id: string
    member_id: string
    kind: 'dining' | 'bento'
    meal: MealType
    n: string
    /** 每道菜整份分給這個人（或便當）的比例；全部參與者加總為 1 */
    dish_share: string
    dish_ids: string[]
    staple_g: string | null
    target: { energy_kcal: string; protein_g: string }
    nutrition: { energy_kcal: string | null; protein_g: string | null }
    /** 是否符合 T12 標準：熱量 ≤ 目標 110%、蛋白質 ≥ 目標 90%（欄名沿用） */
    within_10pct: { energy_kcal: boolean; protein_g: boolean }
    /** 七種營養素（只供顯示；null 表示資料缺值） */
    nutrition_all: Record<string, string | null>
    /** 缺值營養素中，這個人分到的已知部分 */
    nutrition_known_partial?: Record<string, string>
  }[]
  targets: Record<string, string>
  input_issues: { code: string; recipe_id: string; message: string; adoption_blocked: boolean }[]
  adoption_blocked: boolean
  final_food_ids: string[]
  nutrition_passed: boolean
  nutrition: { totals: Record<string, string | null> | null; missing: string[] }
  /** 每人（用餐與便當分開）這一餐吃到的量，對照每日建議攝取量（畫面用） */
  people?: PersonNutrition[]
  daily_reference_meta?: { version: string; source: { publisher: string; title: string; url: string } }
  /** 七種營養素整餐值（只供顯示；只有熱量（≤110%）、蛋白質（≥90%）參與驗證）；缺值時附已知部分與缺資料的食材 */
  nutrition_all?: {
    totals: Record<string, string | null>
    known_partial: Record<string, string>
    missing_by_nutrient: Record<string, string[]>
  }
}

/** 每日建議：target 為建議值，range 為上下限，max 為上限（值皆為精確字串） */
export type DailyReference = { kind: 'target' | 'range' | 'max'; value?: string; min?: string; max?: string; label: string; caveat?: string }

/** 一個人的一份（用餐或便當）：份量、應占每日比例、七種營養素攝取量 */
export type PersonPortion = {
  participation_id: string
  n: string
  share: string
  meal: MealType
  meal_ratio: string
  dish_share: string
  dish_ids: string[]
  staple_g: string | null
  intake: Record<string, string | null>
  known_partial: Record<string, string>
}

export type PersonNutrition = {
  member_id: string
  name: string | null
  kinds: ('dining' | 'bento')[]
  dining: PersonPortion | null
  bento: PersonPortion | null
  daily: Record<string, DailyReference>
}

/** T11 營養小知識（POST /meal-requests/{id}/knowledge-tips） */
export type KnowledgeTips = {
  status: 'available' | 'not_found' | 'unavailable'
  reason?: string | null
  items: {
    ingredient_id: string
    ingredient_name: string | null
    tip_zh: string
    citations: { knowledge_id: string; source_url: string; source_label?: string | null; conditions: string | null }[]
  }[]
  dropped: { ingredient_id: string; reason: string }[]
  not_found: string[]
  knowledge_status: string | null
  nutrition_status: string | null
}
