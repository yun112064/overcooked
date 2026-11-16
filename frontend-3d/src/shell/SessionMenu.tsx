import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useEffect, useId, useRef, useState } from 'react'
import { useNavigate } from 'react-router'
import { api } from '../api/client'
import { useDepth } from '../depth/DepthProvider'
import { DEPTH_MODES, DEPTH_MODE_LABELS } from '../depth/depthMode'
import { afterLogout, useMe } from '../session/session'
import styles from './SessionMenu.module.css'

type SessionMenuProps = {
  /**
   * 目前的家庭名稱；屋內由 <SceneHud> 帶進來。
   * 不給＝人還在玄關（還沒選家，或回來換家）：開關寫「帳號」，選單裡列出登入的帳號，也沒有「換一個家」。
   * 玄關一定要有這個選單：剛註冊、還沒有家庭的人，否則找不到地方登出或換帳號。
   */
  householdName?: string
}

/**
 * 右上角的選單：景深效果、換一個家、登出。屋內由 <SceneHud> 放上去；玄關自己放（不帶 householdName）。
 * 全家福畫框下的名牌（DESIGN.md 5.8）也有同一組設定；這裡保留是為了每個房間都找得到登出。
 */
export function SessionMenu({ householdName }: SessionMenuProps) {
  const [open, setOpen] = useState(false)
  const wrapper = useRef<HTMLDivElement>(null)
  const toggle = useRef<HTMLButtonElement>(null)
  const { mode, setMode } = useDepth()
  const email = useMe().data?.user.email
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  // 全家福的設定面板也有一組景深的 radio；同名的 radio 會被瀏覽器當成同一組，所以各用各的名字
  const depthGroup = useId()

  const logout = useMutation({
    mutationFn: async () => api.post('/auth/logout'),
    // 不論伺服器有沒有回應，本機都清掉並回到門外
    onSettled: () => {
      afterLogout(queryClient)
      navigate('/login', { replace: true })
    },
  })

  // 點到選單外面就收起來：手機上它蓋著場景的右上角，不該等人再去按一次開關
  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      if (event.target instanceof Node && !wrapper.current?.contains(event.target)) setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    return () => document.removeEventListener('pointerdown', onPointerDown)
  }, [open])

  const label = householdName ?? '帳號'
  return (
    <div
      ref={wrapper} className={styles.menu} data-no-swipe
      // Escape 收起選單，焦點回到開關上；標成已處理，同一下 Escape 才不會連後面的面板或聊天室一起關
      onKeyDown={(event) => {
        if (event.key !== 'Escape' || !open) return
        event.preventDefault()
        setOpen(false)
        toggle.current?.focus()
      }}
      // 鍵盤走出選單也收起來。只看「焦點確實去了外面的某個東西」：
      // 有些瀏覽器用滑鼠按按鈕不會給焦點（relatedTarget 是空的），那種情況由上面的 pointerdown 處理
      onBlur={(event) => {
        const next = event.relatedTarget
        if (next instanceof Node && !event.currentTarget.contains(next)) setOpen(false)
      }}
    >
      <button
        ref={toggle} type="button" className={styles.toggle} aria-expanded={open}
        aria-label={`${label}，設定選單`} onClick={() => setOpen((v) => !v)}
      >
        <span className={styles.name}>{label}</span><span aria-hidden="true"> ▾</span>
      </button>
      {open && (
        <div className={styles.panel}>
          {!householdName && email && (
            <p className={styles.account}>登入的帳號<b>{email}</b></p>
          )}
          {/* 真的 radio：方向鍵換選項、讀屏念「三選一」，不用自己補鍵盤操作 */}
          <fieldset className={styles.segments}>
            <legend>景深效果</legend>
            {DEPTH_MODES.map((m) => (
              <label key={m}>
                <input type="radio" name={depthGroup} value={m} checked={mode === m} onChange={() => setMode(m)} />
                <span>{DEPTH_MODE_LABELS[m]}</span>
              </label>
            ))}
          </fieldset>
          {householdName && (
            <button type="button" className={styles.item} onClick={() => navigate('/households')}>換一個家</button>
          )}
          <button type="button" className={styles.item} disabled={logout.isPending} onClick={() => logout.mutate()}>
            {logout.isPending ? '登出中…' : '登出'}
          </button>
        </div>
      )}
    </div>
  )
}
