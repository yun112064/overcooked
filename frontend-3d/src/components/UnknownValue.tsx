/**
 * 未知值。後端回 null 代表「不知道」，不是 0——畫面上一律用這個虛線樣式的「未知」，
 * 不顯示成 0、不畫成有料的長條或飯碗。
 *
 *   <UnknownValue />                          → 未知
 *   <UnknownValue detail="已知 NT$320" />     → 未知（已知 NT$320）
 *   <UnknownValue label="未填" />             → 未填（成員資料沒填的欄位）
 */
import type { ReactNode } from 'react'
import styles from './UnknownValue.module.css'

type UnknownValueProps = {
  /** 預設「未知」 */
  label?: string
  /** 補充已經知道的部分，例如「已知 NT$320」「缺：鹽、醬油」 */
  detail?: ReactNode
  className?: string
}

export function UnknownValue({ label = '未知', detail, className }: UnknownValueProps) {
  return (
    <span className={`${styles.unknown} ${className ?? ''}`}>
      {label}{detail && <small>（{detail}）</small>}
    </span>
  )
}
