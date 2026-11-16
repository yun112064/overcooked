import { describe, expect, it } from 'vitest'
import type { AssistantReference } from '../api/assistant'
import {
  HISTORY_TURNS, answerBadge, buildHistory, citedReferences, formatDate, offlineText, openaiOnline, placeWindow,
  referenceByline, settledId, splitCitations, type TeamStatus,
} from './chatLogic'
import type { Message } from './chatStore'

const message = (id: number, patch: Partial<Message> = {}): Message =>
  ({ id, role: id % 2 ? 'user' : 'assistant', text: `第 ${id} 則`, status: 'done', ...patch })

const reference = (n: number, patch: Partial<AssistantReference> = {}): AssistantReference =>
  ({ n, title: `文章 ${n}`, url: `https://example.com/${n}`, source_name: '健康醫療網', ...patch })

describe('offlineText', () => {
  const online = { status: 'online', reason: null } as const

  it('在線或還不知道狀態時不擋使用者', () => {
    expect(offlineText(undefined, 'local')).toBeNull()
    expect(offlineText({}, 'local')).toBeNull()
    expect(offlineText({ assistant: online }, 'local')).toBeNull()
  })

  it('依離線原因給說明，沒見過的原因用通用說明', () => {
    const team = (reason: string | null): TeamStatus => ({ assistant: { status: 'offline', reason } })
    expect(offlineText(team('NOT_CONFIGURED'), 'local')).toBe('飲食小幫手目前未啟用。')
    expect(offlineText(team('SERVICE_NOT_READY'), 'local')).toBe('飲食小幫手還沒準備好（模型或資料庫未啟動）。')
    expect(offlineText(team('AUTH_FAILED'), 'local')).toBe('飲食小幫手驗證失敗，請通知管理者。')
    expect(offlineText(team('UNREACHABLE'), 'local')).toBe('飲食小幫手目前離線（團隊電腦未開機或服務未啟動），請稍後再試。')
    expect(offlineText(team(null), 'local')).toBe('飲食小幫手目前離線（團隊電腦未開機或服務未啟動），請稍後再試。')
  })

  it('只看目前選用的那個模型', () => {
    const team: TeamStatus = { assistant: { status: 'offline', reason: null }, assistant_openai: online }
    expect(offlineText(team, 'openai')).toBeNull()
    expect(offlineText(team, 'local')).not.toBeNull()
  })

  it('OpenAI 版要在線才能選', () => {
    expect(openaiOnline(undefined)).toBe(false)
    expect(openaiOnline({ assistant_openai: { status: 'not_configured', reason: 'NOT_CONFIGURED' } })).toBe(false)
    expect(openaiOnline({ assistant_openai: online })).toBe(true)
  })
})

describe('buildHistory', () => {
  it('只帶已完成且有內容的訊息', () => {
    const history = buildHistory([
      message(1), message(2),
      message(3), message(4, { status: 'error', text: '回答到一半' }),
      message(5), message(6, { status: 'stopped' }),
      message(7), message(8, { status: 'streaming', text: '' }),
      message(9), message(10, { text: '' }),
    ])
    expect(history.map((turn) => turn.content)).toEqual(['第 1 則', '第 2 則', '第 3 則', '第 5 則', '第 7 則', '第 9 則'])
    expect(history[0]).toEqual({ role: 'user', content: '第 1 則' })
  })

  it('最多帶最近幾則，每則最多 2000 字', () => {
    const many = Array.from({ length: 10 }, (_, i) => message(i + 1))
    const history = buildHistory(many)
    expect(history).toHaveLength(HISTORY_TURNS)
    expect(history.at(-1)?.content).toBe('第 10 則')
    expect(buildHistory([message(1, { text: '字'.repeat(2500) })])[0].content).toHaveLength(2000)
  })
})

describe('settledId', () => {
  it('最後一則還在生成就不算', () => {
    expect(settledId([])).toBe(0)
    expect(settledId([message(1), message(2, { status: 'streaming' })])).toBe(0)
    expect(settledId([message(1), message(2)])).toBe(2)
    expect(settledId([message(1), message(2, { status: 'error' })])).toBe(2)
    expect(settledId([message(1), message(2, { status: 'stopped' })])).toBe(2)
  })
})

describe('splitCitations', () => {
  const refs = [reference(1), reference(3)]

  it('對得上參考資料的 [n] 才是引用', () => {
    expect(splitCitations('地瓜有膳食纖維[1]。[2]', refs)).toEqual([
      { kind: 'text', text: '地瓜有膳食纖維' },
      { kind: 'cite', n: 1, joined: false, followed: false },
      { kind: 'text', text: '。' },
      { kind: 'text', text: '[2]' },
    ])
  })

  it('連續的引用標成接續，中間隔了字就重新算', () => {
    expect(splitCitations('好處很多[1][3]，還有[3]', refs)).toEqual([
      { kind: 'text', text: '好處很多' },
      { kind: 'cite', n: 1, joined: false, followed: true },
      { kind: 'cite', n: 3, joined: true, followed: false },
      { kind: 'text', text: '，還有' },
      { kind: 'cite', n: 3, joined: false, followed: false },
    ])
  })

  it('三個連在一起時，中間那個兩側都緊鄰引用', () => {
    expect(splitCitations('[1][3][1]', refs)).toEqual([
      { kind: 'cite', n: 1, joined: false, followed: true },
      { kind: 'cite', n: 3, joined: true, followed: true },
      { kind: 'cite', n: 1, joined: true, followed: false },
    ])
  })

  it('對不上參考資料的 [n] 是普通文字，會把前後的引用隔開', () => {
    expect(splitCitations('[1][2][3]', refs)).toEqual([
      { kind: 'cite', n: 1, joined: false, followed: false },
      { kind: 'text', text: '[2]' },
      { kind: 'cite', n: 3, joined: false, followed: false },
    ])
  })

  it('沒有參考資料時原文照登', () => {
    expect(splitCitations('沒有出處[1]')).toEqual([{ kind: 'text', text: '沒有出處' }, { kind: 'text', text: '[1]' }])
    expect(splitCitations('')).toEqual([])
  })
})

describe('citedReferences', () => {
  const references = [reference(1), reference(2), reference(3)]

  it('只列回答真的引用到的', () => {
    expect(citedReferences({ references, citations: [3, 1] }).map((r) => r.n)).toEqual([1, 3])
  })

  it('沒有引用就不列（查到但沒用上的不算出處）', () => {
    expect(citedReferences({ references, citations: [] })).toEqual([])
    expect(citedReferences({ references })).toEqual([])
    expect(citedReferences({ citations: [1] })).toEqual([])
  })
})

describe('referenceByline', () => {
  it('有醫學審稿就顯示審稿，不顯示作者', () => {
    const r = reference(1, {
      source_name: 'Hello醫師', author: '王小明', reviewers: { 撰文: '王小明', 醫學審稿: '陳醫師' }, published_at: '2026-03-05T08:00:00',
    })
    expect(referenceByline(r)).toBe('Hello醫師　醫學審稿：陳醫師　2026/3/5')
  })

  it('沒有審稿時顯示作者；都沒有就只有站名', () => {
    expect(referenceByline(reference(1, { author: '林營養師' }))).toBe('健康醫療網　作者：林營養師')
    expect(referenceByline(reference(1, { reviewers: { 審稿: '張醫師' } }))).toBe('健康醫療網　醫學審稿：張醫師')
    expect(referenceByline(reference(1, { author: null, reviewers: null, published_at: null }))).toBe('健康醫療網')
  })

  it('看不懂的日期不顯示', () => {
    expect(formatDate('不是日期')).toBeNull()
    expect(formatDate(undefined)).toBeNull()
    expect(formatDate('2026-11-08T12:00:00')).toBe('2026/11/8')
  })
})

describe('answerBadge', () => {
  it('一般回答與打招呼沒有標籤，其餘標出類型', () => {
    expect(answerBadge(undefined)).toBeNull()
    expect(answerBadge('answer')).toBeNull()
    expect(answerBadge('greeting')).toBeNull()
    expect(answerBadge('category_answer')).toEqual({ label: '類別資訊', tone: 'info' })
    expect(answerBadge('out_of_scope')).toEqual({ label: '超出範圍', tone: 'warn' })
    expect(answerBadge('declined_medical')).toEqual({ label: '請諮詢醫師', tone: 'warn' })
    expect(answerBadge('emergency')).toEqual({ label: '緊急情況', tone: 'error' })
  })
})

describe('placeWindow', () => {
  it('放得下時視窗中線對著吉祥物', () => {
    expect(placeWindow({ anchor: 700, lane: 1440, width: 400, leftInset: 80 })).toEqual({ left: 500, tail: 200 })
  })

  it('靠左時不蓋到側欄，靠右時不超出畫面；尖角仍指著吉祥物', () => {
    expect(placeWindow({ anchor: 90, lane: 1440, width: 400, leftInset: 80 })).toEqual({ left: 92, tail: 28 })
    expect(placeWindow({ anchor: 1380, lane: 1440, width: 400, leftInset: 80 })).toEqual({ left: 1028, tail: 352 })
  })

  it('手機上視窗幾乎和畫面一樣寬：留在畫面內，尖角跟著吉祥物', () => {
    const left = placeWindow({ anchor: 40, lane: 390, width: 358.8, leftInset: 0 })
    expect(left.left).toBe(12)
    expect(left.tail).toBe(28)
    const right = placeWindow({ anchor: 350, lane: 390, width: 358.8, leftInset: 0 })
    expect(right.left).toBeCloseTo(19.2)
    expect(right.tail).toBeCloseTo(330.8)
  })

  it('比可用寬度還寬就置中', () => {
    expect(placeWindow({ anchor: 100, lane: 300, width: 290, leftInset: 0 }).left).toBe(5)
  })
})
