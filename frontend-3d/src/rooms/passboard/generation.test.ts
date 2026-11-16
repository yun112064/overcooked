import { describe, expect, it } from 'vitest'
import type { Job } from '../../api/types'
import { clock, isActive, latestResultVersion, pollInterval, stageLabel, stageList, submitState } from './generation'

function job(overrides: Partial<Job> = {}): Job {
  return {
    id: 'j1', kind: 'meal_generation', status: 'running', stage: 'retrieving', attempts: 1, max_attempts: 3,
    meal_request_id: 'r1', input_version: 2, result_version: null, error: null, created_at: '', updated_at: '',
    ...overrides,
  }
}

const states = (j: Pick<Job, 'status' | 'stage'>) => Object.fromEntries(stageList(j).items.map((i) => [i.key, i.state]))

describe('stageList（鍋旁的階段列）', () => {
  it('排隊中：停在第一格，其餘都還沒開始', () => {
    const { items, note } = stageList({ status: 'queued', stage: 'queued' })
    expect(items.map((i) => i.key)).toEqual(['queued', 'retrieving', 'calculating', 'knowledge', 'result_ready'])
    expect(items[0]).toMatchObject({ label: '排隊中', state: 'current' })
    expect(items.slice(1).every((i) => i.state === 'pending')).toBe(true)
    expect(note).toBeNull()
  })

  it('執行中：後端回報的階段是目前，前面的算做完，後面的還沒開始', () => {
    expect(states({ status: 'running', stage: 'calculating' })).toEqual({
      queued: 'done', retrieving: 'done', calculating: 'current', knowledge: 'pending', result_ready: 'pending',
    })
  })

  it('「整理知識說明」查不到可用知識時會跳過：後端回報了才列，做完也不補一個假的勾', () => {
    expect(stageList({ status: 'running', stage: 'knowledge' }).items.some((i) => i.key === 'explaining')).toBe(false)
    expect(states({ status: 'running', stage: 'explaining' })).toEqual({
      queued: 'done', retrieving: 'done', calculating: 'done', knowledge: 'done', explaining: 'current', result_ready: 'pending',
    })
    expect(stageList({ status: 'succeeded', stage: 'result_ready' }).items.some((i) => i.key === 'explaining')).toBe(false)
  })

  it('不是每次都有的階段（AI 生成、檢查）只有後端回報正在做時才列出來', () => {
    expect(stageList({ status: 'running', stage: 'retrieving' }).items.some((i) => i.key === 'ai_generating')).toBe(false)
    const ai = stageList({ status: 'running', stage: 'ai_generating' })
    expect(ai.items.map((i) => i.key)).toEqual(['queued', 'retrieving', 'ai_generating', 'calculating', 'knowledge', 'result_ready'])
    expect(ai.items.find((i) => i.key === 'ai_generating')).toMatchObject({ label: 'AI 生成菜單中', state: 'current' })
    expect(stageList({ status: 'running', stage: 'validating_input' }).items[1]).toMatchObject({ key: 'validating_input', state: 'current' })
  })

  it('剛開始處理：排隊算做完，還沒有目前的階段', () => {
    const { items } = stageList({ status: 'running', stage: 'started' })
    expect(items.map((i) => i.state)).toEqual(['done', 'current', 'pending', 'pending', 'pending', 'pending'])
    expect(items[1].label).toBe('開始處理')
  })

  it('成功：全部做完', () => {
    expect(stageList({ status: 'succeeded', stage: 'result_ready' }).items.every((i) => i.state === 'done')).toBe(true)
  })

  it('排隊但後端說稍後重試：仍停在排隊，另外寫出原因', () => {
    const { items, note } = stageList({ status: 'queued', stage: 'retry_scheduled' })
    expect(items[0].state).toBe('current')
    expect(note).toBe('稍後自動重試')
  })

  it('不認得的階段：不猜進度，只把後端給的字寫出來', () => {
    const { items, note } = stageList({ status: 'running', stage: 'something_new' })
    expect(items.every((i) => i.state === 'pending')).toBe(true)
    expect(note).toBe('something_new')
    expect(stageList({ status: 'running', stage: null }).note).toBe('處理中')
  })

  it('失敗：不知道停在哪，沒有任何一格被點亮', () => {
    expect(stageList({ status: 'failed', stage: 'failed' }).items.every((i) => i.state === 'pending')).toBe(true)
  })
})

describe('stageLabel、isActive、clock', () => {
  it('階段名稱；不認得的階段顯示工作狀態', () => {
    expect(stageLabel({ stage: 'calculating', status: 'running' })).toBe('計算營養與價格')
    expect(stageLabel({ stage: null, status: 'queued' })).toBe('queued')
  })

  it('只有 queued／running 算進行中', () => {
    expect(isActive(job({ status: 'queued' }))).toBe(true)
    expect(isActive(job({ status: 'running' }))).toBe(true)
    expect(isActive(job({ status: 'succeeded' }))).toBe(false)
    expect(isActive(job({ status: 'failed' }))).toBe(false)
    expect(isActive(null)).toBe(false)
  })

  it('等待時間 m:ss', () => {
    expect(clock(0)).toBe('0:00')
    expect(clock(65)).toBe('1:05')
    expect(clock(600.9)).toBe('10:00')
    expect(clock(-3)).toBe('0:00')
  })
})

describe('pollInterval（要不要繼續問工作狀態）', () => {
  it('工作進行中：每 2 秒問一次', () => {
    expect(pollInterval(job({ status: 'queued' }), true)).toBe(2000)
    expect(pollInterval(job({ status: 'running' }), false)).toBe(2000)
  })

  it('工作已經結束：不問（問卷狀態還沒重讀回來也一樣）', () => {
    expect(pollInterval(job({ status: 'succeeded' }), true)).toBe(false)
    expect(pollInterval(job({ status: 'failed' }), false)).toBe(false)
  })

  it('讀到了，但這份問卷沒有工作（null）：不問', () => {
    expect(pollInterval(null, true)).toBe(false)
    expect(pollInterval(null, false)).toBe(false)
  })

  it('一次都還沒讀到（第一次讀取失敗）：問卷在排隊／生成中就繼續問，不然進度和取消都卡住', () => {
    expect(pollInterval(undefined, true)).toBe(2000)
  })

  it('一次都還沒讀到，問卷也沒有在煮：不問', () => {
    expect(pollInterval(undefined, false)).toBe(false)
  })
})

describe('submitState（能不能開始煮）', () => {
  const row = { status: 'draft', version: 2, complete: true }

  it('填完整、沒有工作 → 可以煮', () => {
    expect(submitState(row, null)).toMatchObject({ canSubmit: true, canRegenerate: false, submitLabel: '開始煮', own: null })
  })

  it('沒填完整 → 不能煮', () => {
    expect(submitState({ ...row, complete: false }, null).canSubmit).toBe(false)
  })

  it('生成中 → 不能再送', () => {
    const state = submitState({ ...row, status: 'running' }, job({ status: 'running' }))
    expect(state).toMatchObject({ active: true, canSubmit: false, canRegenerate: false })
  })

  it('這一版已經煮成功 → 不能再按開始煮，要用重新生成', () => {
    const state = submitState({ ...row, status: 'result_ready' }, job({ status: 'succeeded' }))
    expect(state).toMatchObject({ canSubmit: false, hasResult: true, canRegenerate: true })
  })

  it('這一版失敗 → 可以再煮一次', () => {
    const failed = job({ status: 'failed', error: { code: 'GENERATION_FAILED', message: '失敗' } })
    expect(submitState({ ...row, status: 'failed' }, failed)).toMatchObject({ canSubmit: true, submitLabel: '再煮一次' })
  })

  it('舊版本的工作不算這一版的', () => {
    const old = job({ status: 'succeeded', input_version: 1 })
    expect(submitState(row, old)).toMatchObject({ own: null, canSubmit: true, submitLabel: '開始煮' })
  })

  it('已採用：只能看，不能煮也不能重新生成', () => {
    expect(submitState({ ...row, status: 'confirmed' }, null)).toMatchObject({
      confirmed: true, hasResult: true, canSubmit: false, canRegenerate: false,
    })
  })

  it('「開始煮」擺不擺：草稿與失敗擺；這一版已有結果改用重新生成；已採用不擺', () => {
    expect(submitState({ status: 'draft', version: 2, complete: false }, null).showSubmit).toBe(true)
    expect(submitState({ status: 'draft', version: 2, complete: true }, null).showSubmit).toBe(true)
    expect(submitState({ status: 'failed', version: 2, complete: true }, job({ status: 'failed' })).showSubmit).toBe(true)
    // 生成中：擺著但不能按
    expect(submitState({ status: 'running', version: 2, complete: true }, job())).toMatchObject({ showSubmit: true, canSubmit: false })
    expect(submitState({ status: 'result_ready', version: 2, complete: true }, job({ status: 'succeeded' })).showSubmit).toBe(false)
    expect(submitState({ status: 'confirmed', version: 2, complete: true }, job({ status: 'succeeded' })).showSubmit).toBe(false)
    // 有結果但讀不到這一版的工作：後端仍接受送出，按鈕留著
    expect(submitState({ status: 'result_ready', version: 2, complete: true }, null)).toMatchObject({ showSubmit: true, canSubmit: true })
  })

  it('待決定也算有結果', () => {
    expect(submitState({ ...row, status: 'awaiting_decision' }, null).hasResult).toBe(true)
  })
})

describe('latestResultVersion（要帶到餐桌的版本）', () => {
  const list = (...versions: number[]) => versions.map((result_version) => ({ result_version }))

  it('清單裡有比生成工作更新的版本（補查價格、重試小知識）→ 用清單的', () => {
    expect(latestResultVersion(list(3, 2, 1), 1)).toBe(3)
  })

  it('清單還是舊的快取 → 用生成工作回報的', () => {
    expect(latestResultVersion(list(1), 2)).toBe(2)
  })

  it('只有其中一邊知道', () => {
    expect(latestResultVersion(undefined, 2)).toBe(2)
    expect(latestResultVersion(list(4), null)).toBe(4)
  })

  it('都不知道 → null（不猜是第 1 版）', () => {
    expect(latestResultVersion(undefined, null)).toBeNull()
    expect(latestResultVersion([], undefined)).toBeNull()
  })
})
