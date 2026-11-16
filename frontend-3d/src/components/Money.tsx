/**
 * 金額。null／undefined 顯示成虛線的「未知」，不會變成 NT$0。
 *
 *   <Money value={prices.purchase_total} />                                  → NT$486 或 未知
 *   <Money value={prices.cost_total} known={prices.known_cost_total} />      → 未知（已知 NT$320）
 *
 * 整包花費（purchase_total）和按比例成本（cost_total）要分開各放一個 <Money>，不要相加。
 */
import { formatMoney } from './formatMoney'
import { UnknownValue } from './UnknownValue'
import styles from './Money.module.css'

type MoneyProps = {
  value: string | number | null | undefined
  /** 金額未知時，已經知道的那一部分（後端的 known_* 欄位） */
  known?: string | number | null
  className?: string
}

export function Money({ value, known, className }: MoneyProps) {
  const text = formatMoney(value)
  if (text !== null) return <span className={`${styles.money} ${className ?? ''}`}>{text}</span>
  const knownText = formatMoney(known)
  return <UnknownValue className={className} detail={knownText === null ? undefined : `已知 ${knownText}`} />
}
