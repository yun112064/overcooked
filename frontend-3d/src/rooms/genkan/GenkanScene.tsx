import { zodResolver } from '@hookform/resolvers/zod'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useForm } from 'react-hook-form'
import { useNavigate } from 'react-router'
import { z } from 'zod'
import { api, errorMessage } from '../../api/client'
import type { Household } from '../../api/types'
import plateArt from '../../assets/rooms/genkan/door-plate.svg'
import z0 from '../../assets/rooms/genkan/z0.svg'
import z1 from '../../assets/rooms/genkan/z1.svg'
import z2 from '../../assets/rooms/genkan/z2.svg'
import z3 from '../../assets/rooms/genkan/z3.svg'
import { Hotspot, Placed, Room, type HotspotItem } from '../../camera/Room'
import type { Stop } from '../../camera/framing'
import { useDepth } from '../../depth/DepthProvider'
import { afterHouseholdChange } from '../../session/session'
import { Mascot } from '../../shell/Mascot'
import { SessionMenu } from '../../shell/SessionMenu'
import styles from './GenkanScene.module.css'

const LAYERS = [z0, z1, z2, z3]
const STOPS: Stop[] = [
  // 四張門牌橫跨 294–906，長螢幕手機蓋滿畫面時看不全，所以指定要看到的寬度
  { id: 'plates', label: '門牌', fx: 0.375, span: 680 },
  { id: 'inner', label: '內門', fx: 0.78 },
]
// 木軌上四個銅鉤的位置（畫稿座標）；前三個掛家庭，最後一個掛「新門牌」
const HOOK_X = [360, 520, 680, 840]
const PLATE_Y = 180
const PLATE_W = 132
const MAX_PLATES = HOOK_X.length - 1
const CAMERA_MS = 600

type HouseholdList = { households: Household[]; current_household_id: string | null }

const schema = z.object({ name: z.string().trim().min(1, '請輸入家庭名稱').max(100, '最多 100 個字') })
type FormValues = z.infer<typeof schema>

/** 玄關：木軌上的每張門牌是一個家庭，選好之後鏡頭走到內門、進客廳（DESIGN.md 5.0）。 */
export default function GenkanScene() {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const { mode } = useDepth()
  const [stop, setStop] = useState('plates')
  const [panelOpen, setPanelOpen] = useState(false)
  /** 正在走向內門；值是進門後要去的房間 */
  const [entering, setEntering] = useState<string | null>(null)

  const list = useQuery({
    queryKey: ['households'],
    queryFn: async () => (await api.get<HouseholdList>('/household')).data,
  })
  const { register, handleSubmit, reset, formState: { errors } } = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: { name: '' },
  })

  // 選好或建立好家庭：沉浸模式先把鏡頭帶到內門再進屋，其餘模式直接進去
  const enter = async (to: string) => {
    await afterHouseholdChange(queryClient)
    setPanelOpen(false)
    if (mode !== 'immersive') {
      navigate(to)
      return
    }
    setStop('inner')
    setEntering(to)
  }
  useEffect(() => {
    if (!entering) return
    const timer = window.setTimeout(() => navigate(entering), CAMERA_MS)
    return () => window.clearTimeout(timer)
  }, [entering, navigate])

  const select = useMutation({
    mutationFn: async (id: string) => (await api.post<Household>('/household/select', { household_id: id })).data,
    onSuccess: () => enter('/today'),
  })
  const create = useMutation({
    mutationFn: async (values: FormValues) => (await api.post<Household>('/household', values)).data,
    // 剛掛上的門牌家裡還沒有人：先到全家福把家人畫上去（舊前端建立家庭後也是先到成員頁），
    // 不然要到問卷第 2 步才會發現沒有成員可以選
    onSuccess: async () => { reset(); await enter('/family') },
  })

  const busy = select.isPending || create.isPending || entering !== null
  const households = list.data?.households ?? []
  const currentId = list.data?.current_household_id ?? null
  // 上次進的那一家排最前面，確保它一定掛在木軌上
  const ordered = [...households].sort((a, b) => Number(b.id === currentId) - Number(a.id === currentId))
  const onRail = ordered.slice(0, MAX_PLATES)
  const hidden = ordered.length - onRail.length

  const hotspots: HotspotItem[] = [
    // 物件列和木軌上的門牌是同一個動作，忙的時候（送出中、正在進門）一樣不能再按，否則會再選一次家
    ...onRail.map((h) => ({
      id: h.id, label: h.name, status: h.id === currentId ? '上次進這家' : undefined, stop: 'plates',
      disabled: busy, onActivate: () => select.mutate(h.id),
    })),
    {
      id: 'new', label: hidden > 0 ? `其他 ${hidden} 個家・新門牌` : '＋ 掛新門牌', stop: 'plates',
      disabled: busy, onActivate: () => setPanelOpen(true),
    },
  ]

  return (
    <Room
      name="玄關" layers={LAYERS} stops={STOPS} stopId={stop} onStopChange={setStop} hotspots={hotspots}
      hud={(
        <>
          {/* 還沒有家庭的人也要能登出或換帳號（屋內的選單由 SceneHud 放，玄關沒有 SceneHud） */}
          <SessionMenu />
          <Mascot message="開門後就能問我" />
          {panelOpen && (
            <div
              className={styles.panel} role="dialog" aria-label="門牌" data-no-swipe
              // preventDefault：標成已處理，同一下 Escape 不會連別的浮動介面（減少動態的提示）一起關
              onKeyDown={(event) => { if (event.key === 'Escape') { event.preventDefault(); setPanelOpen(false) } }}
            >
              <button type="button" className={styles.close} aria-label="關閉" onClick={() => setPanelOpen(false)}>✕</button>
              {hidden > 0 && (
                <>
                  <h2>所有的家</h2>
                  <ul className={styles.all}>
                    {ordered.map((h) => (
                      <li key={h.id}>
                        <button type="button" disabled={busy} onClick={() => select.mutate(h.id)}>
                          {h.name}{h.id === currentId && <small>上次進這家</small>}
                        </button>
                      </li>
                    ))}
                  </ul>
                </>
              )}
              <h2>掛一塊新門牌</h2>
              <form noValidate onSubmit={handleSubmit((values) => create.mutate(values))}>
                <label className={styles.field}>
                  <span>家庭名稱</span>
                  {/* 對話框一開就把游標放在唯一的欄位 */}
                  <input autoFocus placeholder="王家" aria-invalid={!!errors.name} {...register('name')} />
                  {errors.name && <em>{errors.name.message}</em>}
                </label>
                {create.isError && <p className={styles.alert} role="alert">{errorMessage(create.error)}</p>}
                <button type="submit" className={styles.primary} disabled={busy}>
                  {create.isPending ? '建立中…' : '建立並進門'}
                </button>
              </form>
            </div>
          )}
        </>
      )}
    >
      <Placed x={584} y={52} k={0.15} className={styles.wallTitle}>
        {list.isPending ? '正在找門牌…' : households.length === 0 ? '還沒有門牌，先掛一塊吧' : '歡迎回家，要進哪個家？'}
      </Placed>

      {(list.isError || select.isError) && (
        <Placed x={584} y={104} k={0.15} className={styles.wallAlert}>
          <span role="alert">{errorMessage(list.error ?? select.error)}</span>
          {list.isError && <button type="button" onClick={() => list.refetch()}>再試一次</button>}
        </Placed>
      )}

      {onRail.map((h, i) => (
        <Hotspot
          key={h.id} id={h.id} label={`進入${h.name}`} x={HOOK_X[i]} y={PLATE_Y} width={PLATE_W} k={0.4}
          className={styles.plate} style={{ backgroundImage: `url("${plateArt}")` }}
          disabled={busy} onClick={() => select.mutate(h.id)}
        >
          <span className={styles.plateText}>
            <b>{h.name}</b>
            {h.id === currentId && <em>上次進這家</em>}
          </span>
        </Hotspot>
      ))}

      {!list.isPending && !list.isError && (
        <Hotspot
          id="new" label="掛一塊新門牌" x={HOOK_X[onRail.length]} y={PLATE_Y} width={PLATE_W} k={0.4}
          className={`${styles.plate} ${styles.empty}`} disabled={busy} onClick={() => setPanelOpen(true)}
        >
          <svg viewBox="0 0 160 220" aria-hidden="true">
            <path d="M10 104 L80 50 L150 104 V198 q0 8 -8 8 H18 q-8 0 -8 -8Z" />
          </svg>
          <span className={styles.plateText}>
            <b>＋</b>
            <i>{hidden > 0 ? `其他 ${hidden} 個家` : '掛新門牌'}</i>
          </span>
        </Hotspot>
      )}
    </Room>
  )
}
