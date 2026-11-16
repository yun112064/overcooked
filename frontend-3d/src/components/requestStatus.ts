/**
 * 問卷（meal request）狀態的文字與標籤顏色。文字與舊前端的「我的問卷」清單相同。
 * 後端的狀態：draft、queued、running、result_ready、awaiting_decision、failed、confirmed。
 */
import type { ChipTone } from './StatusChip'

const REQUEST_STATUS: Record<string, { label: string; tone: ChipTone }> = {
  draft: { label: '草稿', tone: 'neutral' },
  queued: { label: '排隊中', tone: 'info' },
  running: { label: '生成中', tone: 'info' },
  awaiting_decision: { label: '待決定', tone: 'warn' },
  result_ready: { label: '已有結果', tone: 'info' },
  failed: { label: '失敗', tone: 'danger' },
  confirmed: { label: '已採用', tone: 'ok' },
}

/** 不認得的狀態照後端給的字顯示，不猜它是什麼。 */
export function requestStatusInfo(status: string): { label: string; tone: ChipTone } {
  return REQUEST_STATUS[status] ?? { label: status, tone: 'neutral' }
}
