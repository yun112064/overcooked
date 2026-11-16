/**
 * 全家福（/family，DESIGN.md 5.8）：畫框裡的蠟筆人物就是家庭成員。
 * 行為從舊前端的 MembersPage＋MemberFormDialog 搬過來（同樣的端點、內容、query key 與版本檢查）。
 *
 * - 點人物 → 左邊面板看資料，可以編輯或刪除；虛線人形 → 新增。
 * - 圖畫紙上方的標題 → 全家名單與統計（畫裡排不下的成員也從這裡進去）。
 * - 畫框下的黃銅名牌 → 設定（景深、換家、服務狀態、登出）。
 * 一次只開一個面板：手機上面板是下方抽屜，疊兩個會互相蓋住。
 */
import { useMutation, useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { api, apiError, errorMessage, errorStatus } from '../../api/client'
import z0 from '../../assets/rooms/portrait/z0.svg'
import z1 from '../../assets/rooms/portrait/z1.svg'
import z2 from '../../assets/rooms/portrait/z2.svg'
import z3 from '../../assets/rooms/portrait/z3.svg'
import { Hotspot, Placed, Room, type HotspotItem } from '../../camera/Room'
import type { Stop } from '../../camera/framing'
import { Button } from '../../components/Button'
import { ConfirmDialog } from '../../components/ConfirmDialog'
import { HudNote } from '../../components/HudNote'
import { ErrorNotice, Notice } from '../../components/Notice'
import { Panel } from '../../components/Panel'
import { Spinner } from '../../components/Spinner'
import { membersQuery } from '../../session/queries'
import { useMe } from '../../session/session'
import { SceneHud } from '../../shell/SceneHud'
import { MemberDetail } from './MemberDetail'
import { MemberForm } from './MemberForm'
import {
  MAX_DRAWN, crayonKind, figureBox, figureScale, layoutPeople, memberSummary, summaryText,
} from './people'
import { memberOptionsQuery, memberTargetQuery, targetUnavailable } from './queries'
import { RosterPanel } from './RosterPanel'
import { SettingsPanel } from './SettingsPanel'
import { CRAYON, CRAYON_ADD } from './sprites'
import panelStyles from './panels.module.css'
import styles from './PortraitScene.module.css'

const LAYERS = [z0, z1, z2, z3]
const STOP = 'portrait'
// 圖畫紙是 510–1090；手機要整張紙都看得到（左右再帶一點襯紙），所以指定要看到的寬度
const STOPS: Stop[] = [{ id: STOP, label: '全家福', fx: 0.5, span: 620 }]
// 畫稿座標。名牌 700–900 × 742–782：y 給中心，樣式再往上移半個高度（手機上要撐到 44px 高）
const PLATE = { x: 800, y: 762, width: 200 }
// 圖畫紙左上的天空，蠟筆寫的標題
const TITLE = { x: 690, y: 188 }
/** 圖畫紙在 z1；疊上去的東西跟著同一層移動 */
const K = 0.4
/** 一格的可點範圍比格子窄一點，相鄰兩人的焦點框才不會黏在一起 */
const SLOT_GAP = 4
/** 「畫上新成員」的虛線人形比大人小一點 */
const ADD_SCALE = 0.84

type View =
  | { kind: 'none' }
  | { kind: 'member'; id: string }
  | { kind: 'edit'; id: string }
  | { kind: 'add' }
  | { kind: 'roster' }
  | { kind: 'settings' }

const CLOSED: View = { kind: 'none' }

export default function PortraitScene() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const householdName = useMe().data?.current_household?.name
  const [view, setView] = useState<View>(CLOSED)
  const [deletingId, setDeletingId] = useState<string | null>(null)

  const members = useQuery(membersQuery)
  const options = useQuery(memberOptionsQuery)
  const list = members.data ?? []
  // 每位成員的每日基準：只用來標出「後端說算不出來」的人；讀不到（連線問題）就不標，不亂猜
  const targets = useQueries({ queries: list.map((member) => memberTargetQuery(member)) })
  const unavailable = new Set(
    list.filter((_, i) => targets[i]?.isError && targetUnavailable(targets[i].error)).map((member) => member.id),
  )

  // 登入過期或還沒選家庭（例如伺服器重啟過）：回大門或玄關，不停在一個什麼都讀不到的房間
  const sessionGone = errorStatus(members.error) === 401
  const noHousehold = apiError(members.error)?.code === 'HOUSEHOLD_NOT_SELECTED'
  useEffect(() => {
    // 重新問「我是誰」；問到沒登入，外層的路由會帶回大門
    if (sessionGone) void queryClient.invalidateQueries({ queryKey: ['me'] })
    else if (noHousehold) navigate('/households', { replace: true })
  }, [sessionGone, noHousehold, queryClient, navigate])

  const remove = useMutation({
    mutationFn: async (id: string) => api.delete(`/members/${id}`),
    onSuccess: async () => {
      setDeletingId(null)
      setView(CLOSED)
      await queryClient.invalidateQueries({ queryKey: membersQuery.queryKey })
    },
    // 已經被別人刪掉了：重新讀清單，畫面才會跟上
    onError: (err) => {
      if (apiError(err)?.code === 'NOT_FOUND') void queryClient.invalidateQueries({ queryKey: membersQuery.queryKey })
    },
  })

  const selected = view.kind === 'member' || view.kind === 'edit' ? list.find((m) => m.id === view.id) : undefined
  const deleting = deletingId ? list.find((m) => m.id === deletingId) : undefined
  const loaded = members.data !== undefined
  const drawn = list.slice(0, MAX_DRAWN)
  const hiddenCount = list.length - drawn.length
  // 最後一格是「畫上新成員」
  const slots = layoutPeople(drawn.length + 1)
  const addSlot = slots[drawn.length]
  const addBox = figureBox(addSlot, ADD_SCALE)

  const openMember = (id: string) => setView({ kind: 'member', id })
  const openAdd = () => setView({ kind: 'add' })
  const openSettings = () => setView({ kind: 'settings' })
  const openRoster = () => setView({ kind: 'roster' })
  const close = () => setView(CLOSED)

  const settingsItem: HotspotItem = { id: 'settings', label: '設定', status: householdName, stop: STOP, onActivate: openSettings }
  const hotspots: HotspotItem[] = loaded
    ? [
        ...list.map((member) => ({
          id: member.id, label: member.display_name, stop: STOP,
          status: unavailable.has(member.id) ? '目標算不出來' : undefined,
          onActivate: () => openMember(member.id),
        })),
        { id: 'add', label: '＋ 新成員', stop: STOP, onActivate: openAdd },
        ...(list.length > 0
          ? [{ id: 'roster', label: '全家名單', status: `${list.length} 人`, stop: STOP, onActivate: openRoster }]
          : []),
        settingsItem,
      ]
    : [settingsItem]

  const formOpen = view.kind === 'add' || (view.kind === 'edit' && !!selected)
  const formTitle = view.kind === 'add' ? '新增成員' : '編輯成員'

  return (
    <Room
      name="全家福" layers={LAYERS} stops={STOPS} hotspots={hotspots}
      hud={(
        <SceneHud tab="family">
          <HudNote>
            {members.isPending && <Spinner label="正在看畫裡有誰…" />}
            {members.isError && !sessionGone && !noHousehold && (
              <ErrorNotice error={members.error} onRetry={() => members.refetch()} retrying={members.isFetching} />
            )}
            {/* 表單開著時，同一個錯誤已經寫在表單的面板裡 */}
            {options.isError && !formOpen && (
              <ErrorNotice
                error={options.error} fallback="讀不到成員表單的選項。"
                onRetry={() => options.refetch()} retrying={options.isFetching}
              />
            )}
            {loaded && list.length === 0 && view.kind === 'none' && (
              <Notice title="先認識一下你的家人" action={<Button variant="primary" onClick={openAdd}>新增成員</Button>}>
                新增成員後，規劃餐食時就能選擇誰要用餐，系統會依每個人的需要分配份量。
              </Notice>
            )}
          </HudNote>

          {view.kind === 'member' && selected && (
            <MemberDetail
              member={selected} onClose={close}
              onEdit={() => setView({ kind: 'edit', id: selected.id })}
              onDelete={() => { remove.reset(); setDeletingId(selected.id) }}
            />
          )}

          {formOpen && (options.data ? (
            <MemberForm
              member={view.kind === 'edit' ? selected ?? null : null} options={options.data}
              // 編輯是從成員資料進來的，取消或存好都回到那裡；新增則回到畫
              onClose={() => setView(view.kind === 'edit' ? { kind: 'member', id: view.id } : CLOSED)}
              onSaved={(saved) => setView(view.kind === 'edit' ? { kind: 'member', id: saved.id } : CLOSED)}
            />
          ) : (
            // 表單的選項（食量、活動量、目標與年齡規則）來自後端，沒讀到之前不給填
            <Panel title={formTitle} side="left" onClose={close} className={panelStyles.wallLeft}>
              {options.isError ? (
                <ErrorNotice
                  error={options.error} fallback="讀不到成員表單的選項。"
                  onRetry={() => options.refetch()} retrying={options.isFetching}
                />
              ) : <Spinner label="正在準備表單…" />}
            </Panel>
          ))}

          {view.kind === 'edit' && !selected && loaded && (
            <Panel title="編輯成員" side="left" onClose={close} className={panelStyles.wallLeft}>
              <Notice tone="warn">找不到這位成員，可能已經被刪除了。</Notice>
            </Panel>
          )}

          {view.kind === 'roster' && (
            <RosterPanel members={list} unavailable={unavailable} onOpen={openMember} onAdd={openAdd} onClose={close} />
          )}

          {view.kind === 'settings' && <SettingsPanel householdName={householdName} onClose={close} />}

          {deleting && (
            <ConfirmDialog
              title="刪除成員" confirmLabel={remove.isPending ? '刪除中…' : '刪除'} tone="danger"
              busy={remove.isPending} error={remove.isError ? errorMessage(remove.error) : undefined}
              onConfirm={() => remove.mutate(deleting.id)} onCancel={() => setDeletingId(null)}
            >
              確定要刪除「{deleting.display_name}」嗎？刪除後無法復原。
            </ConfirmDialog>
          )}
        </SceneHud>
      )}
    >
      {loaded && list.length > 0 && (
        <Hotspot
          id="roster" label={`看全家名單與統計，目前 ${list.length} 人`} {...TITLE} k={K}
          className={`${styles.title} ${styles.titleButton}`} onClick={openRoster}
        >
          <b>我們這一家<i aria-hidden="true"> ›</i></b>
          <span>{list.length} 個人{hiddenCount > 0 && `・還有 ${hiddenCount} 位在名單裡`}</span>
        </Hotspot>
      )}
      {loaded && list.length === 0 && (
        <Placed {...TITLE} k={K} className={styles.title}>
          <b>我們這一家</b>
          <span>還沒有人，先畫上第一位吧</span>
        </Placed>
      )}

      {drawn.map((member, i) => {
        const slot = slots[i]
        const box = figureBox(slot, figureScale(member.age_years))
        const pieces = memberSummary(member, unavailable.has(member.id))
        const width = slot.width - SLOT_GAP
        const state = member.id === deletingId ? styles.erasing : selected?.id === member.id ? styles.selected : ''
        return (
          <Hotspot
            key={member.id} id={member.id} label={`${member.display_name}，${summaryText(pieces)}。看資料`}
            x={slot.x} y={box.top} width={width} k={K}
            className={`${styles.person} ${state}`} onClick={() => openMember(member.id)}
            aria-pressed={selected?.id === member.id}
          >
            <img
              className={styles.figure} style={{ width: `${(box.width / width) * 100}%` }}
              src={CRAYON[crayonKind(member)]} alt="" draggable={false}
            />
            {/* 後排沒有踩在草地上，腳下補一筆蠟筆的草 */}
            {slot.row === 'back' && (
              <svg className={styles.mound} viewBox="0 0 100 12" aria-hidden="true">
                <path d="M4 8 q8 -9 16 0 t16 0 t16 0 t16 0 t16 0 t12 0" />
              </svg>
            )}
            <span className={styles.caption}>
              <b>{member.display_name}</b>
              <span className={styles.summary}>
                {pieces.map((piece) => (
                  <span key={piece.text} className={piece.missing ? styles.pencil : undefined}>{piece.text}</span>
                ))}
              </span>
            </span>
          </Hotspot>
        )
      })}

      {loaded && (
        <Hotspot
          id="add" label="畫上新成員" x={addSlot.x} y={addBox.top} width={addSlot.width - SLOT_GAP} k={K}
          className={`${styles.person} ${styles.addPerson} ${view.kind === 'add' ? styles.selected : ''}`} onClick={openAdd}
          aria-pressed={view.kind === 'add'}
        >
          <img
            className={styles.figure} style={{ width: `${(addBox.width / (addSlot.width - SLOT_GAP)) * 100}%` }}
            src={CRAYON_ADD} alt="" draggable={false}
          />
          <span className={styles.caption}><b>畫上新成員</b></span>
        </Hotspot>
      )}

      <Hotspot
        id="settings" label={`${householdName ?? '這個家'}的名牌，打開設定`} {...PLATE} k={K}
        className={styles.plate} onClick={openSettings}
      >
        <span className={styles.plateName}>{householdName ?? '設定'}</span>
        <span className={styles.tag}>名牌・設定</span>
      </Hotspot>
    </Room>
  )
}
