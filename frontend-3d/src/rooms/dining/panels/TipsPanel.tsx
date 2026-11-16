/**
 * 營養小知識（DESIGN.md 5.6）：小卡盤攤開成一疊可以左右翻的知識卡。
 *
 * - 內容只照後端給的顯示（食材名、說明、出處），不改寫、不自己補充。
 * - 出處固定印在卡片正面，可以點「原文」；不藏在翻面或提示裡。
 * - 暫時拿不到時寫明原因，並說明菜單、份量與採用資格不受影響；可以重試時才給按鈕。
 * - 這桌沒有小知識時就是沒有，不拿別的內容充數，也不給重試。
 */
import { useState, type ReactNode } from 'react'
import type { KnowledgeCitation, MealResult, RetryState } from '../../../api/types'
import { Button } from '../../../components/Button'
import { Notice } from '../../../components/Notice'
import { Spinner } from '../../../components/Spinner'
import type { TipsState } from '../tableModel'
import styles from './panels.module.css'

function Citation({ citation }: { citation: KnowledgeCitation }) {
  if (citation.attribution) {
    return (
      <>
        {citation.attribution}{' '}
        {citation.source_url && <a href={citation.source_url} target="_blank" rel="noreferrer">原文</a>}
      </>
    )
  }
  return (
    <>
      來源：
      {citation.source_url
        ? <a href={citation.source_url} target="_blank" rel="noreferrer">{citation.knowledge_id}</a>
        : citation.knowledge_id}
      （{citation.source_version}）
    </>
  )
}

function Cards({ items }: { items: MealResult['knowledge']['items'] }) {
  const [page, setPage] = useState(0)
  // 換版本後張數可能變少，停在最後一張
  const index = Math.min(page, items.length - 1)
  const item = items[index]
  if (!item) return <p>這一版沒有可以顯示的小知識卡。</p>
  return (
    <>
      <article className={styles.tipCard} aria-label={`第 ${index + 1} 張，共 ${items.length} 張`}>
        <h3>{item.ingredient_name ?? item.ingredient_id}</h3>
        <p>{item.explanation}</p>
        <p className={styles.tipSource}>
          {item.citations.map((c, k) => (
            <span key={c.knowledge_id}>{k > 0 && '、'}<Citation citation={c} /></span>
          ))}
        </p>
      </article>
      {items.length > 1 && (
        <div className={styles.pager}>
          <Button aria-label="上一張" disabled={index === 0} onClick={() => setPage(index - 1)}>‹</Button>
          <span aria-live="polite">{index + 1} / {items.length}</span>
          <Button aria-label="下一張" disabled={index === items.length - 1} onClick={() => setPage(index + 1)}>›</Button>
        </div>
      )}
      <p className={styles.footnote}>資料來源：健康醫療網。每張卡是這桌其中一樣食材的小知識，內容與出處都照來源顯示。</p>
    </>
  )
}

type TipsDetailProps = {
  knowledge: MealResult['knowledge']
  state: TipsState['state']
  /** 上一次重試的狀態（失敗時顯示原因） */
  retry: RetryState | null
  /** 只能看（已採用的快照、已經被新版本取代的舊版）：不提供重試 */
  readOnly: boolean
  retryPending: boolean
  /** 問卷正在重新生成：這時不能重試 */
  retryDisabled?: boolean
  onRetry: () => void
  /** 重試送出失敗的訊息 */
  error?: ReactNode
}

export function TipsDetail({ knowledge, state, retry, readOnly, retryPending, retryDisabled = false, onRetry, error }: TipsDetailProps) {
  if (state === 'available') return <div className={styles.stack}><Cards items={knowledge.items} /></div>
  if (state === 'not_found') {
    return (
      <div className={styles.stack}>
        <p>這桌的食材目前查不到對應的小知識。</p>
        <p className={styles.hint}>菜單、份量與採用資格不受影響。</p>
      </div>
    )
  }
  const retrying = state === 'retrying'
  return (
    <div className={styles.stack}>
      <Notice tone="warn" title={retrying ? '小知識重試中…' : '小知識暫時拿不到'}>
        {knowledge.reason === 'BUDGET_EXHAUSTED'
          ? '本月外部服務額度不足，小知識暫時無法產生；'
          : '營養知識服務暫時無法使用；'}
        菜單、份量與採用資格不受影響。
        {!readOnly && knowledge.retry_available && '可以「只重試小知識」（不會重新生成菜單，結果存成新版本）。'}
        {retry?.status === 'failed' && ` 上次重試失敗：${retry.error?.message ?? '原因未知'}`}
      </Notice>
      {retrying && <Spinner label="正在重新產生小知識，完成後會自動換成新版本…" />}
      {error}
      {!readOnly && knowledge.retry_available && (
        <Button variant="primary" busy={retryPending} disabled={retrying || retryDisabled} onClick={onRetry}>
          {retrying ? '重試中…' : '只重試小知識'}
        </Button>
      )}
    </div>
  )
}
