import { api, apiError, errorStatus } from './client'

export type Domain = 'recipe' | 'ingredient' | 'price' | 'source_policy'
export type EditableDomain = Exclude<Domain, 'source_policy'>
export type Collection<T> = { items: T[]; next_cursor: string | null }
export type Capabilities = { user_id: string; permissions: string[]; external_execution_enabled: false }
export type Source = {
  artifact_id: string
  artifact_sha256: string
  origin_kind: 'supplied_document' | 'workbook' | 'manual_entry' | 'web_capture' | 'ai_output'
  source_policy_version_id: string | null
}
export type Measurement = {
  status: 'declared' | 'unknown'
  value: string | null
  unknown_reason: string | null
  evidence_ids: string[]
}
export type Line = {
  line_id: string
  food_id: string | null
  ingredient_version_id: string | null
  canonical_name: string
  raw_text: string
  amount: Measurement
  unit: string | null
  weight_g: Measurement
  weight_basis: 'whole_source_recipe_before_cooking'
}
export type Recipe = {
  logical_recipe_id: string | null
  name: string
  standard_dish_id: string | null
  aliases: string[]
  category: string
  cuisine: string
  meal_type: string
  servings: {
    count: string | null; basis: 'portions' | 'batch'; raw_text: string
    portions_per_batch: string | null; evidence_ids: string[]
  }
  ingredients: Line[]
  steps: { step_no: number; instruction: string }[]
  declared_allergen_ids: string[] | null
  sources: Source[]
}
export type Ingredient = {
  food_id: string | null; canonical_name: string; aliases: string[]
  declared_allergen_ids: string[] | null; sources: Source[]
}
export type Price = {
  replaces_version_id?: string
  correction_kind: 'product_binding' | 'quote_correction'
  food_id: string
  ingredient_version_id: string
  ingredient_publication_id: string
  legacy_recipe_release_id: string
  provider: 'pxmart' | 'uniprosperity' | 'manual_evidence'
  product_id: string
  product_name: string
  snapshot_id: number | null
  price_twd: string | null
  package_mass_g: string | null
  observed_at: string | null
  valid_from: string | null
  valid_until: string | null
  availability: 'in_stock' | 'out_of_stock' | 'unknown'
  region_condition: string | null
  store_condition: string | null
  promotion_condition: string | null
  sources: Source[]
}
export type Content = Recipe | Ingredient | Price
export type Pins = {
  legacy_recipe_release_id: string | null
  ingredient_publication_id: string | null
  base_publication_id: string | null
  rule_set_version: string
  contract_version: string
  [key: string]: string | null
}
export type ProposalState = 'draft' | 'changes_requested' | 'submitted' | 'approved' | 'rejected' | 'published' | 'withdrawn'
export type Proposal = {
  proposal_id: string; domain: EditableDomain; revision: number; status: ProposalState; content: Content
  dependency_pins: Pins; content_hash: string; dependency_hash: string; contributors: string[]; updated_at: string
}
export type Workflow = {
  revision: number; content_hash: string
  validation: { validation_id: string; evaluated_at: string; catalog_ready: boolean; issues: Issue[] } | null
  approvals: Partial<Record<ReviewScope, string>>
  latest_task: Task | null
}
export type Issue = { code: string; message: string; severity: string; json_pointer: string }
export type ReviewScope = 'content' | 'source_rights'
export type Verdict = 'approve' | 'request_changes' | 'reject'
export type Diff = { changes: { json_pointer: string; op: string; before: unknown; after: unknown }[]; next_cursor: string | null }
export type Publication = { publication_id: string; created_at: string; member_count: number; manifest_hash: string }
export type Binding = { publication_id: string | null; revision: number; updated_at: string }
export type ActivationPlan = {
  plan_id: string; plan_hash: string; can_activate: boolean; expires_at: string; issues: Issue[]
}
export type Entity = {
  logical_id: string; content: Content; approval_state: 'managed_approved'
  ref: { kind: 'managed'; version_id: string; content_hash: string }
}
export type LegacyRelease = { release_id: string; dataset_version: string; active: boolean; imported_at: string }
export type LegacyRow = { logical_id: string; name: string; approval_state: 'legacy_unverified' }
export type LegacyDetail = LegacyRow & { aliases: string[] } & Partial<{
  standard_dish_id: string; category: string; cuisine: string; meal_type: string; servings_basis: string
  servings_raw: string; original_servings: string | null
  ingredients: { line_id: string; food_id: string; canonical_name: string; raw_text: string; source_amount: string | null; source_unit: string | null; source_weight_g: string | null }[]
  steps: Recipe['steps']
}>
export type Observation = {
  product_name?: string | null
  snapshot_id: number; provider: string; product_id: string | null; raw_price_twd: string | null
  queried_at: string; effective_valid_until: string | null; calculation_usable: boolean; missing_reason: string | null
  usage_status: 'historical_requires_refresh' | 'live_observation' | 'unusable'
}
export type Task = { task_id: string; kind: string; status: string; attempt: number; observed_at: string; execution_mode: string }
export type Connection = { registered_target_key: string; status: string; checked_at: string | null; latency_ms: string | null; reason_code: string | null }
export type Metrics = { as_of: string; recipe_count: number | null; pending_proposal_count: number | null; database_bytes: number | null; running_task_count: number | null }
export type DataType = { key: string; storage_kind: string; owner: string; read_policy: string; write_policy: string; phase1_action: string }

export const domains = { recipe: '食譜', ingredient: '食材', price: '價格', source_policy: '來源政策' }
export const channels = { recipe: 'recipe_catalog', ingredient: 'ingredient_catalog', price: 'price_policy' }
export const proposalStates: Record<ProposalState, string> = {
  draft: '草稿', changes_requested: '需修改', submitted: '待審', approved: '已核准',
  rejected: '已退件', published: '已發布', withdrawn: '已撤回',
}
export const taskStates: Record<string, string> = {
  queued: '已排隊', running: '執行中', succeeded: '已完成', failed: '失敗',
  cancel_requested: '取消中', cancelled: '已取消',
}
export const connectionStates: Record<string, string> = {
  not_configured: '未設定', not_checked: '未檢查', healthy: '正常', unhealthy: '無法連線', stale: '結果已過期',
}
export const originNames: Record<Source['origin_kind'], string> = {
  ai_output: 'AI 候選', web_capture: '網頁截存', workbook: '工作簿', manual_entry: '人工證據', supplied_document: '提供的文件',
}

export async function read<T>(path: string, params?: Record<string, string | number | undefined>, signal?: AbortSignal) {
  return (await api.get<T>('/admin/v1' + path, { params, signal })).data
}

export async function command<T>(path: string, body: object, key: string) {
  const headers: Record<string, string> = { 'Idempotency-Key': key }
  if (body instanceof FormData) headers['Content-Type'] = 'multipart/form-data'
  return (await api.post<T>('/admin/v1' + path, body, { headers })).data
}

export function expectRevision(proposal: Proposal) {
  return { expected_revision: proposal.revision, expected_content_hash: proposal.content_hash }
}

export function time(value: string | null | undefined) {
  if (!value || Number.isNaN(Date.parse(value))) return '未知'
  return new Intl.DateTimeFormat('zh-TW', {
    dateStyle: 'short', timeStyle: 'medium', timeZone: 'Asia/Taipei', hourCycle: 'h23',
  }).format(new Date(value))
}

export function contentName(content: Content) {
  if ('logical_recipe_id' in content) return content.name
  if ('canonical_name' in content) return content.canonical_name
  return content.product_name
}

const errors: Record<string, string> = {
  MANAGEMENT_DISABLED: '管理功能尚未開啟。',
  FORBIDDEN: '目前帳號沒有執行此操作的管理權限。',
  INVALID_INPUT: '資料格式未通過檢查，請確認必填欄位、數值格式與資料來源。',
  VERSION_CONFLICT: '此提案已有較新的版本。請重新整理並核對最新內容。',
  VALIDATION_REQUIRED: '請先完成目前版本的驗證，並處理阻擋問題。',
  APPROVAL_STALE: '審核結果已失效，請重新核對目前版本與驗證結果。',
  DEPENDENCY_CHANGED: '依賴的資料版本已變更，請重新核對後再送出。',
  SELF_REVIEW_FORBIDDEN: '參與過此提案編輯的帳號不能審核此提案。',
  SELF_PUBLISH_FORBIDDEN: '參與過此提案編輯的帳號不能發布此提案。',
  CONSUMER_ADAPTER_NOT_READY: '資料使用端尚未就緒，這個發布版本目前不能啟用。',
  TASK_NOT_READY: '驗證任務尚未完成。',
  LEGACY_VIEW_NOT_AVAILABLE: '舊版目錄僅提供來源參考。',
  PRICE_REPLACEMENT_TARGET_INVALID: '替換目標已變更或與價格適用範圍不同。請從指定發布版本重新選取。',
  PRICE_REPLACEMENT_TARGET_IMMUTABLE: '此草稿已綁定替換目標。',
  PRICE_POLICY_BASE_MISMATCH: '這個版本的基礎發布與目前啟用版本不同，或此版本未曾啟用。',
  PRICE_POLICY_RELEASE_MISMATCH: '價格版本與目前使用的食譜版本不同。',
  BINDING_CHANGED: '目前啟用版本已更新。請重新核對並建立計畫。',
  ACTIVATION_PLAN_INVALID: '啟用計畫已失效，請重新核對。',
}

export function managementError(err: unknown, fallback = '目前無法取得管理資料，請重新整理。') {
  const issue = apiError(err)
  if (issue?.code && errors[issue.code]) return errors[issue.code]
  if (errorStatus(err) === 404) return fallback
  return issue?.message || fallback
}
