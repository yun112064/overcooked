/**
 * 生成工作的顯示規則（純函式）：階段名稱、鍋旁的階段列、能不能送出。
 * 階段列只照後端回報的 status／stage 點亮，不自己估百分比。
 */
import type { Job, MealRequest } from '../../api/types'

/** 階段名稱（和舊前端相同）。 */
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

export function stageLabel(job: Pick<Job, 'stage' | 'status'>): string {
  return STAGE_LABELS[job.stage ?? ''] ?? job.status
}

/** 後端處理的先後順序。 */
const ORDER = ['queued', 'started', 'validating_input', 'retrieving', 'ai_generating', 'validating', 'calculating', 'knowledge', 'explaining', 'result_ready']
/**
 * 每次生成都會經過的階段（照後端 generation.py 的流程）。
 * 其餘的只有後端回報正在做時才列出來，不假裝每次都有：
 * AI 生成與檢查只在資料庫排不出菜單時才有；「整理知識說明」在查不到可用的知識時會直接跳過。
 */
const ALWAYS = new Set(['queued', 'retrieving', 'calculating', 'knowledge', 'result_ready'])

export type StageItem = { key: string; label: string; state: 'done' | 'current' | 'pending' }

/**
 * 鍋旁的階段列。
 * - 排隊中：停在「排隊中」；後端說稍後重試、重新排入時另外寫在 note。
 * - 執行中：後端回報的階段是「目前」，順序在它前面的算做完。
 * - 後端回報的階段不在順序表裡（或沒有回報）：不猜進度，只把階段名稱寫在 note。
 */
export function stageList(job: Pick<Job, 'status' | 'stage'>): { items: StageItem[]; note: string | null } {
  const stage = job.stage ?? ''
  const current = job.status === 'queued' ? 'queued' : job.status === 'succeeded' ? 'result_ready' : stage
  const position = ORDER.indexOf(current)
  const done = job.status === 'succeeded'
  const items = ORDER.filter((key) => ALWAYS.has(key) || key === current).map((key): StageItem => {
    const at = ORDER.indexOf(key)
    const state = done || (position >= 0 && at < position) ? 'done' : at === position ? 'current' : 'pending'
    return { key, label: STAGE_LABELS[key], state }
  })
  let note: string | null = null
  if (job.status === 'queued' && stage && stage !== 'queued') note = STAGE_LABELS[stage] ?? stage
  else if (job.status === 'running' && position < 0) note = stage ? STAGE_LABELS[stage] ?? stage : '處理中'
  return { items, note }
}

export function isActive(job: Pick<Job, 'status'> | null | undefined): boolean {
  return !!job && (job.status === 'queued' || job.status === 'running')
}

/** 進行中的工作多久問一次狀態（毫秒） */
const POLL_MS = 2000

/**
 * 工作狀態要不要繼續問：回傳間隔（毫秒），不用問回傳 false。
 * job 是 undefined＝一次都還沒讀到（還在讀，或第一次就失敗）。這時問卷本身若在排隊／生成中也要繼續問：
 * 不然第一次讀取失敗後輪詢永遠不會開始，看不到進度、也按不了取消，只能離開再進來。
 */
export function pollInterval(job: Pick<Job, 'status'> | null | undefined, requestLocked: boolean): number | false {
  return isActive(job) || (job === undefined && requestLocked) ? POLL_MS : false
}

/** 等待時間 m:ss。 */
export function clock(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

export type SubmitState = {
  /** 這份問卷目前這一版的生成工作（不是這一版的不算） */
  own: Job | null
  active: boolean
  /** 已採用：只能看 */
  confirmed: boolean
  hasResult: boolean
  canSubmit: boolean
  canRegenerate: boolean
  /** 要不要擺出「開始煮」：已採用不擺；這一版已經有結果時改用「重新生成」，也不擺 */
  showSubmit: boolean
  /** 主要按鈕的文字 */
  submitLabel: string
}

/** 能不能送出、重新生成（規則和舊前端的生成面板相同；後端同樣會再檢查一次）。 */
export function submitState(row: Pick<MealRequest, 'status' | 'version' | 'complete'>, job: Job | null | undefined): SubmitState {
  const active = isActive(job)
  const own = job && job.input_version === row.version ? job : null
  const confirmed = row.status === 'confirmed'
  const hasResult = row.status === 'result_ready' || row.status === 'awaiting_decision' || confirmed
  const canSubmit = row.complete && !active && !confirmed && !(own && own.status === 'succeeded')
  return {
    own,
    active,
    confirmed,
    hasResult,
    canSubmit,
    showSubmit: !confirmed && (canSubmit || !hasResult),
    canRegenerate: hasResult && !confirmed && row.complete && !active,
    submitLabel: own && own.status === 'failed' ? '再煮一次' : '開始煮',
  }
}

/**
 * 要帶到餐桌的結果版本：後端清單裡最新的那一版與這一版生成工作回報的，取比較新的。
 * （補查價格、重試小知識會產生比生成工作更新的版本；清單還沒讀到或是舊快取時，工作回報的可能比較新。）
 */
export function latestResultVersion(
  results: readonly { result_version: number }[] | undefined, jobVersion: number | null | undefined,
): number | null {
  const versions = [...(results ?? []).map((r) => r.result_version), ...(jobVersion == null ? [] : [jobVersion])]
  return versions.length > 0 ? Math.max(...versions) : null
}
