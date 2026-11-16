/**
 * 右頁按鈕與物件列上的狀態字（小知識有幾則、生成進行到哪裡）。
 */
import { STAGE_LABELS } from './previewText'
import type { Generation } from './useGeneration'
import type { MenuPreview } from './useMenuPreview'

/** 小知識按鈕旁的狀態字。 */
export function tipsStatus(preview: MenuPreview): string {
  if (!preview.nutrition.data) return ''
  if (!preview.passed) return '未提供'
  if (preview.tips.isError) return '讀取失敗'
  const data = preview.tips.data
  if (!data) return '產生中…'
  if (data.status === 'unavailable') return '暫時拿不到'
  return data.items.length > 0 ? `${data.items.length} 則` : '沒有'
}

/** 生成按鈕旁的狀態字（物件列也用）。 */
export function generationStatus(generation: Generation): string {
  const { job } = generation
  if (generation.running && job) return STAGE_LABELS[job.stage ?? ''] ?? '生成中'
  // 工作狀態一次都沒讀到：寫出來，不要看起來像「沒有在生成」
  if (!job && generation.jobError) return '讀不到進度'
  if (job && generation.forThisVersion) {
    if (job.status === 'succeeded') return '有結果'
    if (job.status === 'failed') return job.error?.code === 'CANCELLED' ? '已取消' : '失敗'
  }
  return generation.hasResult ? '有結果' : ''
}
