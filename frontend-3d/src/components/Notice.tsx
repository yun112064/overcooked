/**
 * 一段行內的說明或錯誤（奶油色便條）。可以放在面板裡，也可以用 <Placed> 放進場景。
 *
 *   <Notice>這桌還沒有小知識。</Notice>
 *   <Notice tone="warn" title="小知識暫時拿不到">菜單、份量與採用資格不受影響。</Notice>
 *   <ErrorNotice error={query.error} onRetry={() => query.refetch()} retrying={query.isFetching} />
 *
 * - tone：info（預設）／success／warn／error。error 會標 role="alert" 讓讀屏立刻唸出來。
 * - action：放在訊息下方的按鈕。
 * - <ErrorNotice>：把 API 錯誤轉成文字（用後端給的訊息，沒有才用 fallback），可附「再試一次」。
 *   錯誤一定以文字呈現，不能只靠動畫或顏色。
 */
import type { ReactNode } from 'react'
import { errorMessage } from '../api/client'
import { Button } from './Button'
import styles from './Notice.module.css'

type NoticeProps = {
  tone?: 'info' | 'success' | 'warn' | 'error'
  title?: string
  children?: ReactNode
  action?: ReactNode
  className?: string
}

export function Notice({ tone = 'info', title, children, action, className }: NoticeProps) {
  return (
    <div className={`${styles.notice} ${styles[tone]} ${className ?? ''}`} role={tone === 'error' ? 'alert' : undefined}>
      {title && <strong>{title}</strong>}
      {children && <div>{children}</div>}
      {action && <div className={styles.action}>{action}</div>}
    </div>
  )
}

type ErrorNoticeProps = {
  /** useQuery／useMutation 的 error */
  error: unknown
  /** 後端沒有給訊息時顯示的文字 */
  fallback?: string
  onRetry?: () => void
  retrying?: boolean
  retryLabel?: string
  className?: string
}

export function ErrorNotice({ error, fallback, onRetry, retrying = false, retryLabel = '再試一次', className }: ErrorNoticeProps) {
  return (
    <Notice
      tone="error" className={className}
      action={onRetry && <Button busy={retrying} onClick={onRetry}>{retrying ? '重試中…' : retryLabel}</Button>}
    >
      {errorMessage(error, fallback)}
    </Notice>
  )
}
