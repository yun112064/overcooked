// 對話用到的純邏輯：不碰 React 與瀏覽器 API，方便單獨測試。
import type { AssistantEngine, AssistantReference, AssistantTurn, AssistantType } from '../api/assistant'
import type { Message } from './chatStore'

export type ServiceStatus = { status: 'online' | 'offline' | 'not_configured'; reason: string | null }
export type TeamStatus = Record<string, ServiceStatus | undefined>

/** 帶給後端的前文最多幾則（和舊前端相同） */
export const HISTORY_TURNS = 6
const HISTORY_CHARS = 2000

const OFFLINE_TEXT: Record<string, string> = {
  NOT_CONFIGURED: '飲食小幫手目前未啟用。',
  SERVICE_NOT_READY: '飲食小幫手還沒準備好（模型或資料庫未啟動）。',
  AUTH_FAILED: '飲食小幫手驗證失敗，請通知管理者。',
}
const OFFLINE_DEFAULT = '飲食小幫手目前離線（團隊電腦未開機或服務未啟動），請稍後再試。'

/** OpenAI 版有設定且在線才能選。 */
export function openaiOnline(team: TeamStatus | undefined): boolean {
  return team?.assistant_openai?.status === 'online'
}

/** 目前選用的那個模型離線時要顯示的說明；在線或還不知道狀態時回傳 null（不知道就不擋使用者）。 */
export function offlineText(team: TeamStatus | undefined, engine: AssistantEngine): string | null {
  const service = engine === 'openai' ? team?.assistant_openai : team?.assistant
  if (!service || service.status === 'online') return null
  return OFFLINE_TEXT[service.reason ?? ''] ?? OFFLINE_DEFAULT
}

/** 只把已完成、有內容的訊息當前文；失敗、被停止或還在生成的不算。 */
export function buildHistory(messages: readonly Message[]): AssistantTurn[] {
  return messages
    .filter((m) => m.status === 'done' && m.text)
    .slice(-HISTORY_TURNS)
    .map((m) => ({ role: m.role, content: m.text.slice(0, HISTORY_CHARS) }))
}

/** 最後一則已經結束（不在串流中）的訊息 id；沒有就是 0。比已讀的大就表示有新回答。 */
export function settledId(messages: readonly Message[]): number {
  const last = messages.at(-1)
  return last && last.status !== 'streaming' ? last.id : 0
}

export type AnswerPart =
  | { kind: 'text'; text: string }
  /** joined：前一個也是引用，顯示成「1,3」；followed：後面緊接著另一個引用。兩個數字相鄰的那一側不能把可點範圍往外擴 */
  | { kind: 'cite'; n: number; joined: boolean; followed: boolean }

/** 把回答裡的 [n] 拆出來；只有對得上參考資料的才算引用，其餘照原文顯示。 */
export function splitCitations(text: string, references: readonly AssistantReference[] = []): AnswerPart[] {
  const parts: AnswerPart[] = []
  for (const piece of text.split(/(\[\d{1,2}\])/g)) {
    const match = /^\[(\d{1,2})\]$/.exec(piece)
    const n = match ? Number(match[1]) : null
    if (n !== null && references.some((r) => r.n === n)) {
      const previous = parts.at(-1)
      const joined = previous?.kind === 'cite'
      if (joined) previous.followed = true
      parts.push({ kind: 'cite', n, joined, followed: false })
    } else if (piece) {
      parts.push({ kind: 'text', text: piece })
    }
  }
  return parts
}

/** 回答下方列出的參考資料：只列回答真的引用到的；沒有引用就不列（查到但沒用上的不能算出處）。 */
export function citedReferences(message: Pick<Message, 'references' | 'citations'>): AssistantReference[] {
  const cited = new Set(message.citations ?? [])
  return cited.size ? (message.references ?? []).filter((r) => cited.has(r.n)) : []
}

export function formatDate(value?: string | null): string | null {
  if (!value) return null
  const d = new Date(value)
  return Number.isNaN(d.getTime()) ? null : `${d.getFullYear()}/${d.getMonth() + 1}/${d.getDate()}`
}

/** 參考資料標題下的那一行：站名、醫學審稿或作者、日期。 */
export function referenceByline(reference: AssistantReference): string {
  // Hello醫師的 reviewers 含撰文、資料查核、醫學審稿；只顯示醫學審稿，沒有時顯示作者
  const reviewer = reference.reviewers?.['醫學審稿'] ?? reference.reviewers?.['審稿'] ?? ''
  const person = reviewer ? `醫學審稿：${reviewer}` : reference.author ? `作者：${reference.author}` : null
  return [reference.source_name, person, formatDate(reference.published_at)].filter(Boolean).join('　')
}

export type AnswerBadge = { label: string; tone: 'info' | 'warn' | 'error' }

const TYPE_BADGE: Partial<Record<AssistantType, AnswerBadge>> = {
  category_answer: { label: '類別資訊', tone: 'info' },
  out_of_scope: { label: '超出範圍', tone: 'warn' },
  declined_medical: { label: '請諮詢醫師', tone: 'warn' },
  emergency: { label: '緊急情況', tone: 'error' },
}

/** 回答上方的小標籤；一般回答與打招呼沒有。 */
export function answerBadge(type: AssistantType | undefined): AnswerBadge | null {
  return (type && TYPE_BADGE[type]) || null
}

/**
 * 視窗要擺在哪（都是相對於跑道左緣的 px）。
 * 盡量讓視窗的中線對著吉祥物，但不超出畫面、不蓋到桌機的側欄；尖角永遠指著吉祥物。
 */
export function placeWindow(input: { anchor: number; lane: number; width: number; leftInset: number; margin?: number }): { left: number; tail: number } {
  const { anchor, lane, width, leftInset, margin = 12 } = input
  const low = leftInset + margin
  const high = lane - width - margin
  // 放不下（視窗比可用寬度還寬）就置中
  const left = low > high ? (lane - width) / 2 : Math.min(high, Math.max(low, anchor - width / 2))
  // 尖角不要貼到圓角上
  const tail = Math.min(width - 28, Math.max(28, anchor - left))
  return { left, tail }
}
