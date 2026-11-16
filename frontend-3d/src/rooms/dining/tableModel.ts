/**
 * 把後端的生成結果整理成「桌上有什麼」：轉盤上的器皿、桌邊的座位、小卡盤的狀態。
 * 純函式；判定規則沿用 components/nutritionVerdict（熱量 ≤110%、蛋白質 ≥90%，算不出來就是「計算不完整」）。
 */
import { toNumber } from '../../api/nutrition'
import type { MealPlanSummary, MealResult, ResultPersonNutrition, ResultPersonPortion } from '../../api/types'
import { nutritionVerdict, overallVerdict, ratioOf, type Verdict } from '../../components/nutritionVerdict'
import { retryActive } from './resultText'
import { panelSideForSeat, vesselKind, type VesselKind } from './tableLayout'

/** 桌上可以點開的東西（＝目前打開的面板）。 */
export type PanelTarget =
  | { kind: 'dish'; index: number }
  | { kind: 'staple' }
  | { kind: 'person'; memberId: string }
  | { kind: 'family' }
  | { kind: 'receipt' }
  | { kind: 'tips' }
  | { kind: 'status' }

/** 面板對應到場景裡哪一個物件（和 <Hotspot id>、物件列的 id 相同）。 */
export function targetId(target: PanelTarget): string {
  if (target.kind === 'dish') return `dish-${target.index}`
  if (target.kind === 'person') return `seat-${target.memberId}`
  return target.kind
}

/**
 * 桌機上面板開在桌子的哪一邊（DESIGN.md 5.6）：左＝營養與小知識，右＝菜、收據、這桌的狀況。
 * 個人營養預設在左邊；坐在桌子左半邊的人改開右邊，才不會蓋住他自己的座位。
 * memberIds 是桌邊座位的順序（結果的 per_person）。
 */
export function panelSide(target: PanelTarget, memberIds: readonly string[]): 'left' | 'right' {
  if (target.kind === 'family' || target.kind === 'tips') return 'left'
  if (target.kind !== 'person') return 'right'
  const index = memberIds.indexOf(target.memberId)
  return index < 0 ? 'left' : panelSideForSeat(memberIds.length, index)
}

export type Vessel = {
  /** 物件列與場景裡共用的 id */
  id: string
  name: string
  kind: VesselKind
  /** 點了要打開哪一個細節 */
  target: Extract<PanelTarget, { kind: 'dish' | 'staple' }>
  highSodium: boolean
  specified: boolean
  /** 主食算不出份量（整餐營養因此無法計算） */
  problem: boolean
}

/** 轉盤上的器皿：主食的飯桶排第一個，再來是每一道菜。 */
export function buildVessels(result: Pick<MealResult, 'dishes' | 'staple'>): Vessel[] {
  const vessels: Vessel[] = []
  if (result.staple) {
    vessels.push({
      id: 'staple', name: result.staple.label, kind: 'staple', target: { kind: 'staple' },
      highSodium: false, specified: false, problem: result.staple.display_amount == null,
    })
  }
  result.dishes.forEach((dish, index) => {
    vessels.push({
      id: `dish-${index}`, name: dish.dish_name, target: { kind: 'dish', index },
      kind: vesselKind({ name: dish.dish_name, cookingMethod: dish.cooking_method, ingredients: dish.ingredients.map((i) => i.name) }),
      highSodium: Boolean(dish.high_sodium && dish.sodium_mg_per_serving != null),
      specified: dish.specified, problem: false,
    })
  })
  return vessels
}

type Daily = ResultPersonNutrition['daily']

/**
 * 一份（用餐或便當）吃到的量 ÷ 這一份的建議量（每日建議 × 這一份應占每日的比例）。
 * 任何一邊未知都回 null，不當成 0。
 */
export function portionRatios(portion: ResultPersonPortion, daily: Daily): { energy: number | null; protein: number | null } {
  const mealRatio = toNumber(portion.meal_ratio)
  const ratio = (key: 'energy_kcal' | 'protein_g') => {
    const perDay = toNumber(daily[key]?.value)
    if (mealRatio === null || perDay === null) return null
    return ratioOf(toNumber(portion.intake[key]), perDay * mealRatio)
  }
  return { energy: ratio('energy_kcal'), protein: ratio('protein_g') }
}

/** 名牌上的判定文字：通過／熱量超出／蛋白質不足／計算不完整。 */
export function verdictLabel(energy: number | null, protein: number | null): string {
  const e = nutritionVerdict('energy_kcal', energy)
  const p = nutritionVerdict('protein_g', protein)
  if (e === 'unknown' || p === 'unknown') return '計算不完整'
  if (e === 'pass' && p === 'pass') return '通過'
  return [e === 'fail' ? '熱量超出' : null, p === 'fail' ? '蛋白質不足' : null].filter(Boolean).join('・')
}

export type Seat = {
  id: string
  memberId: string
  name: string
  energy: number | null
  protein: number | null
  /** 飯碗的樣子：unknown 是空碗加虛線框，不畫飯 */
  verdict: Verdict
  verdictLabel: string
  /** 這一餐不在家吃、只帶便當 */
  bentoOnly: boolean
}

/**
 * 每位成員一個座位。只帶便當的人，飯碗看的是便當那一份。
 * 舊結果沒有每人資料時回空陣列（桌邊不擺座位，改看整餐合計）。
 */
export function buildSeats(nutrition: MealResult['nutrition']): Seat[] {
  return (nutrition.per_person ?? []).map((person) => {
    // 舊結果沒有 dining 欄：用餐與便當當時已合併計算，照舊當成在家吃
    const dining = person.dining ?? true
    const portion = dining ? person : person.bento ?? person
    const { energy, protein } = portionRatios(portion, person.daily)
    return {
      id: `seat-${person.member_id}`, memberId: person.member_id, name: person.name ?? '已刪除成員',
      energy, protein, verdict: overallVerdict(energy, protein), verdictLabel: verdictLabel(energy, protein),
      bentoOnly: !dining,
    }
  })
}

export type TipsState = {
  /** available 有卡／unavailable 暫時拿不到／retrying 重試中／not_found 這桌沒有 */
  state: 'available' | 'unavailable' | 'retrying' | 'not_found'
  /** 卡盤旁邊的字 */
  label: string
  count: number
}

/** 小卡盤的四種狀態（DESIGN.md 5.6）。 */
export function tipsState(result: Pick<MealResult, 'knowledge' | 'retries'>): TipsState {
  const count = result.knowledge.items.length
  if (result.knowledge.status === 'available') return { state: 'available', label: `營養小知識 ${count} 則`, count }
  if (result.knowledge.status === 'unavailable') {
    return retryActive(result.retries?.knowledge)
      ? { state: 'retrying', label: '小知識重試中…', count: 0 }
      : { state: 'unavailable', label: '小知識暫時拿不到', count: 0 }
  }
  return { state: 'not_found', label: '這桌沒有小知識', count: 0 }
}

export type ResultFlags = {
  /** 已採用：只能看，不能再決定、重試或採用 */
  confirmed: boolean
  /**
   * 這一版已經被取代時，最新的是第幾版；不是舊版（或問卷已採用）就是 null。
   * 舊前端只顯示、也只對最新的版本動作；後端不擋舊版本，所以這裡自己守住：
   * 不然重新查價發現超出預算之後，還能回頭採用查價前的那一版。
   */
  newerVersion: number | null
  /** 只能看：問卷已採用，或這一版已經被新的版本取代 */
  readOnly: boolean
  /** 有食材查不到價格（成本只計已知部分；不擋採用） */
  pricePending: boolean
  missingPriceNames: string[]
  priceRetrying: boolean
  knowledgeRetrying: boolean
  /** 和昨天或今天其他餐的已採用紀錄重複：不可採用 */
  dayConflict: boolean
  canRetryPrice: boolean
  canRetryKnowledge: boolean
  hasPriceDecision: boolean
}

/**
 * 結果頁各種「現在可以做什麼」的判斷（和舊前端 MealResultView 的條件相同）。
 * latestVersion 是這份問卷最新的結果版本（版本清單的第一筆）；清單還沒讀到就不傳。
 */
export function resultFlags(r: MealResult, latestVersion?: number): ResultFlags {
  const confirmed = r.request_status === 'confirmed'
  // 用「比較大」而不是「不一樣」：剛換到重試產生的新版本時，清單可能還是舊的
  const newerVersion = !confirmed && latestVersion !== undefined && latestVersion > r.result_version ? latestVersion : null
  const readOnly = confirmed || newerVersion !== null
  const pricePending = r.prices.budget_status === 'PRICE_INCOMPLETE'
  const missingPriceNames = r.prices.items.filter((i) => i.price_status === 'unknown').map((i) => i.name)
  return {
    confirmed, newerVersion, readOnly, pricePending, missingPriceNames,
    priceRetrying: retryActive(r.retries?.price),
    knowledgeRetrying: retryActive(r.retries?.knowledge),
    dayConflict: r.previous_day_check?.status === 'conflict',
    canRetryPrice: pricePending && missingPriceNames.length > 0 && !readOnly,
    canRetryKnowledge: r.knowledge.status === 'unavailable' && Boolean(r.knowledge.retry_available) && !readOnly,
    hasPriceDecision: r.eligibility.pending_decisions.some((d) => d.kind === 'accept_missing_price'),
  }
}

/**
 * 這份問卷的採用，和現在看的這一版是什麼關係。
 * 問卷「已採用」不等於「這一桌被採用」：同一份問卷有好幾版結果，也可以不經過生成、直接採用資料庫菜單。
 */
export type Adoption =
  /** 問卷還沒被採用 */
  | { kind: 'none' }
  /** 問卷已採用，但還不知道採用的是哪一份（紀錄讀取中、讀不到或找不到）：不猜是不是這一桌 */
  | { kind: 'unresolved' }
  /** 採用的就是現在看的這一版 */
  | { kind: 'this' }
  /** 採用的是別的結果版本 */
  | { kind: 'other'; version: number }
  /** 採用的是資料庫菜單（紀錄上沒有結果版本），不是任何一版生成結果 */
  | { kind: 'menu' }

/**
 * plan 是這份問卷的已採用紀錄；還沒讀到（或找不到）傳 undefined。
 * 紀錄的 result_version：數字＝採用的是那一版；null＝採用的是資料庫菜單——和「還沒讀到」是兩回事，不能混在一起。
 */
export function adoptionOf(
  r: Pick<MealResult, 'request_status' | 'result_version'>, plan: Pick<MealPlanSummary, 'result_version'> | undefined,
): Adoption {
  if (r.request_status !== 'confirmed') return { kind: 'none' }
  if (!plan || plan.result_version === undefined) return { kind: 'unresolved' }
  if (plan.result_version === null) return { kind: 'menu' }
  return plan.result_version === r.result_version ? { kind: 'this' } : { kind: 'other', version: plan.result_version }
}

/** 被採用的是第幾版結果；沒採用、還不知道、或採用的是資料庫菜單時是 null。 */
export function adoptedResultVersion(r: Pick<MealResult, 'result_version'>, adoption: Adoption): number | null {
  if (adoption.kind === 'this') return r.result_version
  return adoption.kind === 'other' ? adoption.version : null
}

export type StatusSummary = { tone: 'ok' | 'info' | 'warn' | 'danger'; text: string }

/**
 * 畫面上方那顆狀態按鈕的字：一眼看出這桌能不能採用。細節在「這桌的狀況」面板。
 * 只有確定被採用的就是這一版，才寫「已採用」。
 * adoption 也可以直接給「被採用的結果版本」（數字）；不給而問卷已採用時，當成還不知道是哪一份。
 */
export function statusSummary(r: MealResult, flags: ResultFlags, adoption?: Adoption | number): StatusSummary {
  const adopted = typeof adoption === 'number' ? adoptionOf(r, { result_version: adoption }) : adoption ?? adoptionOf(r, undefined)
  if (flags.confirmed) {
    if (adopted.kind === 'this') return { tone: 'ok', text: '已採用' }
    if (adopted.kind === 'other') return { tone: 'warn', text: `採用的是第 ${adopted.version} 版` }
    if (adopted.kind === 'menu') return { tone: 'warn', text: '採用的是資料庫菜單' }
    return { tone: 'info', text: '問卷已採用' }
  }
  if (flags.newerVersion !== null) return { tone: 'info', text: '舊版，只能看' }
  if (r.eligibility.pending_decisions.length > 0) return { tone: 'warn', text: '等你決定' }
  if (!r.eligibility.adoptable) return { tone: 'warn', text: '還不能採用' }
  if (flags.dayConflict) return { tone: 'danger', text: '和其他餐重複' }
  return { tone: 'ok', text: '可以採用' }
}

export type TableAction = {
  kind:
    /** 採用這桌 */
    | 'adopt'
    /** 還不能採用：打開「這桌的狀況」（有待決定的事是「去決定」，否則「看原因」） */
    | 'decide' | 'reason'
    /** 換一桌（用同一份問卷重新煮） */
    | 'regenerate'
    | 'retryPrice' | 'retryKnowledge'
    /** 這一版已經被取代：只剩「看最新的那一版」 */
    | 'latest'
    /** 採用之後：一鍵購入、回客廳、看紀錄 */
    | 'market' | 'home' | 'records'
  disabled: boolean
}

type ActionContext = {
  /** 問卷正在（重新）生成：後端這時不收採用、決定與重試，舊前端這時也不顯示結果 */
  cooking: boolean
  /** 問卷讀到了沒（換一桌要送問卷的版本） */
  requestLoaded: boolean
  /** 現在看的這一版，是剛剛在這個畫面上採用的（不是從紀錄進來看快照，也不是剛採用了別的版本） */
  adoptedHere: boolean
  /** 有缺料、也找得到已採用紀錄：可以一鍵購入 */
  canShop: boolean
}

/**
 * 底部動作列有哪些按鈕（DESIGN.md 5.6）。能不能採用只看後端的 eligibility。
 * 已採用的快照（從紀錄進來）沒有動作列，回空陣列；剛採用完則換成一鍵購入與離開的路。
 * 已經被新版本取代的舊版只能看：不能採用、決定、重試，也不從這裡換一桌，只留一條到最新版的路。
 */
export function tableActions(r: MealResult, flags: ResultFlags, context: ActionContext): TableAction[] {
  if (flags.confirmed) {
    if (!context.adoptedHere) return []
    return [
      ...(context.canShop ? [{ kind: 'market' as const, disabled: false }] : []),
      { kind: 'home', disabled: false },
      { kind: 'records', disabled: false },
    ]
  }
  if (flags.newerVersion !== null) return [{ kind: 'latest', disabled: false }]
  const { cooking } = context
  const actions: TableAction[] = []
  if (r.eligibility.adoptable) actions.push({ kind: 'adopt', disabled: flags.dayConflict || cooking })
  else actions.push({ kind: r.eligibility.pending_decisions.length > 0 ? 'decide' : 'reason', disabled: false })
  actions.push({ kind: 'regenerate', disabled: cooking || !context.requestLoaded })
  if (flags.canRetryPrice || flags.hasPriceDecision) actions.push({ kind: 'retryPrice', disabled: flags.priceRetrying || cooking })
  if (flags.canRetryKnowledge) actions.push({ kind: 'retryKnowledge', disabled: flags.knowledgeRetrying || cooking })
  return actions
}

/**
 * 這個命令（採用、決定、重試）的失敗，是不是對現在看的這一版下的。
 * 餐桌換版本時命令的狀態還留著；不比對的話，第 1 版採用失敗的訊息會跟到第 2 版去。
 */
export function failedOnVersion<V>(
  command: { isError: boolean; variables: V | undefined }, shownVersion: number | undefined, versionOf: (variables: V) => number,
): boolean {
  return command.isError && command.variables !== undefined && shownVersion !== undefined && versionOf(command.variables) === shownVersion
}
