import { describe, expect, it } from 'vitest'
import type { Issue } from '../api/types'
import {
  draftSteps, latestGenerationJob, potFromSteps, potLabel, potState, stovePots,
  type PotJob, type PotRequest,
} from './potState'

const required = (field: string): Issue => ({ field, reason: 'REQUIRED', message: `請填 ${field}` })

/** 完整問卷（有 issues）；預設是剛建立、什麼都還沒填的草稿。 */
function detail(overrides: Partial<PotRequest> = {}): PotRequest {
  return {
    id: 'r1', status: 'draft', version: 1, complete: false,
    issues: ['meal_type', 'budget', 'staple', 'dish_count', 'participants'].map(required),
    draft: { available_ingredient_ids: [] },
    ...overrides,
  }
}

/** 清單摘要（沒有 issues）。 */
function summary(overrides: Partial<PotRequest> = {}): PotRequest {
  return { id: 'r1', status: 'draft', version: 1, meal_date: '2026-11-14', meal_type: 'dinner', ...overrides }
}

function job(overrides: Partial<PotJob> = {}): PotJob {
  return { kind: 'meal_generation', status: 'running', meal_request_id: 'r1', input_version: 1, error: null, ...overrides }
}

describe('potFromSteps', () => {
  it('0 步是空鍋、1–4 步半滿、5 步全滿', () => {
    expect(potFromSteps(0)).toEqual({ kind: 'empty' })
    expect(potFromSteps(1)).toEqual({ kind: 'filling', steps: 1 })
    expect(potFromSteps(4)).toEqual({ kind: 'filling', steps: 4 })
    expect(potFromSteps(5)).toEqual({ kind: 'full' })
  })

  it('超出範圍或不是整數時夾回合理的值', () => {
    expect(potFromSteps(-2)).toEqual({ kind: 'empty' })
    expect(potFromSteps(9)).toEqual({ kind: 'full' })
    expect(potFromSteps(2.9)).toEqual({ kind: 'filling', steps: 2 })
    expect(potFromSteps(Number.NaN)).toEqual({ kind: 'empty' })
  })
})

describe('draftSteps', () => {
  it('第一步（日期、餐別、預算）還有沒填的 → 0', () => {
    expect(draftSteps(detail())).toBe(0)
    expect(draftSteps(detail({ issues: [required('budget')] }))).toBe(0)
  })

  it('第一步填好、還沒選成員 → 1', () => {
    expect(draftSteps(detail({ issues: ['participants', 'staple', 'dish_count'].map(required) }))).toBe(1)
  })

  it('成員選好、沒有走過第三步的證據 → 2', () => {
    expect(draftSteps(detail({ issues: ['staple', 'dish_count'].map(required) }))).toBe(2)
  })

  it('選了現有食材就算走過第三步 → 3', () => {
    expect(draftSteps(detail({
      issues: [required('staple')], draft: { available_ingredient_ids: ['i1'] },
    }))).toBe(3)
  })

  it('第四步填好時，可略過的第三步也算過了 → 4', () => {
    expect(draftSteps(detail({ issues: [] }))).toBe(4)
  })

  it('填了但不合格（不是 REQUIRED）仍算填過，料留在鍋裡', () => {
    const tooLow: Issue = { field: 'budget', reason: 'BUDGET_TOO_LOW', message: '預算太低' }
    expect(draftSteps(detail({ issues: [tooLow] }))).toBe(4)
    expect(draftSteps(detail({ issues: [tooLow, required('participants')] }))).toBe(1)
  })

  it('後面的步驟先填好也不能跳過前面沒填的', () => {
    expect(draftSteps(detail({ issues: [required('meal_type')] }))).toBe(0)
  })

  it('清單摘要只能粗略判斷：日期與餐別都有 → 1，否則 0', () => {
    expect(draftSteps(summary())).toBe(1)
    expect(draftSteps(summary({ meal_type: null }))).toBe(0)
    expect(draftSteps(summary({ meal_date: null }))).toBe(0)
  })
})

describe('potState：沒有工作資訊時看問卷狀態', () => {
  it('沒有問卷 → 空鍋', () => {
    expect(potState({ request: null })).toEqual({ kind: 'empty' })
    expect(potState({ request: undefined })).toEqual({ kind: 'empty' })
  })

  it('已採用的那一餐已經上桌，鍋是空的', () => {
    expect(potState({ request: detail({ status: 'confirmed', complete: true, issues: [] }) })).toEqual({ kind: 'empty' })
    // 就算工作清單裡還留著它成功的工作
    expect(potState({
      request: summary({ status: 'confirmed' }), job: job({ status: 'succeeded' }),
    })).toEqual({ kind: 'empty' })
  })

  it('剛建立、第一步還沒填完的草稿 → 空鍋', () => {
    expect(potState({ request: detail() })).toEqual({ kind: 'empty' })
  })

  it('草稿依已完成步數半滿', () => {
    expect(potState({ request: detail({ issues: ['participants', 'staple'].map(required) }) }))
      .toEqual({ kind: 'filling', steps: 1 })
    expect(potState({ request: detail({ issues: [required('dish_count')] }) }))
      .toEqual({ kind: 'filling', steps: 2 })
  })

  it('填完整的草稿 → 全滿，等送出', () => {
    expect(potState({ request: detail({ complete: true, issues: [] }) })).toEqual({ kind: 'full' })
  })

  it('每一步都填了但有不合格的項目 → 停在 4，不會全滿', () => {
    const tooLow: Issue = { field: 'budget', reason: 'BUDGET_TOO_LOW', message: '預算太低' }
    expect(potState({ request: detail({ issues: [tooLow] }) })).toEqual({ kind: 'filling', steps: 4 })
  })

  it('queued／running → 蓋上冒煙', () => {
    expect(potState({ request: summary({ status: 'queued' }) })).toEqual({ kind: 'cooking' })
    expect(potState({ request: summary({ status: 'running' }) })).toEqual({ kind: 'cooking' })
  })

  it('result_ready／awaiting_decision → 開蓋全亮', () => {
    expect(potState({ request: summary({ status: 'result_ready' }) })).toEqual({ kind: 'ready' })
    expect(potState({ request: summary({ status: 'awaiting_decision' }) })).toEqual({ kind: 'ready' })
  })

  it('failed → 失敗', () => {
    expect(potState({ request: summary({ status: 'failed' }) })).toEqual({ kind: 'failed' })
  })

  it('不認得的狀態當成草稿處理，不猜它在煮或煮好了', () => {
    expect(potState({ request: summary({ status: 'something_new' }) })).toEqual({ kind: 'filling', steps: 1 })
  })

  it('清單摘要的草稿：只知道有沒有開始', () => {
    expect(potState({ request: summary() })).toEqual({ kind: 'filling', steps: 1 })
    expect(potState({ request: summary({ meal_type: null }) })).toEqual({ kind: 'empty' })
  })
})

describe('potState：單板傳入 stepsDone', () => {
  it('草稿階段以 stepsDone 為準', () => {
    expect(potState({ request: detail(), stepsDone: 3 })).toEqual({ kind: 'filling', steps: 3 })
    expect(potState({ request: detail({ complete: true, issues: [] }), stepsDone: 2 })).toEqual({ kind: 'filling', steps: 2 })
    expect(potState({ request: detail(), stepsDone: 5 })).toEqual({ kind: 'full' })
    expect(potState({ request: detail({ complete: true, issues: [] }), stepsDone: 0 })).toEqual({ kind: 'empty' })
  })

  it('不影響生成中、有結果、失敗這些狀態', () => {
    expect(potState({ request: summary({ status: 'running' }), stepsDone: 2 })).toEqual({ kind: 'cooking' })
    expect(potState({ request: summary({ status: 'result_ready' }), stepsDone: 2 })).toEqual({ kind: 'ready' })
    expect(potState({ request: summary({ status: 'failed' }), stepsDone: 2 })).toEqual({ kind: 'failed' })
    expect(potState({ request: null, stepsDone: 4 })).toEqual({ kind: 'empty' })
  })
})

describe('potState：有工作資訊時，工作比問卷新', () => {
  it('工作在排隊或執行 → 蓋上冒煙，即使問卷還停在草稿（剛送出、問卷還沒重新讀取）', () => {
    expect(potState({ request: detail({ complete: true, issues: [] }), job: job({ status: 'queued' }) })).toEqual({ kind: 'cooking' })
    expect(potState({ request: detail(), job: job({ status: 'running' }) })).toEqual({ kind: 'cooking' })
  })

  it('重新生成：問卷還顯示上一次的結果，但新的工作在跑 → 蓋上冒煙', () => {
    expect(potState({ request: summary({ status: 'result_ready' }), job: job({ status: 'running' }) })).toEqual({ kind: 'cooking' })
  })

  it('這一版的工作成功 → 開蓋全亮，即使問卷還停在生成中', () => {
    expect(potState({ request: summary({ status: 'running' }), job: job({ status: 'succeeded' }) })).toEqual({ kind: 'ready' })
  })

  it('這一版的工作失敗 → 失敗，即使問卷還停在生成中', () => {
    expect(potState({
      request: summary({ status: 'running' }),
      job: job({ status: 'failed', error: { code: 'GENERATION_FAILED', message: '生成失敗' } }),
    })).toEqual({ kind: 'failed' })
    // 沒有附錯誤內容的失敗也是失敗
    expect(potState({ request: summary({ status: 'queued' }), job: job({ status: 'failed' }) })).toEqual({ kind: 'failed' })
  })

  it('使用者自己取消不算失敗：問卷回草稿 → 料保留、全滿', () => {
    const cancelled = job({ status: 'failed', error: { code: 'CANCELLED', message: '已取消生成' } })
    expect(potState({ request: detail({ complete: true, issues: [] }), job: cancelled })).toEqual({ kind: 'full' })
  })

  it('取消後問卷還停在生成中（尚未重新讀取）→ 先回到全滿，不顯示還在煮', () => {
    const cancelled = job({ status: 'failed', error: { code: 'CANCELLED', message: '已取消生成' } })
    expect(potState({ request: summary({ status: 'running' }), job: cancelled })).toEqual({ kind: 'full' })
    expect(potState({ request: summary({ status: 'queued' }), job: cancelled, stepsDone: 4 })).toEqual({ kind: 'filling', steps: 4 })
  })

  it('取消重新生成：同一版先前有結果，問卷回到結果 → 開蓋全亮', () => {
    const cancelled = job({ status: 'failed', error: { code: 'CANCELLED', message: '已取消生成' } })
    expect(potState({ request: summary({ status: 'awaiting_decision' }), job: cancelled })).toEqual({ kind: 'ready' })
  })

  it('修改問卷後版本變了，舊版本的工作結果不再算數', () => {
    const edited = detail({ version: 2, issues: [required('staple')] })
    expect(potState({ request: edited, job: job({ status: 'succeeded', input_version: 1 }) })).toEqual({ kind: 'filling', steps: 2 })
    expect(potState({ request: edited, job: job({ status: 'failed', input_version: 1 }) })).toEqual({ kind: 'filling', steps: 2 })
  })

  it('別份問卷的工作不算', () => {
    expect(potState({ request: detail(), job: job({ status: 'running', meal_request_id: 'other' }) })).toEqual({ kind: 'empty' })
    expect(potState({ request: detail(), job: job({ status: 'running', meal_request_id: null }) })).toEqual({ kind: 'empty' })
  })

  it('小知識重試、查價重試不會讓鍋蓋回去', () => {
    expect(potState({
      request: summary({ status: 'result_ready' }), job: job({ kind: 'knowledge_retry', status: 'running' }),
    })).toEqual({ kind: 'ready' })
    expect(potState({
      request: summary({ status: 'result_ready' }), job: job({ kind: 'price_retry', status: 'failed' }),
    })).toEqual({ kind: 'ready' })
  })
})

describe('potLabel', () => {
  it('每個狀態都有文字', () => {
    expect(potLabel({ kind: 'empty' })).toBe('空鍋')
    expect(potLabel({ kind: 'filling', steps: 3 })).toBe('備料中 3/5')
    expect(potLabel({ kind: 'full' })).toBe('可以開始煮')
    expect(potLabel({ kind: 'cooking' })).toBe('生成中')
    expect(potLabel({ kind: 'ready' })).toBe('煮好了')
    expect(potLabel({ kind: 'failed' })).toBe('失敗')
  })
})

describe('latestGenerationJob', () => {
  it('清單是新到舊：取這份問卷第一筆生成工作，跳過其他種類與其他問卷', () => {
    const jobs = [
      job({ meal_request_id: 'r2', status: 'running' }),
      job({ kind: 'knowledge_retry', status: 'running' }),
      job({ status: 'succeeded' }),
      job({ status: 'failed' }),
    ]
    expect(latestGenerationJob(jobs, 'r1')).toBe(jobs[2])
    expect(latestGenerationJob(jobs, 'r2')).toBe(jobs[0])
    expect(latestGenerationJob(jobs, 'r9')).toBeNull()
  })
})

describe('stovePots', () => {
  it('沒有問卷：左邊一口空鍋（點了開新的一餐），右邊不放', () => {
    expect(stovePots([])).toEqual({ left: { request: null, state: { kind: 'empty' } }, right: null })
  })

  it('已採用的問卷不上爐', () => {
    expect(stovePots([summary({ status: 'confirmed' })])).toEqual({ left: { request: null, state: { kind: 'empty' } }, right: null })
  })

  it('左鍋有東西時，右邊放一口備用空鍋', () => {
    const draft = summary()
    expect(stovePots([draft])).toEqual({
      left: { request: draft, state: { kind: 'filling', steps: 1 } },
      right: { request: null, state: { kind: 'empty' } },
    })
  })

  it('左鍋是還沒開始填的草稿（空鍋）時，右邊不放', () => {
    const untouched = summary({ meal_type: null })
    expect(stovePots([untouched])).toEqual({ left: { request: untouched, state: { kind: 'empty' } }, right: null })
  })

  it('左爐口是最新一份還沒採用的問卷', () => {
    const adopted = summary({ id: 'a', status: 'confirmed' })
    const ready = summary({ id: 'b', status: 'awaiting_decision' })
    const older = summary({ id: 'c', status: 'failed' })
    const stove = stovePots([adopted, ready, older])
    expect(stove.left).toEqual({ request: ready, state: { kind: 'ready' } })
    expect(stove.right).toEqual({ request: null, state: { kind: 'empty' } })
  })

  it('同時有兩份在生成：右爐口是第二口蓋著的鍋，沒有備用空鍋', () => {
    const a = summary({ id: 'a', status: 'running' })
    const b = summary({ id: 'b', status: 'queued' })
    const c = summary({ id: 'c', status: 'queued' })
    expect(stovePots([a, b, c])).toEqual({
      left: { request: a, state: { kind: 'cooking' } },
      right: { request: b, state: { kind: 'cooking' } },
    })
  })

  it('最新的是草稿、另一份在生成：生成中的那口放右邊', () => {
    const draft = summary({ id: 'a' })
    const cooking = summary({ id: 'b', status: 'running' })
    expect(stovePots([draft, cooking])).toEqual({
      left: { request: draft, state: { kind: 'filling', steps: 1 } },
      right: { request: cooking, state: { kind: 'cooking' } },
    })
  })

  it('用工作清單補上問卷清單還沒反映的狀態', () => {
    const stale = summary({ id: 'a', status: 'draft' })
    const jobs = [job({ meal_request_id: 'a', status: 'queued' })]
    expect(stovePots([stale], jobs).left).toEqual({ request: stale, state: { kind: 'cooking' } })
  })

  it('食譜書挑菜的單還在挑時不上爐；送去生成之後才上爐', () => {
    const picking = summary({ id: 'p', mode: 'picker', meal_date: '2026-11-14', meal_type: 'dinner' })
    const draft = summary({ id: 'a' })
    expect(stovePots([picking]).left.request).toBeNull()
    expect(stovePots([picking, draft]).left.request).toBe(draft)
    const cooking = summary({ id: 'p', mode: 'picker', status: 'running' })
    expect(stovePots([cooking]).left).toEqual({ request: cooking, state: { kind: 'cooking' } })
  })
})
