/**
 * 需要使用者回答才能繼續的確認對話框（刪除成員、取消生成、換一桌…）。
 * 用原生 <dialog> 的強制回應模式：焦點鎖在裡面、背景不能操作、Escape＝取消。
 *
 *   {asking && (
 *     <ConfirmDialog
 *       title="要刪除小米嗎？" confirmLabel="刪除" tone="danger"
 *       busy={remove.isPending} error={remove.isError ? errorMessage(remove.error) : undefined}
 *       onConfirm={() => remove.mutate()} onCancel={() => setAsking(false)}
 *     >
 *       刪除後無法復原。
 *     </ConfirmDialog>
 *   )}
 *
 * - 要顯示才掛上去；它不會自己關，成功後由外面卸下。
 * - 一打開焦點在「取消」上，免得誤按 Enter 就執行。
 * - busy 時兩顆按鈕都停用，Escape 也不會關（送出中不能假裝取消了）。
 * - error：失敗訊息，以文字顯示在按鈕上方。
 */
import { useEffect, useId, useRef, type ReactNode } from 'react'
import { Button } from './Button'
import styles from './ConfirmDialog.module.css'

type ConfirmDialogProps = {
  title: string
  /** 說明文字 */
  children?: ReactNode
  confirmLabel: string
  cancelLabel?: string
  tone?: 'default' | 'danger'
  busy?: boolean
  error?: ReactNode
  onConfirm: () => void
  onCancel: () => void
}

export function ConfirmDialog({
  title, children, confirmLabel, cancelLabel = '取消', tone = 'default', busy = false, error, onConfirm, onCancel,
}: ConfirmDialogProps) {
  const dialog = useRef<HTMLDialogElement>(null)
  const titleId = useId()
  const bodyId = useId()

  useEffect(() => {
    const element = dialog.current
    if (!element) return
    if (!element.open) element.showModal()
    return () => element.close()
  }, [])

  return (
    <dialog
      ref={dialog} className={styles.dialog} role="alertdialog"
      aria-labelledby={titleId} aria-describedby={children ? bodyId : undefined}
      // 瀏覽器預設 Escape 會直接關掉 <dialog>；改成交給外面決定
      onCancel={(event) => {
        event.preventDefault()
        if (!busy) onCancel()
      }}
      // 不讓 Escape 冒泡到外層的面板，否則會連面板一起關
      onKeyDown={(event) => { if (event.key === 'Escape') event.stopPropagation() }}
    >
      <h2 id={titleId}>{title}</h2>
      {children && <div id={bodyId} className={styles.body}>{children}</div>}
      {error && <p className={styles.error} role="alert">{error}</p>}
      <div className={styles.actions}>
        <Button disabled={busy} onClick={onCancel}>{cancelLabel}</Button>
        <Button variant={tone === 'danger' ? 'danger' : 'primary'} busy={busy} onClick={onConfirm}>{confirmLabel}</Button>
      </div>
    </dialog>
  )
}
