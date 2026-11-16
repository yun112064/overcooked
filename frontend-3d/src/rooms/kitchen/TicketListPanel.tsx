/**
 * 「全部的單」面板：舊前端「我的問卷」頁的清單搬到這裡。
 * 單軌只掛得下最新幾張，其餘的單、每張單的版本，以及「新增問卷／從食譜庫選菜」兩個入口都在這裡，
 * 所以不管爐上有幾口鍋，隨時都能開新的一餐。
 */
import type { ReactNode } from 'react'
import type { MealRequestSummary } from '../../api/types'
import { Button } from '../../components/Button'
import { Notice } from '../../components/Notice'
import { Panel } from '../../components/Panel'
import { requestStatusInfo } from '../../components/requestStatus'
import { Spinner } from '../../components/Spinner'
import { StatusChip } from '../../components/StatusChip'
import { greeting, ticketText, todayTitle } from './kitchenLogic'
import styles from './KitchenScene.module.css'

type TicketListPanelProps = {
  /** 還沒讀到是 undefined */
  requests: readonly MealRequestSummary[] | undefined
  loading: boolean
  /** 讀取清單、開新單、查結果失敗時的訊息（已經是畫好的 <ErrorNotice>） */
  errors?: ReactNode
  householdName?: string
  memberNames?: readonly string[]
  /** 正在開新單或正在查結果：先不讓人再點 */
  busy: boolean
  creating: boolean
  onOpen: (request: MealRequestSummary) => void
  onCreate: () => void
  onPick: () => void
  onClose: () => void
}

export function TicketListPanel({
  requests, loading, errors, householdName, memberNames, busy, creating, onOpen, onCreate, onPick, onClose,
}: TicketListPanelProps) {
  return (
    <Panel
      title="我的問卷" onClose={onClose}
      footer={(
        <>
          <Button variant="primary" busy={creating} disabled={busy} onClick={onCreate}>{creating ? '開單中…' : '新增問卷'}</Button>
          <Button disabled={busy} onClick={onPick}>從食譜庫選菜</Button>
        </>
      )}
    >
      <p className={styles.heroDate}>{todayTitle()}</p>
      <p className={styles.heroTitle}>{householdName ? `${householdName}，` : ''}{greeting()}</p>
      <p className={styles.heroText}>填一份問卷，告訴我們誰要吃、預算多少、家裡有什麼，就能得到全家營養剛好的一餐。</p>
      {memberNames && memberNames.length > 0 && (
        <p className={styles.heroMembers}><span>一起吃飯的家人</span>{memberNames.join('、')}</p>
      )}

      {errors}
      {loading && <Spinner label="正在讀問卷…" />}

      {requests?.length === 0 && (
        <Notice title="規劃你家的第一餐">
          建立一份問卷，告訴我們誰要吃、預算多少、家裡有什麼，就能得到一份營養剛好的菜單。
        </Notice>
      )}

      {requests && requests.length > 0 && (
        <>
          <p className={styles.listHint}>點進問卷可以修改、生成菜單，或查看已採用的結果。</p>
          <ul className={styles.list}>
            {requests.map((request) => {
              const text = ticketText(request)
              const status = requestStatusInfo(request.status)
              return (
                <li key={request.id}>
                  <button type="button" disabled={busy} onClick={() => onOpen(request)}>
                    <span className={styles.rowDate}><b>{text.date}</b><small>{text.weekday}</small></span>
                    <span className={styles.rowMain}><b>{text.meal}</b><small>{text.note}</small></span>
                    <StatusChip tone={status.tone}>{status.label}</StatusChip>
                  </button>
                </li>
              )
            })}
          </ul>
        </>
      )}
    </Panel>
  )
}
