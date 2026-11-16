/**
 * 問卷用的小零件：欄位（標籤＋輸入＋說明或錯誤）、小標籤、可收合的段落。
 * 樣式在 Paper.module.css；放在紙上或抽屜裡都用同一份。
 */
import { useId, type ReactNode } from 'react'
import styles from './Paper.module.css'

export type ControlProps = {
  id: string
  'aria-describedby': string | undefined
  'aria-invalid': true | undefined
}

type FieldProps = {
  label: string
  hint?: ReactNode
  /** 有錯誤時取代說明文字，並以文字唸出來 */
  error?: ReactNode
  children: (control: ControlProps) => ReactNode
}

/** 一個欄位。children 收到 id 與 aria 屬性，展開到真正的 input／select 上，標籤與說明才連得起來。 */
export function Field({ label, hint, error, children }: FieldProps) {
  const id = useId()
  const noteId = `${id}-note`
  const note = error || hint
  return (
    <div className={styles.field}>
      <label htmlFor={id}>{label}</label>
      {children({ id, 'aria-describedby': note ? noteId : undefined, 'aria-invalid': error ? true : undefined })}
      {note && (
        <p id={noteId} className={error ? styles.fieldError : styles.fieldHint} role={error ? 'alert' : undefined}>{note}</p>
      )}
    </div>
  )
}

const TAG_TONES = {
  plain: styles.tagPlain,
  ok: styles.tagOk,
  warn: styles.tagWarn,
  danger: styles.tagDanger,
  unsure: styles.tagUnsure,
}

/** 小標籤（已加入、已略過、過敏…）。顏色只是輔助，內容一定是文字。 */
export function Tag({ tone = 'plain', children }: { tone?: keyof typeof TAG_TONES; children: ReactNode }) {
  return <span className={`${styles.tag} ${TAG_TONES[tone]}`}>{children}</span>
}

/** 可收合的段落（原生 details：鍵盤與讀屏都能操作）。 */
export function Fold({ title, aside, open, children }: { title: string; aside?: ReactNode; open?: boolean; children: ReactNode }) {
  return (
    <details className={styles.fold} open={open}>
      <summary><b>{title}</b>{aside && <small>{aside}</small>}</summary>
      <div className={styles.foldBody}>{children}</div>
    </details>
  )
}

/** 摘要用的一列：左邊小標題、右邊內容。放在 <dl> 裡。 */
export function Row({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className={styles.row}>
      <dt>{label}</dt>
      <dd>{children}</dd>
    </div>
  )
}
