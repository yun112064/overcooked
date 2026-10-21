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
  /** T15 結果重試工作（knowledge_retry／price_retry）的來源結果版本 */
  source_result_version?: number | null
  error: { code: string; message: string } | null
  /** T14：成功的生成工作附結果摘要；完整結果由 T15 結果頁讀取 */
  result_summary?: {
    generation_result_id: string
    result_version: number
    mode: 'database_menu' | 'ai_generated_menu'
    mode_label: string
    fallback_reason: string | null
    /** T15：awaiting_decision＝缺價或缺必要營養待決定；result_ready 不代表可採用 */
    request_status?: 'result_ready' | 'awaiting_decision'
  } | null
  created_at: string
  updated_at: string
}

// --- T15 生成結果與缺值決策（金額、克數、營養以字串保存精確值；null 代表未知，不是 0） ---

export type DecisionKind = 'accept_missing_price' | 'continue_missing_nutrition'

export type ResultBlock = { code: string; message: string; [key: string]: unknown }

export type PendingDecision = {
  kind: DecisionKind
  message: string
  missing_food_ids?: string[]
  known_cost_total?: string | null
}

export type ResultDecision = {
  id: string
  kind: DecisionKind | 'retry_missing_price'
  result_version: number
  request_version: number
  missing_food_ids: string[] | null
  produced_result_version: number | null
  created_at: string
}

export type PriceItem = {
  food_id: string
  name: string
  shortage_grams: string
  price_status: 'known' | 'unknown'
  packs: number | null
  purchase_price: string | null
  cost: string | null
  missing_reason: string | null
  retailer: string | null
  /** 畫面顯示的來源名稱（全聯／萬家福） */
  source_name: string | null
  source: string | null
  product_name: string | null
  product_url: string | null
  package_description: string | null
  queried_at: string | null
  valid_until: string | null
}

export type KnowledgeCitation = {
  knowledge_id: string
  source_version: string
  ingredient_id: string
  excerpt: string
  source_url: string | null
  rights: string | null
  conditions: string | null
  // G07 出處（外部來源必帶，顯示時不可移除）：來源名稱、文章標題、發布時間、顯示用文字
  source_name?: string | null
  title?: string | null
  published_at?: string | null
  content_provider?: string | null
  attribution?: string | null
}

export type ResultIngredient = {
  food_id: string
  name: string
  weight_basis: string | null
  whole_meal_grams: string | null
  display_grams: string | null
  /** 畫面用量：克到 1 g；湯匙等容量單位到 0.5；無換算者照原單位，都沒有時顯示原文 */
  display_amount?: string | null
  display_unit?: string | null
  display_text?: string | null
  /** decisions #19：克數或烹調係數來自批次 LLM 推估（gpt-5.4-mini）；人工覆寫值不算 */
  llm_estimated?: boolean
  estimate_label?: string | null
  /** decisions #28：TFDA 樣本對照來源；llm_mapped 為 LLM 推薦的對照（標示「LLM 對照」） */
  tfda_mapping_source?: 'exact' | 'alias' | 'synonym' | 'human' | 'llm_mapped' | null
  tfda_sample_id?: string | null
  llm_mapped?: boolean
  mapping_label?: string | null
  estimated_nutrients?: string[]
  /** decisions #40：保留率視為 1 的近似營養素 */
  approximate_nutrients?: string[]
  estimates?: {
    /** nutrient_fill（decisions #32）：TFDA 樣本熱量／蛋白質空白，以 LLM 推估每 100 g 補值 */
    kind: 'cooking' | 'unit' | 'tfda_mapping' | 'nutrient_fill'
    source: string
    status: string
    model: string | null
    prompt_version: string | null
    schema_version: string | null
    llm_estimated: boolean
  }[]
}

export type LlmEstimates = {
  used: boolean
  food_ids: string[]
  nutrients: string[]
  label: string | null
}

/** decisions #28：哪些食材的 TFDA 樣本來自 LLM 對照推薦 */
export type LlmMappings = {
  used: boolean
  food_ids: string[]
  label: string | null
}

/** decisions #40：烹調後以 TFDA 生組成、保留率視為 1 近似的非必要營養素（畫面標示「近似」） */
export type Approximations = {
  used: boolean
  food_ids: string[]
  nutrients: string[]
  basis: string | null
  label: string | null
}

export type NutritionSuggestion = {
  code: 'NUTRITION_OUT_OF_RANGE' | 'NUTRITION_INCOMPLETE' | 'INGREDIENT_UNRESOLVED'
  nutrient?: 'energy_kcal' | 'protein_g'
  direction?: 'increase' | 'decrease'
  needed_change?: string
  within_adjustable_ranges?: boolean
  action?: string
  ingredients: {
    food_id: string
    name: string
    direction?: 'increase' | 'decrease'
    adjustable_min?: string | null
    adjustable_max?: string | null
    adjustment_approved?: boolean
    max_change?: string | null
  }[]
}

export type RetryState = {
  job_id: string
  status: 'queued' | 'running' | 'succeeded' | 'failed'
  stage: string | null
  attempts: number
  error: { code: string; message: string } | null
  produced_result_version: number | null
  created_at: string
}

export type MealResult = {
  meal_request_id: string
  request_status: string
  current_request_version: number
  result_version: number
  request_version: number
  derived_from_result_version: number | null
  created_at: string
  mode: 'database_menu' | 'ai_generated_menu'
  mode_label: string
  fallback_reason: string | null
  fallback_message: string | null
  dishes: {
    standard_dish_id: string
    dish_name: string
    aliases: string[]
    source: 'database' | 'ai'
    recipe_id: string | null
    specified: boolean
    cooking_method: string | null
    steps: string[]
    ingredients: ResultIngredient[]
  }[]
  /** 主食（decisions #16）：計入營養與 ±10%，不計價格；份量設定待營養審核者確認 */
  staple: {
    label: string
    tfda_name: string | null
    food_id: string | null
    review_status: string | null
    unresolved_reason: string | null
    whole_meal_grams: string | null
    display_amount: string | null
    display_unit: string | null
    display_text: string | null
    counts_for_price: false
    llm_estimated?: boolean
  } | null
  nutrition: {
    total_n: string | null
    target: { energy_kcal: string; protein_g: string } | null
    energy_kcal: string | null
    protein_g: string | null
    gap: { energy_kcal?: string; protein_g?: string }
    within_range: boolean
    other_nutrients: Record<string, string | null>
    missing_nutrients: Record<string, string[]>
    /** false：用量未通過驗證（缺資料或無法在範圍內符合目標），僅供查看 */
    quantities_verified?: boolean
    suggestions?: NutritionSuggestion[]
    /** 哪些食材、哪些營養用到 LLM 推估的換算係數（decisions #19） */
    llm_estimates?: LlmEstimates
    llm_mappings?: LlmMappings
    approximations?: Approximations
  }
  prices: {
    status: string
    budget: string | null
    budget_status: 'OK' | 'PRICE_INCOMPLETE' | 'BUDGET_EXCEEDED' | null
    price_complete: boolean
    purchase_total: string | null
    cost_total: string | null
    known_purchase_total: string | null
    known_cost_total: string | null
    items: PriceItem[]
  }
  knowledge: {
    status: 'available' | 'not_found' | 'unavailable'
    code?: string
    reason?: string | null
    /** 知識服務故障或額度不足時可「只重試知識說明」 */
    retry_available?: boolean
    items: { ingredient_id: string; ingredient_name: string | null; explanation: string; citations: KnowledgeCitation[] }[]
  }
  /** 重試工作狀態（查價中／說明產生中、失敗原因、產生的新版本） */
  retries?: { price: RetryState | null; knowledge: RetryState | null }
  /** 目前的昨日已採用紀錄比對（衝突時不可採用，按鈕停用） */
  previous_day_check?: {
    status: 'ok' | 'conflict'
    history_changed: boolean
    code?: string
    conflicting_dishes?: string[]
    previous_date?: string
    message?: string
  }
  previous_day: {
    previous_date: string | null
    has_records: boolean
    excluded_dishes: { standard_dish_id: string; dish_name: string; aliases: string[] }[]
    specified_repeats: string[]
  }
  knowledge_retry?: { from_result_version: number; retried_at: string } | null
  price_retry?: { from_result_version: number; retried_at: string; price_source: string } | null
  eligibility: {
    adoptable: boolean
    viewable: boolean
    blocks: ResultBlock[]
    pending_decisions: PendingDecision[]
    price_exception_accepted: boolean
    decisions: ResultDecision[]
  }
}

export type MealResultSummary = {
  result_version: number
  request_version: number
  mode: 'database_menu' | 'ai_generated_menu'
  mode_label: string
  created_at: string
  adoptable: boolean
}

export type MealPlanDish = {
  standard_dish_id: string
  dish_name: string
  aliases: string[]
  source: 'database' | 'ai' | null
}

/** T16 已採用餐食紀錄（不代表實際已食用）。 */
export type MealPlanSummary = {
  id: string
  meal_date: string
  meal_type: MealType
  mode: 'database_menu' | 'ai_generated_menu'
  mode_label: string
  ai_generated: boolean
  record_label: string
  adopted_at: string
  meal_request_id: string | null
  result_version: number | null
  request_version: number | null
  price_exception_accepted: boolean
  dishes: MealPlanDish[]
}

type ResultDish = MealResult['dishes'][number]

export type MealPlanDetail = Omit<MealPlanSummary, 'dishes'> & {
  snapshot_available: boolean
  fallback_message?: string | null
  dishes: (MealPlanDish & Partial<Pick<ResultDish, 'cooking_method' | 'steps' | 'ingredients'>> & {
    recipe_source?: { recipe_id: string; source_name: string; source_url: string } | null
  })[]
  nutrition?: MealResult['nutrition']
  prices?: MealResult['prices']
  knowledge?: MealResult['knowledge']
  previous_day?: MealResult['previous_day'] & { history_changed: boolean }
  missing?: { price_food_ids: string[]; price_exception_accepted: boolean; nutrients: Record<string, string[]> }
}
