import { describe, expect, it } from 'vitest'
import type { RetryState } from '../../api/types'
import {
  adoptedResultVersion, adoptionOf, buildSeats, buildVessels, failedOnVersion, panelSide, portionRatios, resultFlags, statusSummary,
  tableActions, targetId, tipsState, verdictLabel, type Adoption,
} from './tableModel'
import { DAILY, dish, mealResult, person } from './testData'

const retry = (status: RetryState['status']): RetryState => ({
  job_id: 'j', status, stage: null, attempts: 1, error: null, produced_result_version: null, created_at: '',
})
const kinds = (list: { kind: string }[]) => list.map((a) => a.kind)

describe('buildVessels', () => {
  it('飯桶排第一個，再來每道菜一個器皿，id 和面板對得上', () => {
    const vessels = buildVessels(mealResult())
    expect(vessels.map((v) => [v.id, v.name, v.kind])).toEqual([
      ['staple', '糙米飯', 'staple'],
      ['dish-0', '清蒸鱸魚', 'seafood'],
      ['dish-1', '蒜炒高麗菜', 'vegetable'],
      ['dish-2', '味噌豆腐湯', 'soup'],
    ])
    expect(vessels.map((v) => targetId(v.target))).toEqual(vessels.map((v) => v.id))
    expect(vessels[1].specified).toBe(true)
  })

  it('高鈉要有數值才標；沒有主食就沒有飯桶', () => {
    const vessels = buildVessels(mealResult({
      staple: null,
      dishes: [dish('滷肉', { high_sodium: true, sodium_mg_per_serving: '900' }), dish('炒蛋', { high_sodium: true, sodium_mg_per_serving: null })],
    }))
    expect(vessels.map((v) => v.id)).toEqual(['dish-0', 'dish-1'])
    expect(vessels.map((v) => v.highSodium)).toEqual([true, false])
  })

  it('主食算不出份量時標出來', () => {
    const result = mealResult()
    const [staple] = buildVessels({ ...result, staple: { ...result.staple!, display_amount: null, unresolved_reason: 'staple_not_supported' } })
    expect(staple.problem).toBe(true)
  })
})

describe('panelSide：桌機的面板開在哪一邊', () => {
  const four = ['m1', 'm2', 'm3', 'm4']

  it('營養與小知識在左邊，菜、主食、收據、這桌的狀況在右邊', () => {
    expect(panelSide({ kind: 'family' }, four)).toBe('left')
    expect(panelSide({ kind: 'tips' }, four)).toBe('left')
    expect(panelSide({ kind: 'dish', index: 0 }, four)).toBe('right')
    expect(panelSide({ kind: 'staple' }, four)).toBe('right')
    expect(panelSide({ kind: 'receipt' }, four)).toBe('right')
    expect(panelSide({ kind: 'status' }, four)).toBe('right')
  })

  it('個人營養預設在左邊；坐在桌子左半邊的人改開右邊，不蓋住他自己', () => {
    // 4 個人依序坐在下、左、上、右
    expect(four.map((memberId) => panelSide({ kind: 'person', memberId }, four))).toEqual(['left', 'right', 'left', 'left'])
  })

  it('找不到這個人（資料換了）就用預設的左邊', () => {
    expect(panelSide({ kind: 'person', memberId: 'gone' }, four)).toBe('left')
    expect(panelSide({ kind: 'person', memberId: 'm1' }, [])).toBe('left')
  })
})

describe('portionRatios', () => {
  it('吃到的量 ÷（每日建議 × 這一份該占的比例）', () => {
    const { energy, protein } = portionRatios(person('m1', '阿明'), DAILY)
    // 2000 × 0.4 = 800 kcal；60 × 0.4 = 24 g
    expect(energy).toBeCloseTo(0.95, 6)
    expect(protein).toBeCloseTo(26 / 24, 6)
  })

  it('任何一邊未知都是 null，不當成 0', () => {
    expect(portionRatios(person('m', 'x', { intake: { energy_kcal: null, protein_g: '20' } }), DAILY).energy).toBeNull()
    expect(portionRatios(person('m', 'x'), {}).energy).toBeNull()
    expect(portionRatios(person('m', 'x', { meal_ratio: '0' }), DAILY)).toEqual({ energy: null, protein: null })
    expect(portionRatios(person('m', 'x', { intake: {} }), DAILY)).toEqual({ energy: null, protein: null })
  })
})

describe('verdictLabel', () => {
  it('熱量 ≤110%、蛋白質 ≥90% 才通過；兩項都寫出來', () => {
    expect(verdictLabel(1.1, 0.9)).toBe('通過')
    expect(verdictLabel(0.5, 2)).toBe('通過')
    expect(verdictLabel(1.11, 0.95)).toBe('熱量超出')
    expect(verdictLabel(1, 0.89)).toBe('蛋白質不足')
    expect(verdictLabel(1.2, 0.5)).toBe('熱量超出・蛋白質不足')
  })

  it('有一項算不出來就是計算不完整，就算另一項已經超出', () => {
    expect(verdictLabel(null, 1)).toBe('計算不完整')
    expect(verdictLabel(1.5, null)).toBe('計算不完整')
    expect(verdictLabel(null, null)).toBe('計算不完整')
  })
})

describe('buildSeats', () => {
  const seats = buildSeats(mealResult().nutrition)

  it('每位成員一個座位，飯碗是他的判定', () => {
    expect(seats.map((s) => [s.id, s.name, s.verdict, s.verdictLabel])).toEqual([
      ['seat-m1', '阿明', 'pass', '通過'],
      ['seat-m2', '小惠', 'fail', '蛋白質不足'],
      ['seat-m3', '小安', 'fail', '熱量超出'],
      ['seat-m4', '阿嬤', 'unknown', '計算不完整'],
    ])
  })

  it('算不出來的人比例是 null（空碗），不是 0', () => {
    expect(seats[3].energy).toBeNull()
    expect(seats[3].protein).not.toBeNull()
  })

  it('只帶便當的人看便當那一份', () => {
    expect(seats[1].bentoOnly).toBe(true)
    // 2000 × 0.3 = 600 kcal、60 × 0.3 = 18 g
    expect(seats[1].energy).toBeCloseTo(590 / 600, 6)
    expect(seats[1].protein).toBeCloseTo(15 / 18, 6)
    expect(seats[0].bentoOnly).toBe(false)
  })

  it('舊結果沒有 dining 欄當成在家吃；成員被刪掉仍有座位；沒有每人資料就沒有座位', () => {
    const nutrition = mealResult().nutrition
    const legacy = buildSeats({ ...nutrition, per_person: [person('m9', null, { dining: undefined })] })
    expect(legacy).toHaveLength(1)
    expect(legacy[0]).toMatchObject({ name: '已刪除成員', bentoOnly: false, verdict: 'pass' })
    expect(buildSeats({ ...nutrition, per_person: undefined })).toEqual([])
    expect(buildSeats({ ...nutrition, per_person: [] })).toEqual([])
  })
})

describe('tipsState：小卡盤的四種狀態', () => {
  const knowledge = mealResult().knowledge

  it('有卡：標則數', () => {
    expect(tipsState(mealResult())).toEqual({ state: 'available', label: '營養小知識 2 則', count: 2 })
  })

  it('暫時拿不到', () => {
    const r = mealResult({ knowledge: { status: 'unavailable', retry_available: true, items: [] } })
    expect(tipsState(r)).toEqual({ state: 'unavailable', label: '小知識暫時拿不到', count: 0 })
  })

  it('重試中', () => {
    const r = mealResult({ knowledge: { status: 'unavailable', retry_available: true, items: [] }, retries: { price: null, knowledge: retry('running') } })
    expect(tipsState(r)).toEqual({ state: 'retrying', label: '小知識重試中…', count: 0 })
  })

  it('這桌沒有', () => {
    expect(tipsState(mealResult({ knowledge: { ...knowledge, status: 'not_found', items: [] } })))
      .toEqual({ state: 'not_found', label: '這桌沒有小知識', count: 0 })
  })

  it('重試失敗後回到暫時拿不到', () => {
    const r = mealResult({ knowledge: { status: 'unavailable', items: [] }, retries: { price: null, knowledge: retry('failed') } })
    expect(tipsState(r).state).toBe('unavailable')
  })
})

describe('resultFlags', () => {
  it('缺價：列出查不到的食材，可以重新查價', () => {
    const flags = resultFlags(mealResult())
    expect(flags).toMatchObject({
      confirmed: false, pricePending: true, missingPriceNames: ['鱸魚片'], canRetryPrice: true, canRetryKnowledge: false,
      priceRetrying: false, knowledgeRetrying: false, dayConflict: false, hasPriceDecision: false,
    })
  })

  it('已採用：不能再重試', () => {
    const flags = resultFlags(mealResult({ request_status: 'confirmed', knowledge: { status: 'unavailable', retry_available: true, items: [] } }))
    expect(flags).toMatchObject({ confirmed: true, canRetryPrice: false, canRetryKnowledge: false })
  })

  it('小知識要後端說可以重試才給重試', () => {
    expect(resultFlags(mealResult({ knowledge: { status: 'unavailable', retry_available: true, items: [] } })).canRetryKnowledge).toBe(true)
    expect(resultFlags(mealResult({ knowledge: { status: 'unavailable', items: [] } })).canRetryKnowledge).toBe(false)
    expect(resultFlags(mealResult({ knowledge: { status: 'not_found', retry_available: true, items: [] } })).canRetryKnowledge).toBe(false)
  })

  it('重試中、昨日衝突、待決定的缺價', () => {
    const r = mealResult({
      retries: { price: retry('queued'), knowledge: retry('running') },
      previous_day_check: { status: 'conflict', history_changed: true, conflicting_dishes: ['滷肉'], message: '重複' },
    })
    r.eligibility.pending_decisions = [{ kind: 'accept_missing_price', message: '缺價' }]
    expect(resultFlags(r)).toMatchObject({ priceRetrying: true, knowledgeRetrying: true, dayConflict: true, hasPriceDecision: true })
  })

  it('最新的那一版（或清單還沒讀到）不算舊版', () => {
    expect(resultFlags(mealResult())).toMatchObject({ newerVersion: null, readOnly: false })
    expect(resultFlags(mealResult(), 2)).toMatchObject({ newerVersion: null, readOnly: false, canRetryPrice: true })
    // 剛換到重試產生的新版本、清單還是舊的：不能把新版本當成舊版
    expect(resultFlags(mealResult(), 1)).toMatchObject({ newerVersion: null, readOnly: false })
  })

  it('已經有比較新的版本：這一版只能看，不能重新查價或重試小知識', () => {
    const r = mealResult({ knowledge: { status: 'unavailable', retry_available: true, items: [] } })
    expect(resultFlags(r, 3)).toMatchObject({ newerVersion: 3, readOnly: true, confirmed: false, canRetryPrice: false, canRetryKnowledge: false })
  })

  it('已採用的問卷不再提醒有新版本，但一樣只能看', () => {
    expect(resultFlags(mealResult({ request_status: 'confirmed' }), 3)).toMatchObject({ newerVersion: null, readOnly: true, confirmed: true })
  })
})

describe('adoptionOf：問卷的採用和現在看的這一版的關係', () => {
  const confirmed = { request_status: 'confirmed' as const, result_version: 2 }

  it('問卷還沒採用', () => {
    expect(adoptionOf({ request_status: 'result_ready', result_version: 2 }, { result_version: 2 })).toEqual({ kind: 'none' })
  })

  it('採用的就是這一版／是別的版本', () => {
    expect(adoptionOf(confirmed, { result_version: 2 })).toEqual({ kind: 'this' })
    expect(adoptionOf(confirmed, { result_version: 1 })).toEqual({ kind: 'other', version: 1 })
  })

  it('紀錄上沒有結果版本＝採用的是資料庫菜單，不是這一桌', () => {
    expect(adoptionOf(confirmed, { result_version: null })).toEqual({ kind: 'menu' })
  })

  it('紀錄還沒讀到（或找不到）：不猜', () => {
    expect(adoptionOf(confirmed, undefined)).toEqual({ kind: 'unresolved' })
    expect(adoptionOf(confirmed, {})).toEqual({ kind: 'unresolved' })
  })

  it('adoptedResultVersion：只有採用的是某一版結果時才有數字', () => {
    const r = { result_version: 2 }
    expect(adoptedResultVersion(r, { kind: 'this' })).toBe(2)
    expect(adoptedResultVersion(r, { kind: 'other', version: 1 })).toBe(1)
    for (const kind of ['none', 'unresolved', 'menu'] as const) expect(adoptedResultVersion(r, { kind })).toBeNull()
  })
})

describe('statusSummary', () => {
  const summary = (r = mealResult(), adoption?: Adoption | number, latestVersion?: number) =>
    statusSummary(r, resultFlags(r, latestVersion), adoption)

  it('可以採用', () => {
    expect(summary()).toEqual({ tone: 'ok', text: '可以採用' })
  })

  it('有待決定的事優先於還不能採用', () => {
    const r = mealResult()
    r.eligibility = { ...r.eligibility, adoptable: false, pending_decisions: [{ kind: 'continue_missing_nutrition', message: '缺營養' }] }
    expect(summary(r)).toEqual({ tone: 'warn', text: '等你決定' })
    r.eligibility.pending_decisions = []
    expect(summary(r)).toEqual({ tone: 'warn', text: '還不能採用' })
  })

  it('和其他餐重複', () => {
    expect(summary(mealResult({ previous_day_check: { status: 'conflict', history_changed: true } })).text).toBe('和其他餐重複')
  })

  it('已經被新版本取代：就算後端說可以採用，也只寫舊版', () => {
    expect(summary(mealResult(), undefined, 3)).toEqual({ tone: 'info', text: '舊版，只能看' })
  })

  it('只有確定採用的就是這一版才寫已採用', () => {
    const confirmed = mealResult({ request_status: 'confirmed' })
    expect(summary(confirmed, { kind: 'this' })).toEqual({ tone: 'ok', text: '已採用' })
    expect(summary(confirmed, 2)).toEqual({ tone: 'ok', text: '已採用' })
    expect(summary(confirmed, { kind: 'other', version: 1 })).toEqual({ tone: 'warn', text: '採用的是第 1 版' })
    expect(summary(confirmed, 1)).toEqual({ tone: 'warn', text: '採用的是第 1 版' })
  })

  it('採用的是資料庫菜單、或還不知道採用的是哪一份：不說這一桌已採用', () => {
    const confirmed = mealResult({ request_status: 'confirmed' })
    expect(summary(confirmed, { kind: 'menu' })).toEqual({ tone: 'warn', text: '採用的是資料庫菜單' })
    expect(summary(confirmed, { kind: 'unresolved' })).toEqual({ tone: 'info', text: '問卷已採用' })
    expect(summary(confirmed)).toEqual({ tone: 'info', text: '問卷已採用' })
  })
})

describe('tableActions：底部動作列', () => {
  const idle = { cooking: false, requestLoaded: true, adoptedHere: false, canShop: false }
  const actions = (r = mealResult(), context = idle, latestVersion?: number) => tableActions(r, resultFlags(r, latestVersion), context)

  it('可以採用、缺一項價格：採用、換一桌、重新查價', () => {
    expect(actions()).toEqual([
      { kind: 'adopt', disabled: false }, { kind: 'regenerate', disabled: false }, { kind: 'retryPrice', disabled: false },
    ])
  })

  it('和其他餐重複時採用鍵停用', () => {
    expect(actions(mealResult({ previous_day_check: { status: 'conflict', history_changed: true } }))[0]).toEqual({ kind: 'adopt', disabled: true })
  })

  it('後端說不能採用就沒有採用鍵：有待決定的是「去決定」，否則「看原因」', () => {
    const r = mealResult()
    r.eligibility = { ...r.eligibility, adoptable: false, blocks: [{ code: 'BUDGET_EXCEEDED', message: '超出預算' }] }
    expect(kinds(actions(r))).toEqual(['reason', 'regenerate', 'retryPrice'])
    r.eligibility.pending_decisions = [{ kind: 'continue_missing_nutrition', message: '缺營養' }]
    expect(kinds(actions(r))).toEqual(['decide', 'regenerate', 'retryPrice'])
  })

  it('價格完整就沒有重新查價；有缺價待決定時仍然有', () => {
    const complete = mealResult()
    complete.prices = { ...complete.prices, budget_status: 'OK', items: [complete.prices.items[0]] }
    expect(kinds(actions(complete))).toEqual(['adopt', 'regenerate'])
    complete.eligibility.pending_decisions = [{ kind: 'accept_missing_price', message: '缺價' }]
    expect(kinds(actions(complete))).toContain('retryPrice')
  })

  it('小知識可以重試時多一顆；重試中停用', () => {
    const r = mealResult({ knowledge: { status: 'unavailable', retry_available: true, items: [] } })
    expect(actions(r).at(-1)).toEqual({ kind: 'retryKnowledge', disabled: false })
    r.retries = { price: retry('running'), knowledge: retry('queued') }
    expect(actions(r).filter((a) => a.disabled).map((a) => a.kind)).toEqual(['retryPrice', 'retryKnowledge'])
  })

  it('問卷正在重新煮：全部停用', () => {
    const r = mealResult({ knowledge: { status: 'unavailable', retry_available: true, items: [] } })
    expect(actions(r, { ...idle, cooking: true }).every((a) => a.disabled)).toBe(true)
  })

  it('問卷還沒讀到：不能換一桌', () => {
    expect(actions(mealResult(), { ...idle, requestLoaded: false })[1]).toEqual({ kind: 'regenerate', disabled: true })
  })

  it('已採用的快照（從紀錄進來）沒有動作列', () => {
    expect(actions(mealResult({ request_status: 'confirmed' }), { ...idle, canShop: true })).toEqual([])
  })

  it('已經被新版本取代的舊版：不能採用、決定、重試或換一桌，只剩到最新版的路', () => {
    const r = mealResult({ knowledge: { status: 'unavailable', retry_available: true, items: [] } })
    r.eligibility.pending_decisions = [{ kind: 'accept_missing_price', message: '缺價' }]
    expect(actions(r, idle, 3)).toEqual([{ kind: 'latest', disabled: false }])
    // 同一版就是最新的：照常
    expect(kinds(actions(mealResult(), idle, 2))).toEqual(['adopt', 'regenerate', 'retryPrice'])
  })

  it('剛採用完：一鍵購入（有缺料才有）與離開的路', () => {
    const confirmed = mealResult({ request_status: 'confirmed' })
    expect(kinds(actions(confirmed, { ...idle, adoptedHere: true, canShop: true }))).toEqual(['market', 'home', 'records'])
    expect(kinds(actions(confirmed, { ...idle, adoptedHere: true }))).toEqual(['home', 'records'])
  })
})

describe('failedOnVersion：命令的失敗只算在下命令的那一版', () => {
  const versionOf = (v: { result_version: number }) => v.result_version

  it('對現在看的這一版下的命令失敗才算', () => {
    expect(failedOnVersion({ isError: true, variables: { result_version: 2 } }, 2, versionOf)).toBe(true)
    expect(failedOnVersion({ isError: true, variables: { result_version: 1 } }, 2, versionOf)).toBe(false)
  })

  it('沒有失敗、還沒下過命令、桌上還沒有結果都不算', () => {
    expect(failedOnVersion({ isError: false, variables: { result_version: 2 } }, 2, versionOf)).toBe(false)
    expect(failedOnVersion({ isError: true, variables: undefined }, 2, versionOf)).toBe(false)
    expect(failedOnVersion({ isError: true, variables: { result_version: 2 } }, undefined, versionOf)).toBe(false)
  })
})
