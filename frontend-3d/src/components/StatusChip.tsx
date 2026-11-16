/**
 * 狀態標籤（小膠囊）。顏色只是輔助，狀態一定要寫成文字放在裡面。
 *
 *   <StatusChip tone="warn">待決定</StatusChip>
 *   const s = requestStatusInfo(request.status)   // 見 requestStatus.ts
 *   <StatusChip tone={s.tone}>{s.label}</StatusChip>
 *
 * tone：neutral 一般／info 進行中／ok 完成或通過／warn 需要注意／danger 失敗／unknown 未知（虛線）。
 */
import type { ReactNode } from 'react'
import styles from './StatusChip.module.css'

export type ChipTone = 'neutral' | 'info' | 'ok' | 'warn' | 'danger' | 'unknown'

type StatusChipProps = {
  tone?: ChipTone
  children: ReactNode
  className?: string
}

export function StatusChip({ tone = 'neutral', children, className }: StatusChipProps) {
  return <span className={`${styles.chip} ${styles[tone]} ${className ?? ''}`}>{children}</span>
}
