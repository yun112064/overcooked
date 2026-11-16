/**
 * 結果頁用到的文字與換算（從舊前端的 MenuPreviewPanel、PersonNutrition、GenerationPanel 搬過來）。
 * 判定與計算都是後端做的；這裡只把後端給的值變成畫面上的字，未知一律留著 null，不補 0。
 */
import { toNumber, withinTarget } from '../../api/nutrition'
import type { DailyReference, Job, KnowledgeTips, NutritionPreview, RecipeRetrieval } from '../../api/types'

/** 資料庫排不出菜單的原因 → 說明與做法；有菜名時直接寫出來。 */
export function retrievalMessage(menu: Pick<RecipeRetrieval, 'reason' | 'details'>, staple: string | null): string {
  const names = (menu.details?.specified_dishes ?? []).map((n) => `「${n}」`).join('、')
  switch (menu.reason) {
    case 'SPECIFIED_DISH_STAPLE_CONFLICT':
      return `指定的${names || '菜'}本身就是主食，和你選的主食「${staple ?? ''}」重複。請把主食改成「不需要主食」，或拿掉這道菜。`
    case 'SPECIFIED_DISH_DIETARY_CONFLICT':
      return `指定的${names || '菜'}含有成員過敏或禁吃的食材。請拿掉這道菜，或確認成員的禁忌設定。`
    case 'SPECIFIED_DISH_NOT_IN_DATABASE':
      return `食譜庫裡找不到指定的${names || '菜'}。請改寫菜名或拿掉，其他菜會由系統搭配。`
    case 'SPECIFIED_DISH_AMBIGUOUS':
      return '指定的菜名對應到不只一道菜，請把菜名寫得更完整。'
    case 'ALL_EXCLUDED_BY_PREVIOUS_DAY':
      return '符合條件的菜昨天都吃過了。可以減少道數，或直接指定想吃的菜。'
    case 'NO_MATCHING_RECIPES':
      return '食譜庫裡符合條件的菜不夠這麼多道。可以減少道數，或確認成員的禁忌設定。'
    case 'ALL_MENUS_REJECTED':
      return '資料庫裡符合條件的組合都已經換過了。可以調整道數、指定菜色或主食後再查詢。'
    case 'MENU_STRUCTURE_UNMET':
      return '湊不出符合規則的主菜與湯品組合。可以調整道數或指定菜色。'
    default:
      return '資料庫沒有可用的菜單，可以調整問卷後再試。'
  }
}

export const TIP_UNAVAILABLE: Record<string, string> = {
  EXPLANATION_NOT_CONFIGURED: '尚未設定雲端小知識服務（OPENAI_API_KEY）。',
  NOT_CONFIGURED: '食材知識服務未啟動。',
  KNOWLEDGE_UNAVAILABLE: '食材知識服務暫時無法使用。',
  EMBEDDING_UNAVAILABLE: '本機知識檢索模型無法使用。',
  EMBEDDING_NOT_CONFIGURED: '尚未設定雲端知識檢索服務（OPENAI_API_KEY）。',
  ADOPTION_BLOCKED: '這份菜單不能採用（例如含禁吃食材），不查小知識。',
}

/** T04 來源檢查的食材重量：DEMO 的重量來自 1027 工作簿、尚未確認是整份配方烹調前重量，不可用那個說法。 */
export function feedbackWeightText(line: { whole_recipe_pre_cooking_grams: string | null; weight_semantics?: string | null }): string {
  const grams = line.whole_recipe_pre_cooking_grams ?? '未知'
  return line.weight_semantics === 'workbook_weight_g_unverified'
    ? `工作簿重量（未核對是否為整份配方烹調前） ${grams} g`
    : `整份配方烹調前 ${grams} g`
}

/** 小知識來源的網站名稱：網頁取平台名（健康醫療網、Hello醫師），論文取期刊名；沒有時用網址主機名。 */
export function sourceName(label: string | null | undefined, url: string): string {
  const lines = (label ?? '').split('\n').map((s) => s.trim()).filter(Boolean)
  if (lines.length >= 2) return lines[1].replace(/\s+\d.*$/, '')
  if (lines.length === 1) return lines[0]
  try { return new URL(url).hostname } catch { return '原文' }
}

/** 只讓 http(s) 的網址變成可以點的連結；其他的只顯示名稱。 */
export function safeHref(url: string | null | undefined): string | null {
  if (!url) return null
  try {
    const parsed = new URL(url)
    return parsed.protocol === 'http:' || parsed.protocol === 'https:' ? parsed.href : null
  } catch {
    return null
  }
}

/** 這份小知識用到的網站名稱（去重）。 */
export function tipSites(data: KnowledgeTips | undefined): string {
  if (!data || data.status === 'unavailable') return ''
  return [...new Set(data.items.flatMap((k) => k.citations.map((c) => sourceName(c.source_label, c.source_url))))].join('、')
}

/** 整餐熱量／蛋白質旁邊的「目標 X・+5%」。值或目標未知時不寫百分比。 */
export function targetNote(
  key: 'energy_kcal' | 'protein_g', value: number | null, target: number | null, unit: string,
): { note: string | null; tone: 'default' | 'good' | 'warn' } {
  if (value === null || !target) return { note: target ? `目標 ${Math.round(target)} ${unit}` : null, tone: 'default' }
  const diff = value / target - 1
  return {
    note: `目標 ${Math.round(target)} ${unit}・${diff >= 0 ? '+' : ''}${Math.round(diff * 100)}%`,
    tone: withinTarget(key, value / target) ? 'good' : 'warn',
  }
}

type NutritionAll = NonNullable<NutritionPreview['nutrition_all']>

/** 整餐七種營養素的一列：有值就給字；缺值給「已知多少、缺哪些食材」，不以 0 代替。 */
export function nutrientCell(all: NutritionAll, key: string, unit: string): { value: string | null; detail: string | null } {
  const digits = unit === 'g' ? 1 : 0
  const value = toNumber(all.totals[key])
  if (value !== null) return { value: `${value.toFixed(digits)} ${unit}`, detail: null }
  const known = toNumber(all.known_partial[key])
  const lacking = all.missing_by_nutrient[key] ?? []
  const parts = [
    known !== null ? `已知 ${known.toFixed(digits)} ${unit}` : null,
    lacking.length ? `缺：${lacking.join('、')}` : null,
  ].filter(Boolean)
  return { value: null, detail: parts.length ? parts.join('，') : null }
}

// ---- 每人營養 ----

/** 顯示用取整：kcal、mg 到整數，其餘到 0.1。 */
export function amountText(value: number, unit: string): string {
  return unit === 'kcal' || unit === 'mg' ? `${Math.round(value)}` : value.toFixed(1)
}

export type Reference = { text: string; base: number; low: number | null; high: number | null }

/** 每日建議的文字與比較基準（範圍用上下限、上限用上限、建議值用建議值）。 */
export function reference(daily: DailyReference | undefined, unit: string): Reference | null {
  if (!daily) return null
  const min = daily.min !== undefined ? toNumber(daily.min) : null
  const max = daily.max !== undefined ? toNumber(daily.max) : null
  const value = daily.value !== undefined ? toNumber(daily.value) : null
  if (daily.kind === 'range' && min !== null && max !== null) {
    return { text: `${Math.round(min)}–${Math.round(max)} ${unit}`, base: max, low: min, high: max }
  }
  if (daily.kind === 'max' && max !== null) return { text: `< ${Math.round(max)} ${unit}`, base: max, low: null, high: max }
  if (value !== null) return { text: `${amountText(value, unit)} ${unit}`, base: value, low: value, high: value }
  return null
}

/** 這一餐占每日建議的百分比；建議是範圍時給「占上限–占下限」。 */
export function shareText(intake: number, ref: Reference): string {
  if (ref.low !== null && ref.high !== null && ref.low !== ref.high) {
    return `${Math.round((intake / ref.high) * 100)}–${Math.round((intake / ref.low) * 100)}%`
  }
  return `${Math.round((intake / ref.base) * 100)}%`
}

export type BarTone = 'plain' | 'overMeal' | 'overDay'

/** 長條的顏色：上限類（糖、鈉）超過整天上限是紅、超過這一餐的份額是黃，其餘是一般。 */
export function barTone(kind: DailyReference['kind'], ratio: number, mealRatio: number): BarTone {
  if (kind !== 'max') return 'plain'
  if (ratio > 1) return 'overDay'
  return ratio > mealRatio ? 'overMeal' : 'plain'
}

/** 配合長條顏色寫出來的字（和餐桌、冰箱同一句）：超標不能只靠顏色分辨。 */
export const BAR_NOTES: Record<BarTone, string | null> = {
  plain: null,
  overMeal: '超過這一餐的份額',
  overDay: '超過每日上限',
}

// ---- 生成工作 ----

export const STAGE_LABELS: Record<string, string> = {
  queued: '排隊中',
  started: '開始處理',
  validating_input: '檢查問卷',
  retrieving: '查詢資料庫食譜',
  ai_generating: 'AI 生成菜單中',
  validating: '檢查菜單條件',
  calculating: '計算營養與價格',
  knowledge: '查詢營養知識',
  explaining: '整理知識說明',
  retry_scheduled: '稍後自動重試',
  recovered: '重新排入佇列',
  result_ready: '已完成',
  failed: '失敗',
  needs_manual_retry: '需要確認後重新送出',
  cancelled: '已取消',
}

export function isActiveJob(job: Pick<Job, 'status'> | null | undefined): boolean {
  return !!job && (job.status === 'queued' || job.status === 'running')
}

/** 進行中的工作多久問一次狀態（毫秒；和舊前端相同） */
const POLL_MS = 2000

/**
 * 工作狀態要不要繼續問：回傳間隔（毫秒），不用問回傳 false。
 * job 是 undefined＝一次都還沒讀到（還在讀，或第一次就失敗）。這時這張單本身若在排隊／生成中也要繼續問：
 * 不然第一次讀取失敗後輪詢永遠不會開始，看不到進度、按不了取消，「生成中不能改」的鎖也解不開。
 * null＝讀到了、這張單沒有生成工作，不用問。
 */
export function pollInterval(job: Pick<Job, 'status'> | null | undefined, requestLocked: boolean): number | false {
  return isActiveJob(job) || (job === undefined && requestLocked) ? POLL_MS : false
}

/** 95 秒 → 「1:35」 */
export function clock(seconds: number): string {
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`
}
