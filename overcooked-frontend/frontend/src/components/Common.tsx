import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { CircleHelp, CloudCheck, AlertCircle, X } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import type { SaveStatus } from '../domain/types.ts'

export function Help({ text, label, illustration = false }: { text: string; label: string; illustration?: boolean }) {
  const id = useId()
  const [position, setPosition] = useState<{ left: number; top?: number; bottom?: number } | null>(null)
  const button = useRef<HTMLButtonElement>(null)
  function open() {
    const rect = button.current!.getBoundingClientRect()
    const left = Math.max(12, Math.min(rect.left - 12, window.innerWidth - 352))
    setPosition({ left, ...(window.innerHeight - rect.bottom > 270 ? { top: rect.bottom + 7 } : { bottom: window.innerHeight - rect.top + 7 }) })
  }
  return <span className="help-wrap"><button ref={button} type="button" className="help-button" aria-label={label} aria-describedby={position ? id : undefined} onMouseEnter={open} onMouseLeave={() => setPosition(null)} onFocus={open} onBlur={() => setPosition(null)} onKeyDown={event => { if(event.key === 'Escape') setPosition(null) }} onClick={open}><CircleHelp size={14} /></button>{position && createPortal(<div id={id} role="tooltip" className="help-tooltip" style={position}>{text}{illustration && <PortionIllustration />}</div>, document.body)}</span>
}
function PortionIllustration() {
  return <div className="portion-illustration" aria-label="食量比例示意圖">{[0.7, 1, 1.3].map(n => <div key={n}><svg viewBox="0 0 70 70" role="img" aria-label={`${n}n，標準份量的 ${Math.round(n * 100)}%`}><circle cx="35" cy="35" r="32" fill="var(--paper)" stroke="var(--line)" /><circle cx="35" cy="35" r="27" fill="none" stroke="var(--line)" strokeDasharray="2 3" /><circle cx="35" cy="35" r={20 * Math.sqrt(n)} fill="var(--purple)" opacity=".8" /></svg><strong>{n}n</strong><span>{Math.round(n * 100)}%</span></div>)}</div>
}
export function Modal({ title, children, onClose }: { title: string; children: ReactNode; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null), titleId = useId()
  useEffect(() => { const dialog = ref.current!; dialog.showModal(); return () => dialog.close() }, [])
  return <dialog ref={ref} className="modal" aria-labelledby={titleId} onCancel={event => { event.preventDefault(); onClose() }}><div className="modal-heading"><h2 id={titleId}>{title}</h2><button type="button" className="icon-button" aria-label="關閉" onClick={onClose}><X size={19} /></button></div>{children}</dialog>
}
export function PageTitle({ title, icon: Icon, children }: { title: string; icon: LucideIcon; children?: ReactNode }) {
  return <div className="page-title"><span className="title-icon"><Icon size={23} /></span><h1>{title}</h1>{children}</div>
}
export function ErrorText({ children }: { children?: ReactNode }) { return children ? <p className="error" role="alert"><AlertCircle size={16} /> <span>{children}</span></p> : null }
export function SaveIndicator({ status, error, retry }: { status: SaveStatus; error: string; retry: () => void }) {
  return <div className="save-indicator" role="status"><CloudCheck size={16} /><span>{status === 'saved' ? '已保存' : status === 'saving' ? '保存中…' : error || '保存失敗'}</span>{status === 'failed' && <button type="button" className="text-button" onClick={retry}>重試</button>}</div>
}
export function Steps({ step }: { step: 1 | 2 }) { return <ol className="steps" aria-label="規劃步驟"><li aria-current={step === 1 ? 'step' : undefined} className={step === 1 ? 'active' : 'done'}><span>1</span>當餐條件</li><li aria-hidden="true">›</li><li aria-current={step === 2 ? 'step' : undefined} className={step === 2 ? 'active' : ''}><span>2</span>現有食材</li></ol> }
