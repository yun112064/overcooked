/**
 * 畫框下的黃銅名牌打開的設定（DESIGN.md 5.8）：景深效果、換一個家、服務狀態、登出。
 * 換家與登出和右上角選單（src/shell/SessionMenu.tsx）是同一套行為。
 */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router'
import { api } from '../../api/client'
import { Button } from '../../components/Button'
import { ErrorNotice } from '../../components/Notice'
import { Panel } from '../../components/Panel'
import { Spinner } from '../../components/Spinner'
import { StatusChip, type ChipTone } from '../../components/StatusChip'
import { useDepth } from '../../depth/DepthProvider'
import { DEPTH_MODES, DEPTH_MODE_LABELS } from '../../depth/depthMode'
import { afterLogout } from '../../session/session'
import { teamStatusQuery, type ServiceState } from './queries'
import styles from './panels.module.css'

const SERVICE_LABEL: Record<ServiceState['status'], string> = { online: '在線', offline: '離線', not_configured: '未設定' }
const SERVICE_TONE: Record<ServiceState['status'], ChipTone> = { online: 'ok', offline: 'warn', not_configured: 'neutral' }

function Service({ name, state }: { name: string; state: ServiceState }) {
  return (
    <li>
      <span>{name}</span>
      {/* 後端回了不認得的狀態就寫未知，不當成在線 */}
      <StatusChip tone={SERVICE_TONE[state.status] ?? 'unknown'}>{SERVICE_LABEL[state.status] ?? '未知'}</StatusChip>
    </li>
  )
}

export function SettingsPanel({ householdName, onClose }: { householdName: string | undefined; onClose: () => void }) {
  const { mode, setMode } = useDepth()
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const team = useQuery(teamStatusQuery)

  const logout = useMutation({
    mutationFn: async () => api.post('/auth/logout'),
    // 不論伺服器有沒有回應，本機都清掉並回到門外
    onSettled: () => {
      afterLogout(queryClient)
      navigate('/login', { replace: true })
    },
  })

  return (
    <Panel
      title="設定" side="right" onClose={onClose} className={styles.wallRight}
      footer={(
        <>
          <Button onClick={() => navigate('/households')}>換一個家</Button>
          <Button busy={logout.isPending} onClick={() => logout.mutate()}>{logout.isPending ? '登出中…' : '登出'}</Button>
        </>
      )}
    >
      <div className={styles.stack}>
        <dl className={styles.facts}>
          <dt>目前的家</dt>
          <dd>{householdName ?? '（讀不到名稱）'}</dd>
        </dl>

        <div>
          <fieldset className={styles.segments}>
            <legend>景深效果</legend>
            {DEPTH_MODES.map((m) => (
              <label key={m}>
                <input type="radio" name="depth-mode" value={m} checked={mode === m} onChange={() => setMode(m)} />
                <span>{DEPTH_MODE_LABELS[m]}</span>
              </label>
            ))}
          </fieldset>
          <p className={styles.muted}>只記在這台裝置上。覺得暈或卡頓時，可以改成靜態景深或關閉。</p>
        </div>

        <section>
          <h3>服務狀態</h3>
          {team.isPending && <Spinner label="正在看服務狀態…" />}
          {team.isError && (
            <ErrorNotice error={team.error} fallback="讀不到服務狀態。" onRetry={() => team.refetch()} retrying={team.isFetching} />
          )}
          {team.data && (
            <>
              <ul className={styles.services}>
                {team.data.recognition && <Service name="蔬菜辨識" state={team.data.recognition} />}
                {team.data.knowledge && <Service name="營養知識" state={team.data.knowledge} />}
                {team.data.assistant && <Service name="飲食小幫手" state={team.data.assistant} />}
              </ul>
              {team.data.knowledge && team.data.knowledge.status !== 'online' && (
                <p className={styles.muted}>菜單照常生成，知識說明之後可單獨重試。</p>
              )}
            </>
          )}
        </section>
      </div>
    </Panel>
  )
}
