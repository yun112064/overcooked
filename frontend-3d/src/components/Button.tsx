/**
 * 浮動介面（面板、對話框、動作列）用的按鈕，高度至少 44px。
 * 畫在場景物件上的按鈕（紙上的、便利貼上的）請照那個物件的樣子自己畫，不要用這個。
 *
 *   <Button variant="primary" busy={save.isPending} onClick={…}>採用這桌</Button>
 *
 * variant：primary 主要動作（磚紅）／secondary 一般（預設）／ghost 不搶眼的文字按鈕／danger 刪除這類無法復原的動作。
 * busy：送出中——按鈕停用並標 aria-busy；文字請自己換成「…中」。
 * 其餘屬性和原生 <button> 相同；type 預設 'button'（表單送出要自己寫 type="submit"）。
 */
import type { ButtonHTMLAttributes } from 'react'
import styles from './Button.module.css'

type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
  busy?: boolean
}

export function Button({ variant = 'secondary', busy = false, type = 'button', className, disabled, children, ...rest }: ButtonProps) {
  return (
    <button
      {...rest} type={type} disabled={disabled || busy} aria-busy={busy || undefined}
      className={`${styles.button} ${styles[variant]} ${className ?? ''}`}
    >
      {children}
    </button>
  )
}
