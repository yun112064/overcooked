/**
 * 載入中的小圈圈，旁邊一定有文字（關閉動態時圈圈不轉，只剩文字也看得懂）。
 *
 *   {query.isPending && <Spinner />}
 *   <Spinner label="正在算營養…" />
 *   <Spinner label="載入中" hideLabel />     // 空間很小時：文字只給讀屏
 */
import styles from './Spinner.module.css'

type SpinnerProps = {
  label?: string
  hideLabel?: boolean
  className?: string
}

export function Spinner({ label = '載入中…', hideLabel = false, className }: SpinnerProps) {
  return (
    <span className={`${styles.spinner} ${className ?? ''}`} role="status">
      <i aria-hidden="true" />
      <span className={hideLabel ? 'visually-hidden' : undefined}>{label}</span>
    </span>
  )
}
